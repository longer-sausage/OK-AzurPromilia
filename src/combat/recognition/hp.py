"""敌人血条：找薄横条、算填充比例、多目标时按“离画面中心最近”选一条。

实机校准要点（1920×1080）：
- 小怪与精英血条都浮在画面**中部**，不在顶部；
- 血条很薄（3~10px）、很长（100~420px），而误报的红色块通常是粗块（几十 px 高）；
- 精英战可能同时出现两条（Boss 主条 + 小怪条），取离中心最近的一条。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.combat.recognition.filters import color_mask, morphology
from src.combat.recognition.regions import Region


@dataclass(frozen=True, slots=True)
class HealthBarReading:
    """血条读数：整条外框 + 填充比例 + 置信度。"""

    box: tuple[int, int, int, int]
    fill_ratio: float
    confidence: float


def _crop_with_offset(frame, region):
    height, width = frame.shape[:2]
    if isinstance(region, Region):
        x1, y1, x2, y2 = region.pixels(width, height)
    else:
        x1, y1, x2, y2 = Region(*region).pixels(width, height)
    return frame[y1:y2, x1:x2], x1, y1


def _horizontal_runs(mask_row: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate(mask_row):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask_row) - 1))
    return runs


def _is_track_pixel(hsv_pixel, value_min: int, value_max: int, saturation_max: int) -> bool:
    value = int(hsv_pixel[2])
    return value_min <= value <= value_max and int(hsv_pixel[1]) <= saturation_max


def _pick_box(boxes, frame_width: int, frame_height: int, prefer_center: bool):
    if not boxes:
        return None
    if not prefer_center:
        return max(boxes, key=lambda box: box[2])
    center_x = frame_width / 2
    center_y = frame_height / 2

    def distance(box):
        x, y, w, h = box
        dx = x + w / 2 - center_x
        dy = y + h / 2 - center_y
        return dx * dx + dy * dy

    return min(boxes, key=distance)


def read_health_bar(
    frame,
    region,
    hsv_ranges,
    *,
    min_width_ratio: float = 0.02,
    min_width_px_ratio: float = 0.03,
    min_height_px: int = 2,
    max_height_ratio: float = 0.02,
    min_aspect: float = 6.0,
    prefer_center: bool = True,
    track_value_min: int = 20,
    track_value_max: int = 130,
    track_saturation_max: int = 70,
) -> HealthBarReading | None:
    """在区域内读血条；找不到返回 None。"""
    if frame is None or frame.size == 0:
        return None
    frame_height, frame_width = frame.shape[:2]
    roi, offset_x, offset_y = _crop_with_offset(frame, region)
    if roi.size == 0:
        return None

    red_mask = morphology(color_mask(roi, hsv_ranges))
    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    max_height = max(min_height_px, int(frame_height * max_height_ratio))
    min_width_px = int(frame_width * min_width_px_ratio)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < roi.shape[1] * min_width_ratio or w < min_width_px:
            continue
        if h < min_height_px:
            continue
        if h > max_height:  # 粗红色块（特效/UI）不是血条
            continue
        if w / max(h, 1) < min_aspect:
            continue
        # 用绝对坐标参与“离画面中心最近”的比较（roi 有偏移）
        candidates.append((offset_x + x, offset_y + y, w, h))
    chosen = _pick_box(candidates, frame_width, frame_height, prefer_center)
    if chosen is None:
        return None

    abs_x, abs_y, w, h = chosen
    x, y = abs_x - offset_x, abs_y - offset_y

    # 在候选框里找“最长的一段红”，用它做填充比例（避免细碎合并块取到 4px 的短段）
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    best_row = y + h // 2
    best_run = (x, x + max(0, w - 1))
    for row in range(y, min(roi.shape[0], y + h)):
        runs = _horizontal_runs(red_mask[row] > 0)
        run = next(((a, b) for a, b in runs if a <= x + w // 2 <= b), None)
        if run is not None and run[1] - run[0] > best_run[1] - best_run[0]:
            best_run, best_row = run, row

    left, right = best_run
    while left > 0 and _is_track_pixel(hsv[best_row, left - 1], track_value_min, track_value_max, track_saturation_max):
        left -= 1
    while right < roi.shape[1] - 1 and _is_track_pixel(
        hsv[best_row, right + 1], track_value_min, track_value_max, track_saturation_max
    ):
        right += 1

    full_width = max(1, right - left + 1)
    red_width = max(0, best_run[1] - best_run[0] + 1)
    fill_ratio = min(1.0, red_width / full_width) if full_width > red_width else 1.0
    confidence = min(1.0, full_width / max(1.0, roi.shape[1] * 0.05))
    return HealthBarReading(
        box=(offset_x + left, offset_y + y, full_width, h),
        fill_ratio=round(fill_ratio, 3),
        confidence=round(confidence, 3),
    )
