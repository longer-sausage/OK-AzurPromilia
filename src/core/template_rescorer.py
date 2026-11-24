"""全模板打分器：把**同一帧**喂给全部模板，按匹配得分排序。

用途是「失败帧复测」：任务失败时留下的那一帧，一次性跑完全部模板，
就能一眼看出是哪个模板失效了 —— 是差一点点（0.7x）还是完全对不上（0.2x）。

几个必须遵守的细节（否则分数不可比）：

1. **必须显式传全屏 box**。``find_one_feature`` 不传 box 时只在 coco 标注位置
   ±variance 的窗口内搜索（本项目 0.002，约 2px），分数只反映「那个小窗口里的最佳位置」，
   和全屏搜索的分数没有可比性。
2. **必须把 threshold 压到 -1**。框架里 ``threshold == 0`` 会被替换成
   ``default_threshold``（0.8），低于阈值的模板直接不返回，拿不到分数。
   ``TM_CCOEFF_NORMED`` 的取值范围是 [-1, 1]，所以 -1 能拿到原始 max 值。
3. **必须按当前分辨率解析模板名**。分辨率相关的模板由 ``get_feature_by_resolution``
   映射，直接拿原始名字去匹配可能尺寸对不上。

这个模块刻意不依赖 ``BaseTask``：只要对象提供 ``find_one_feature`` 就能用，
测试里可以直接塞一个假 matcher。
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from ok import Box

from src.data.feature_list import FeatureList

# 压到 TM_CCOEFF_NORMED 的下界，保证任何模板都能拿到原始得分
RAW_SCORE_THRESHOLD = -1.0
DEFAULT_UI_THRESHOLD = 0.8

FEATURE_VALUES = [member.value for member in FeatureList]


def resolution_resolver(width: int):
    """按画面宽度选最合适的分辨率后缀（与 ``RuntimeMixin.get_feature_by_resolution`` 同规则）。

    规则：2K/4K 画面优先用带后缀的模板，1080P 用无后缀的；找不到就退回原名。
    """
    if width >= 3800:
        suffixes = ("_4k", "_2k", "")
    elif width >= 2500:
        suffixes = ("_2k", "_4k", "")
    else:
        suffixes = ("", "_2k", "_4k")
    available = set(FEATURE_VALUES)

    def resolve(base_name: str) -> str:
        for suffix in suffixes:
            candidate = f"{base_name}{suffix}"
            if candidate in available:
                return candidate
        return base_name

    return resolve


@dataclass(frozen=True)
class TemplateScore:
    """单个模板的复测结果。"""

    name: str
    resolved_name: str
    score: float
    box: Box | None = None
    threshold: float = DEFAULT_UI_THRESHOLD

    @property
    def hit(self) -> bool:
        return self.score >= self.threshold

    @property
    def gap(self) -> float:
        """离阈值还差多少（命中时为 0 或负数）。"""
        return self.threshold - self.score

    def verdict(self) -> str:
        if self.hit:
            return "命中"
        if self.gap <= 0.08:
            return "差一点"
        if self.gap <= 0.25:
            return "偏弱"
        return "不对"

    def center(self) -> tuple[int, int] | None:
        if self.box is None:
            return None
        return self.box.center()


@dataclass
class RescoreResult:
    """一次复测的完整结果。"""

    scores: list[TemplateScore] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    threshold: float = DEFAULT_UI_THRESHOLD
    frame_size: tuple[int, int] = (0, 0)
    elapsed: float = 0.0

    @property
    def hits(self) -> list[TemplateScore]:
        return [item for item in self.scores if item.hit]

    @property
    def near_misses(self) -> list[TemplateScore]:
        return [item for item in self.scores if not item.hit and item.gap <= 0.08]

    def top(self, count: int = 10) -> list[TemplateScore]:
        return self.scores[:count]

    def best(self) -> TemplateScore | None:
        return self.scores[0] if self.scores else None

    def summary(self) -> str:
        if not self.scores:
            return "没有可复测的模板"
        best = self.scores[0]
        return (
            f"共 {len(self.scores)} 个模板，命中 {len(self.hits)} 个；"
            f"最高分 {best.name} {best.score:.3f}（{best.verdict()}），"
            f"耗时 {self.elapsed * 1000:.0f}ms"
        )


class TemplateRescorer:
    """把一帧画面交给全部模板打分。"""

    def __init__(
        self,
        matcher: Any,
        names: Sequence[str] | None = None,
        threshold: float = DEFAULT_UI_THRESHOLD,
        resolver: Callable[[str], str] | None = None,
        match_method: int = cv2.TM_CCOEFF_NORMED,
        use_gray_scale: bool = False,
        logger: Callable[[str], None] | None = None,
    ):
        self.matcher = matcher
        self._names = list(names) if names is not None else None
        self.threshold = float(threshold or DEFAULT_UI_THRESHOLD)
        self.resolver = resolver
        self.match_method = match_method
        self.use_gray_scale = use_gray_scale
        self._log = logger

    # ── 构造 ──────────────────────────────────────────────
    @classmethod
    def from_task(cls, task, names: Sequence[str] | None = None) -> TemplateRescorer:
        """用任务的 FeatureSet 与分辨率映射构造打分器。"""
        feature_set = task.executor.feature_set
        resolver = getattr(task, "get_feature_by_resolution", None)
        threshold = DEFAULT_UI_THRESHOLD
        try:
            from src.config import config as app_config

            threshold = float(
                app_config.get("template_matching", {}).get("default_threshold", DEFAULT_UI_THRESHOLD)
            )
        except Exception:
            pass
        return cls(
            matcher=feature_set,
            names=names,
            threshold=threshold,
            resolver=resolver if callable(resolver) else None,
            logger=getattr(task, "log_info", None),
        )

    @classmethod
    def from_feature_set(
        cls,
        feature_set: Any,
        width: int = 1920,
        names: Sequence[str] | None = None,
        threshold: float = DEFAULT_UI_THRESHOLD,
    ) -> TemplateRescorer:
        """只给 FeatureSet（GUI 场景没有任务实例）时构造打分器。"""
        return cls(
            matcher=feature_set,
            names=names,
            threshold=threshold,
            resolver=resolution_resolver(int(width or 1920)),
        )

    # ── 模板清单 ──────────────────────────────────────────
    def names(self) -> list[str]:
        if self._names is not None:
            return list(self._names)
        try:
            from src.data.feature_list import FeatureList

            return [member.value for member in FeatureList]
        except Exception as exc:
            self._warn(f"无法枚举模板清单: {exc}")
            return []

    def resolve(self, name: str) -> str:
        if not self.resolver:
            return name
        try:
            resolved = self.resolver(name)
        except Exception:
            return name
        return str(resolved or name)

    # ── 打分 ──────────────────────────────────────────────
    def rescore(
        self,
        frame: np.ndarray,
        names: Iterable[str] | None = None,
        threshold: float | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> RescoreResult:
        """对 ``frame`` 跑完全部模板，返回按得分降序的结果。"""
        result = RescoreResult(threshold=float(threshold if threshold is not None else self.threshold))
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return result

        height, width = frame.shape[:2]
        result.frame_size = (int(width), int(height))
        full_box = Box(0, 0, int(width), int(height))

        candidates = list(names) if names is not None else self.names()
        started = time.perf_counter()
        seen: set[str] = set()

        for index, name in enumerate(candidates):
            resolved = self.resolve(name)
            if resolved in seen:
                continue
            seen.add(resolved)
            score = self._score_one(frame, name, resolved, full_box, result.threshold)
            if score is None:
                result.missing.append(name)
            else:
                result.scores.append(score)
            if progress is not None:
                progress(index + 1, len(candidates), name)

        result.scores.sort(key=lambda item: item.score, reverse=True)
        result.elapsed = time.perf_counter() - started
        return result

    def _score_one(
        self, frame: np.ndarray, name: str, resolved: str, full_box: Box, threshold: float
    ) -> TemplateScore | None:
        try:
            boxes = self.matcher.find_one_feature(
                frame,
                resolved,
                box=full_box,
                threshold=RAW_SCORE_THRESHOLD,
                match_method=self.match_method,
                use_gray_scale=self.use_gray_scale,
                limit=1,
            )
        except Exception as exc:
            self._warn(f"模板 {name} 打分失败: {exc}")
            return None

        if not boxes:
            return None
        best = boxes[0]
        try:
            score_value = float(best.confidence)
        except (TypeError, ValueError):
            return None
        return TemplateScore(name=name, resolved_name=resolved, score=score_value, box=best, threshold=threshold)

    def _warn(self, message: str) -> None:
        if self._log:
            with contextlib.suppress(Exception):
                self._log(message)
