"""交互提示（F 按钮）识别。

画面上出现"金色圆环 + 白色 F"即表示附近有可交互目标。用白色像素掩膜做模板匹配：

- 模板 `assets/combat/interact_f.png` 是白色掩膜（环形 + F 字形）；
- 只在画面中央区域搜索（交互目标一般在附近）；
- 半分辨率 + 3 档缩放，兼顾速度与距离差异。

实测：带提示的画面 0.78，无提示的画面 ≤0.38，阈值取 0.6 可分。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from src.combat.recognition.regions import Region

_ASSET = Path(__file__).resolve().parents[3] / "assets" / "combat" / "interact_f.png"


def load_interact_template(path: Path | None = None) -> np.ndarray | None:
    """加载交互提示模板（白色掩膜）；文件缺失时返回 None。"""
    target = path or _ASSET
    try:
        image = cv2.imdecode(np.fromfile(str(target), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    except (OSError, cv2.error):
        return None
    if image is None:
        return None
    return (image > 127).astype(np.uint8) * 255


def probe_interact_prompt(
    frame,
    *,
    template: np.ndarray | None = None,
    region: tuple[float, float, float, float] = (0.15, 0.15, 0.85, 0.85),
    scales: tuple[float, ...] = (0.85, 1.0, 1.15),
    downscale: float = 0.5,
    white_value_min: int = 235,
    white_saturation_max: int = 45,
) -> tuple[tuple[int, int, int, int] | None, float]:
    """返回 (提示框或 None, 最高分)。"""
    if frame is None or getattr(frame, "size", 0) == 0:
        return None, 0.0
    template = template if template is not None else load_interact_template()
    if template is None:
        return None, 0.0

    height, width = frame.shape[:2]
    x1, y1, x2, y2 = Region(*region).pixels(width, height)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None, 0.0

    work = cv2.resize(roi, None, fx=downscale, fy=downscale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] >= white_value_min) & (hsv[:, :, 1] <= white_saturation_max)).astype(np.uint8) * 255
    if not white.any():
        return None, 0.0

    best_score = 0.0
    best_box = None
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
        result = np.nan_to_num(cv2.matchTemplate(white, scaled, cv2.TM_CCOEFF_NORMED))
        _min_val, max_val, _min_loc, (loc_x, loc_y) = cv2.minMaxLoc(result)
        if max_val > best_score:
            best_score = float(max_val)
            w = int(scaled.shape[1] / downscale)
            h = int(scaled.shape[0] / downscale)
            best_box = (x1 + int(loc_x / downscale), y1 + int(loc_y / downscale), w, h)
    return best_box, round(best_score, 3)


def find_interact_prompt(
    frame,
    *,
    template: np.ndarray | None = None,
    region: tuple[float, float, float, float] = (0.15, 0.15, 0.85, 0.85),
    threshold: float = 0.6,
) -> tuple[int, int, int, int] | None:
    """画面上有交互提示时返回提示框，否则 None。"""
    box, score = probe_interact_prompt(frame, template=template, region=region)
    if box is None or score < threshold:
        return None
    return box
