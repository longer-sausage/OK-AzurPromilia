"""模板库：加载、按分辨率缩放、预处理、匹配与 probe 得分。

文件名约定与 / ok-script 一致：
``<名字>_<基准宽>x<基准高>_<锚点x>_<锚点y>.png``
——这样同一批 PNG 既能被本库使用，也能直接被 ok-script 的 FeatureSet 读取。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from src.combat.recognition.filters import to_gray
from src.combat.recognition.regions import Region, clamp_box, scale_anchor

_NAME_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9_]+)_(?P<w>\d+)x(?P<h>\d+)_(?P<x>\d+)_(?P<y>\d+)$")


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """一个模板：图像 + 基准分辨率 + 锚点 + 预处理。"""

    name: str
    image: np.ndarray
    base_width: int
    base_height: int
    anchor: tuple[int, int]
    preprocess: Callable[[np.ndarray], np.ndarray] | None = None


@dataclass(frozen=True, slots=True)
class Match:
    """一次模板匹配结果（绝对像素坐标）。"""

    name: str
    box: tuple[int, int, int, int]
    score: float


class TemplateLibrary:
    """内存模板库；按分辨率缓存缩放后的模板。"""

    def __init__(self) -> None:
        self._templates: dict[str, TemplateSpec] = {}
        self._scaled: dict[tuple[str, int, int], tuple[np.ndarray, float]] = {}

    # ── 注册 ──────────────────────────────────────────────

    def register(self, spec: TemplateSpec) -> None:
        self._templates[spec.name] = spec
        self._scaled = {key: value for key, value in self._scaled.items() if key[0] != spec.name}

    def load_folder(self, folder, preprocessors: dict[str, Callable] | None = None) -> int:
        """从目录加载 ``<名>_<w>x<h>_<x>_<y>.png``，返回加载数量。"""
        directory = Path(folder)
        if not directory.is_dir():
            return 0
        loaded = 0
        for path in sorted(directory.glob("*.png")):
            match = _NAME_PATTERN.match(path.stem)
            if match is None:
                continue
            image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                continue
            name = match["name"]
            preprocess = (preprocessors or {}).get(name)
            self.register(
                TemplateSpec(
                    name=name,
                    image=image,
                    base_width=int(match["w"]),
                    base_height=int(match["h"]),
                    anchor=(int(match["x"]), int(match["y"])),
                    preprocess=preprocess,
                )
            )
            loaded += 1
        return loaded

    def names(self) -> list[str]:
        return sorted(self._templates)

    def get(self, name: str) -> TemplateSpec | None:
        return self._templates.get(name)

    def __len__(self) -> int:
        return len(self._templates)

    def __contains__(self, name: object) -> bool:
        return name in self._templates

    # ── 匹配 ──────────────────────────────────────────────

    def match(
        self,
        frame,
        name: str,
        *,
        region: Region | tuple[float, float, float, float] | None = None,
        threshold: float = 0.8,
        variance: int = 8,
        use_gray: bool = False,
    ) -> Match | None:
        found, _ = self.probe(
            frame,
            name,
            region=region,
            threshold=threshold,
            variance=variance,
            use_gray=use_gray,
        )
        return found

    def probe(
        self,
        frame,
        name: str,
        *,
        region: Region | tuple[float, float, float, float] | None = None,
        threshold: float = 0.8,
        variance: int = 8,
        use_gray: bool = False,
    ) -> tuple[Match | None, float]:
        """返回 (命中或 None, 最高分)。最高分即使没过阈值也返回，方便校准。"""
        spec = self._templates.get(name)
        if spec is None or frame is None or frame.size == 0:
            return None, 0.0
        height, width = frame.shape[:2]
        template, _scale = self._scaled_template(spec, width, height)

        x1, y1, search = self._search_area(frame, spec, region, variance)
        if search.size == 0 or template.shape[0] > search.shape[0] or template.shape[1] > search.shape[1]:
            return None, 0.0

        template_img = template
        search_img = search
        if spec.preprocess is not None:
            template_img = spec.preprocess(template_img)
            search_img = spec.preprocess(search_img)
        if use_gray or template_img.ndim != search_img.ndim:
            template_img = to_gray(template_img)
            search_img = to_gray(search_img)

        result = cv2.matchTemplate(search_img, template_img, cv2.TM_CCOEFF_NORMED)
        np.nan_to_num(result, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        tw, th = template_img.shape[1], template_img.shape[0]
        box = clamp_box(x1 + max_loc[0], y1 + max_loc[1], tw, th, width, height)
        match = Match(name=name, box=box, score=round(float(max_val), 4))
        if max_val >= threshold:
            return match, match.score
        return None, match.score

    # ── 内部 ──────────────────────────────────────────────

    def _scaled_template(self, spec: TemplateSpec, width: int, height: int) -> tuple[np.ndarray, float]:
        key = (spec.name, width, height)
        cached = self._scaled.get(key)
        if cached is not None:
            return cached
        scale = min(width / max(1, spec.base_width), height / max(1, spec.base_height))
        image = cv2.resize(
            spec.image,
            (max(1, round(spec.image.shape[1] * scale)), max(1, round(spec.image.shape[0] * scale))),
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
        )
        self._scaled[key] = (image, scale)
        return image, scale

    def _search_area(self, frame, spec: TemplateSpec, region, variance: int):
        height, width = frame.shape[:2]
        if region is not None:
            if isinstance(region, Region):
                x1, y1, x2, y2 = region.pixels(width, height)
            else:
                x1, y1, x2, y2 = Region(*region).pixels(width, height)
            return x1, y1, frame[y1:y2, x1:x2]

        template, _scale = self._scaled_template(spec, width, height)
        anchor_x, anchor_y = scale_anchor(
            spec.anchor[0], spec.anchor[1], spec.base_width, spec.base_height, width, height
        )
        pad_x = int(template.shape[1] * 0.5) + variance
        pad_y = int(template.shape[0] * 0.5) + variance
        x1 = max(0, anchor_x - pad_x)
        y1 = max(0, anchor_y - pad_y)
        x2 = min(width, anchor_x + template.shape[1] + pad_x)
        y2 = min(height, anchor_y + template.shape[0] + pad_y)
        return x1, y1, frame[y1:y2, x1:x2]
