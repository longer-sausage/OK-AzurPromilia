"""图像预处理原语。

全部是无状态函数，输入 BGR 图，输出掩膜或处理后的图。模板预处理函数会被
``TemplateLibrary`` 同时作用在模板与搜索区域上。
"""

from __future__ import annotations

import cv2
import numpy as np


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def binarize_brightness(image: np.ndarray, threshold: int = 180, to_bgr: bool = False) -> np.ndarray:
    """按亮度二值化。"""
    gray = to_gray(image)
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    if to_bgr:
        return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    return mask


def color_mask(image: np.ndarray, hsv_ranges) -> np.ndarray:
    """多段 HSV 区间合并成一张掩膜（红色跨 0 边界时需要两段）。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = None
    for lower, upper in hsv_ranges:
        part = cv2.inRange(hsv, np.array(lower, np.uint8), np.array(upper, np.uint8))
        mask = part if mask is None else cv2.bitwise_or(mask, part)
    if mask is None:
        raise ValueError("hsv ranges must not be empty")
    return mask


def mask_corners(
    image: np.ndarray,
    ratio_w: float = 0.5,
    ratio_h: float = 0.5,
    to_bgr: bool = False,
) -> np.ndarray:
    """只保留中心区域。"""
    height, width = image.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    x1 = int(width * (0.5 - ratio_w / 2))
    x2 = int(width * (0.5 + ratio_w / 2))
    y1 = int(height * (0.5 - ratio_h / 2))
    y2 = int(height * (0.5 + ratio_h / 2))
    mask[max(0, y1) : max(0, y2), max(0, x1) : max(0, x2)] = 255
    masked = cv2.bitwise_and(image, image, mask=mask) if image.ndim == 3 else cv2.bitwise_and(image, mask)
    if to_bgr and masked.ndim == 2:
        return cv2.cvtColor(masked, cv2.COLOR_GRAY2BGR)
    return masked


def morphology(mask: np.ndarray, kernel_size: int = 3, closing: bool = True) -> np.ndarray:
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    if closing:
        return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)


def lab_opponent(image: np.ndarray) -> np.ndarray:
    """Lab 的 a/b 两通道：颜色关系比 HSV 阈值更抗光照。"""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2Lab)
    return lab[:, :, 1:3]


def active_color_mask(
    image: np.ndarray,
    hue_ranges,
    *,
    min_saturation: int = 55,
    min_value: int = 65,
    min_chroma: int = 20,
) -> np.ndarray:
    """高饱和 + 指定色相 + Lab 色度的“活跃色”掩膜。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2Lab).astype(np.int16)
    chroma = np.sqrt((lab[:, :, 1] - 128) ** 2 + (lab[:, :, 2] - 128) ** 2)

    hue = hsv[:, :, 0]
    hue_mask = np.zeros(hue.shape, bool)
    for lower, upper in hue_ranges:
        hue_mask |= (hue >= lower) & (hue <= upper)
    mask = hue_mask & (hsv[:, :, 1] >= min_saturation) & (hsv[:, :, 2] >= min_value) & (chroma >= min_chroma)
    return (mask.astype(np.uint8)) * 255
