"""队伍槽位与当前角色识别：找「淡色编号徽章」。

实机校准（swap_before=1号、swap_after=2号、idle=1号，玩家确认）：
- 当前角色的编号徽章是**淡色**（饱和度低、亮度高）；
- 其余角色是深蓝底 + 亮白数字（饱和度高）。
判据：`score = V - k*S`，取最大且领先第二名超过阈值者。

比 「活跃色得分」更简单：这里不需要颜色模板，只需要“谁最不饱和”。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2

from src.combat.recognition.regions import Region


@dataclass(frozen=True, slots=True)
class SlotConfig:
    """当前角色识别参数。"""

    saturation_weight: float = 0.7
    accept_margin: float = 4.0


@dataclass(frozen=True, slots=True)
class SlotDetection:
    """槽位识别结果。"""

    index: int
    scores: tuple[float, ...]
    accepted: bool
    margin: float
    reason: str


def _roi(frame, region) -> cv2.Mat | None:
    height, width = frame.shape[:2]
    if isinstance(region, Region):
        x1, y1, x2, y2 = region.pixels(width, height)
    else:
        x1, y1, x2, y2 = Region(*region).pixels(width, height)
    return frame[y1:y2, x1:x2]


def badge_scores(frame, regions, config: SlotConfig) -> list[float]:
    """每个槽位徽章的“淡色分”：V − k·S，越大越像当前角色。"""
    if frame is None or frame.size == 0:
        return [0.0 for _ in regions]
    scores = []
    for region in regions:
        roi = _roi(frame, region)
        if roi is None or roi.size == 0:
            scores.append(0.0)
            continue
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).astype("float32")
        score = float(hsv[:, :, 2].mean() - config.saturation_weight * hsv[:, :, 1].mean())
        scores.append(round(score, 2))
    return scores


def detect_current_slot(frame, regions: Sequence, config: SlotConfig) -> SlotDetection:
    """按徽章淡色分判定当前角色槽位。"""
    scores = badge_scores(frame, regions, config)
    if not scores:
        return SlotDetection(index=-1, scores=(), accepted=False, margin=0.0, reason="no_slots")
    ordered = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    best_index = ordered[0]
    best = scores[best_index]
    second = scores[ordered[1]] if len(ordered) > 1 else 0.0
    margin = round(best - second, 2)
    accepted = margin >= config.accept_margin
    return SlotDetection(
        index=best_index if accepted else -1,
        scores=tuple(scores),
        accepted=accepted,
        margin=margin,
        reason="pale_badge" if accepted else "ambiguous",
    )
