"""技能按钮读数：外圈蓝环的角度覆盖率（整圈=就绪，弧=冷却）。

实测口径（11 张 1920×1080 截图 + 玩家确认）：
- 就绪：按钮外圈是一整圈**蓝/青色亮环**；
- 冷却：只剩顶部一小段进度弧，不围成圈。

环由两种颜色组成：冷色（青/蓝，高饱和高亮）+ 高光（近白），
单用其中一种会漏掉半圈，所以两种都收。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.combat.recognition.regions import Region


@dataclass(frozen=True, slots=True)
class SkillReading:
    """一个技能槽的读数。"""

    slot: str
    ready_ratio: float
    ready: bool
    cooldown_text: str | None = None


def ring_coverage(
    roi: np.ndarray,
    *,
    band: tuple[float, float] = (0.40, 0.50),
    size: int = 80,
    bins: int = 72,
    white_saturation_max: int = 100,
    white_value_min: int = 190,
    cyan_hue: tuple[int, int] = (80, 135),
    cyan_saturation_min: int = 100,
    cyan_value_min: int = 200,
) -> float:
    """按钮外圈亮环的角度覆盖率（0~1）。"""
    if roi is None or roi.size == 0:
        return 0.0
    roi = cv2.resize(roi, (size, size), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    hue = hsv[:, :, 0]
    white = (hsv[:, :, 1] <= white_saturation_max) & (hsv[:, :, 2] >= white_value_min)
    cyan = (
        (hue >= cyan_hue[0])
        & (hue <= cyan_hue[1])
        & (hsv[:, :, 1] >= cyan_saturation_min)
        & (hsv[:, :, 2] >= cyan_value_min)
    )
    mask = white | cyan

    yy, xx = np.mgrid[:size, :size]
    center = size / 2 - 0.5
    radius = np.sqrt((xx - center) ** 2 + (yy - center) ** 2)
    annulus = (radius >= size * band[0]) & (radius <= size * band[1])
    angle = (np.degrees(np.arctan2(yy - center, xx - center)) + 360) % 360

    covered = {int(angle[y, x] // (360 / bins)) for y, x in zip(*np.nonzero(mask & annulus), strict=False)}
    return round(len(covered) / bins, 4)


def read_skill(
    frame,
    slot: str,
    region,
    *,
    band: tuple[float, float] = (0.40, 0.50),
    threshold: float = 0.5,
    white_saturation_max: int = 100,
    white_value_min: int = 190,
    cyan_hue: tuple[int, int] = (80, 135),
    cyan_saturation_min: int = 100,
    cyan_value_min: int = 200,
    ocr=None,
) -> SkillReading | None:
    """读一个技能槽；区域未校准时返回 None。"""
    if frame is None or frame.size == 0 or region is None:
        return None
    height, width = frame.shape[:2]
    if isinstance(region, Region):
        x1, y1, x2, y2 = region.pixels(width, height)
    else:
        x1, y1, x2, y2 = Region(*region).pixels(width, height)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    coverage = ring_coverage(
        roi,
        band=band,
        white_saturation_max=white_saturation_max,
        white_value_min=white_value_min,
        cyan_hue=cyan_hue,
        cyan_saturation_min=cyan_saturation_min,
        cyan_value_min=cyan_value_min,
    )
    cooldown_text = None
    if ocr is not None and coverage < threshold:
        try:
            cooldown_text = ocr(roi)
        except Exception:
            cooldown_text = None
    return SkillReading(
        slot=slot,
        ready_ratio=coverage,
        ready=coverage >= threshold,
        cooldown_text=cooldown_text,
    )
