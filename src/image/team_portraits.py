"""实机队伍 HUD 的槽位几何与头像相似度。坐标以 1920×1080 客户区为基准。"""

import cv2
import numpy as np

SLOT_COUNT = 3
PORTRAIT_SIZE = 56
_SLOT_STEP = 85
_Y, _X = np.ogrid[-17:18, -17:18]
_RADIUS = np.hypot(_Y, _X)
_BADGE_RIM = (_RADIUS >= 10) & (_RADIUS <= 12)
_BADGE_INSIDE = (_RADIUS >= 6) & (_RADIUS <= 9)
_BADGE_DIGIT = _RADIUS < 6


def _slot_crop(frame: np.ndarray, index: int, left: int, top: int, size: int) -> np.ndarray:
    h, w = frame.shape[:2]
    top += index * _SLOT_STEP
    y1, y2 = round(top * h / 1080), round((top + size) * h / 1080)
    x1, x2 = round(left * w / 1920), round((left + size) * w / 1920)
    crop = frame[y1:y2, x1:x2, :3]
    return cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)


def valid_frame(frame: np.ndarray | None) -> bool:
    return (isinstance(frame, np.ndarray) and frame.ndim == 3 and frame.shape[2] in (3, 4)
            and frame.dtype == np.uint8 and min(frame.shape[:2]) >= 90)


def read_team_hud(frame: np.ndarray | None) -> tuple[float, ...]:
    """三个数字键徽标的字色对比度。空位为 0，出战角色的数字呈灰色。"""
    if not valid_frame(frame):
        return (0.0,) * SLOT_COUNT
    contrasts = []
    for index in range(SLOT_COUNT):
        badge = cv2.cvtColor(_slot_crop(frame, index, 1842, 216, 35), cv2.COLOR_BGR2GRAY)
        background = badge[_BADGE_INSIDE].mean()
        rim = badge[_BADGE_RIM].mean() - background
        digit = float(np.percentile(badge[_BADGE_DIGIT], 95) - background)
        contrasts.append(digit if rim > 20 and digit > 25 else 0.0)
    return tuple(contrasts)


def slot_portrait(frame: np.ndarray, index: int) -> np.ndarray:
    return _slot_crop(frame, index, 1768, 215, PORTRAIT_SIZE)


def portrait_score(portrait: np.ndarray, template: np.ndarray) -> float:
    # 搜索头像中央，给不同分辨率的像素取整和 HUD 动画留少量余量。
    # 轻度平滑可以抑制不同分辨率、背景特效和抗锯齿造成的高频差异。
    center = cv2.GaussianBlur(template, (0, 0), 1.1)[7:-7, 7:-7]
    if center.std(axis=(0, 1)).max() < 3:
        return 0.0  # 常色模板的归一化相关系数没有意义。
    scores = cv2.matchTemplate(cv2.GaussianBlur(portrait, (0, 0), 1.1), center, cv2.TM_CCOEFF_NORMED)
    return max(0.0, float(scores.max()))
