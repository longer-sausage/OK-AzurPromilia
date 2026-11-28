"""点状标记：锁定星形（模板 + 红光校验）、颜色块标记。

锁定标记的实机口径（12 张 1920×1080 截图验证）：
- 被锁定的怪身上会出现「白色四角星 + 红色柔光」；
- 白星是固定图标，但尺寸随距离在 0.8~1.2 倍之间浮动；
- 判定 = 白星模板匹配分数 ≥0.72 且 星形周围的红色柔光占比 ≥0.05。
  单看白星分数不够（未锁定画面最高 0.686），加上红光后未锁定画面全部为 0。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from src.combat.recognition.filters import color_mask, morphology
from src.combat.recognition.regions import Region

_ASSET = Path(__file__).resolve().parents[3] / "assets" / "combat" / "lock_star.png"


@dataclass(frozen=True, slots=True)
class MarkerReading:
    box: tuple[int, int, int, int]
    area: int


@dataclass(frozen=True, slots=True)
class LockStarReading:
    """锁定星形读数。"""

    box: tuple[int, int, int, int]
    score: float
    red_ratio: float


def read_marker(
    frame,
    region,
    hsv_ranges,
    *,
    min_area_ratio: float = 0.0004,
    aspect_range: tuple[float, float] = (0.6, 1.6),
) -> MarkerReading | None:
    """区域内找一个近似方形的高饱和亮色轮廓。"""
    if frame is None or frame.size == 0:
        return None
    height, width = frame.shape[:2]
    if isinstance(region, Region):
        x1, y1, x2, y2 = region.pixels(width, height)
    else:
        x1, y1, x2, y2 = Region(*region).pixels(width, height)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    mask = morphology(color_mask(roi, hsv_ranges))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    min_area = roi.shape[0] * roi.shape[1] * min_area_ratio
    low, high = aspect_range
    best = None
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if not low <= w / max(h, 1) <= high:
            continue
        if best is None or area > best[1]:
            best = ((x, y, w, h), area)
    if best is None:
        return None
    (x, y, w, h), area = best
    return MarkerReading(box=(x1 + x, y1 + y, w, h), area=int(area))


def load_lock_star(path: Path | None = None) -> np.ndarray | None:
    """加载白星二值模板（白=255，黑=0）；文件缺失时返回 None。"""
    target = path or _ASSET
    try:
        image = cv2.imdecode(np.fromfile(str(target), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    except (OSError, cv2.error):
        return None
    if image is None:
        return None
    return (image > 127).astype(np.uint8) * 255


def probe_lock_star(
    frame,
    region,
    *,
    template: np.ndarray | None = None,
    scales: tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2),
    downscale: float = 0.5,
    white_value_min: int = 248,
    white_saturation_max: int = 35,
    glow_radius: float = 1.0,
) -> tuple[LockStarReading | None, float, float]:
    """返回 (最佳匹配读数（可能未达阈值）, 模板最高分, 该位置的红色柔光占比)。"""
    if frame is None or frame.size == 0:
        return None, 0.0, 0.0
    template = template if template is not None else load_lock_star()
    if template is None:
        return None, 0.0, 0.0

    height, width = frame.shape[:2]
    if isinstance(region, Region):
        x1, y1, x2, y2 = region.pixels(width, height)
    else:
        x1, y1, x2, y2 = Region(*region).pixels(width, height)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None, 0.0, 0.0

    work = cv2.resize(roi, None, fx=downscale, fy=downscale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] >= white_value_min) & (hsv[:, :, 1] <= white_saturation_max)).astype(np.uint8) * 255

    best = (0.0, 0, 0, 0)
    for scale in scales:
        scaled = cv2.resize(
            template,
            None,
            fx=scale * downscale,
            fy=scale * downscale,
            interpolation=cv2.INTER_NEAREST,
        )
        if scaled.shape[0] >= white.shape[0] or scaled.shape[1] >= white.shape[1]:
            continue
        result = cv2.matchTemplate(white, scaled, cv2.TM_CCOEFF_NORMED)
        result = np.nan_to_num(result)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        if max_val > best[0]:
            best = (float(max_val), max_loc[0], max_loc[1], scaled.shape[1])

    score, loc_x, loc_y, size = best
    if score <= 0.0:
        return None, 0.0, 0.0

    # 映射回原图，取星形周围 4 倍大小的方框统计“红色柔光”
    center_x = x1 + int(loc_x / downscale) + int(size / downscale) // 2
    center_y = y1 + int(loc_y / downscale) + int(size / downscale) // 2
    half = max(8, int(size / downscale * glow_radius))
    gx1, gy1 = max(0, center_x - half), max(0, center_y - half)
    gx2, gy2 = min(width, center_x + half), min(height, center_y + half)
    glow = frame[gy1:gy2, gx1:gx2]
    glow_hsv = cv2.cvtColor(glow, cv2.COLOR_BGR2HSV)
    hue = glow_hsv[:, :, 0]
    red = ((hue <= 12) | (hue >= 168)) & (glow_hsv[:, :, 1] >= 70) & (glow_hsv[:, :, 2] >= 150)
    red_ratio = float(np.count_nonzero(red)) / max(1, glow.shape[0] * glow.shape[1])

    box = (
        x1 + int(loc_x / downscale),
        y1 + int(loc_y / downscale),
        int(size / downscale),
        int(size / downscale),
    )
    reading = LockStarReading(box=box, score=round(score, 3), red_ratio=round(red_ratio, 3))
    return reading, reading.score, reading.red_ratio


def find_lock_star(
    frame,
    region,
    *,
    template: np.ndarray | None = None,
    score_min: float = 0.72,
    red_min: float = 0.05,
) -> LockStarReading | None:
    """白星分数与红光占比都达标才算锁定。"""
    reading, score, red_ratio = probe_lock_star(frame, region, template=template)
    if reading is None or score < score_min or red_ratio < red_min:
        return None
    return reading
