"""相对坐标区域与多分辨率锚点换算。

模板按基准分辨率裁剪（文件名里带 ``_<宽>x<高>_<x>_<y>``），运行时按
``scale = min(当前宽/基准宽, 当前高/基准高)`` 缩放，锚点保持「离哪边近就贴哪边」
（与 / ok-script 的 ``adjust_coordinates`` 一致）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Region:
    """相对坐标区域（0~1）。"""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if self.x1 >= self.x2 or self.y1 >= self.y2:
            raise ValueError(f"invalid region: {self}")

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            max(0, int(self.x1 * width)),
            max(0, int(self.y1 * height)),
            min(width, int(self.x2 * width)),
            min(height, int(self.y2 * height)),
        )

    def crop(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = self.pixels(width, height)
        return frame[y1:y2, x1:x2]


def scale_anchor(
    x: float,
    y: float,
    base_width: int,
    base_height: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    """把基准分辨率下的锚点映射到当前分辨率（贴边锚定）。"""
    scale = min(width / max(1, base_width), height / max(1, base_height))
    px = width - round((base_width - x) * scale) if x > base_width / 2 else round(x * scale)
    py = height - round((base_height - y) * scale) if y > base_height / 2 else round(y * scale)
    return max(0, min(width, px)), max(0, min(height, py))


def clamp_box(x: int, y: int, w: int, h: int, width: int, height: int) -> tuple[int, int, int, int]:
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    return x, y, max(1, min(w, width - x)), max(1, min(h, height - y))
