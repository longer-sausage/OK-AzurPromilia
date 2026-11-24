"""坐标拾取：客户区像素 ↔ 1920×1080 参考坐标，外加取色。

写流程、点技能、标模板都需要两套坐标：

- **客户区坐标**：这一台机器、这个分辨率下的真实像素，直接给键鼠用；
- **参考坐标**：1920×1080 空间，写进配置才不会因为换分辨率失效。

两者互转就是一次等比缩放。取色顺手给出 ``#RRGGBB``，一起复制走。
"""

from __future__ import annotations

from dataclasses import dataclass

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080


def client_to_reference(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    """客户区像素 → 参考坐标（保留 1 位小数）。"""
    if width <= 0 or height <= 0:
        return (0.0, 0.0)
    return (
        round(float(x) * REFERENCE_WIDTH / width, 1),
        round(float(y) * REFERENCE_HEIGHT / height, 1),
    )


def reference_to_client(rx: float, ry: float, width: int, height: int) -> tuple[int, int]:
    """参考坐标 → 客户区像素（四舍五入到整数像素）。"""
    if width <= 0 or height <= 0:
        return (0, 0)
    return (
        round(float(rx) * width / REFERENCE_WIDTH),
        round(float(ry) * height / REFERENCE_HEIGHT),
    )


def format_snippet(rx: float, ry: float) -> str:
    """复制用的片段：``"x": 960, "y": 540``。"""
    def number(value: float) -> str:
        return str(round(value))

    return f'"x": {number(rx)}, "y": {number(ry)}'


def color_hex(frame, x: int, y: int) -> str:
    """取 ``frame[y, x]`` 的颜色，返回 ``#RRGGBB``；越界返回 ``#000000``。"""
    try:
        import numpy as np

        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return "#000000"
        height, width = frame.shape[:2]
        px = int(max(0, min(width - 1, x)))
        py = int(max(0, min(height - 1, y)))
        pixel = frame[py, px]
        if getattr(pixel, "ndim", 0) > 0:
            blue, green, red = (int(value) for value in pixel[:3])
        else:  # 灰度图
            blue = green = red = int(pixel)
        return f"#{red:02X}{green:02X}{blue:02X}"
    except Exception:
        return "#000000"


@dataclass(frozen=True)
class PickResult:
    """一次拾取的完整结果。"""

    client_x: int
    client_y: int
    ref_x: float
    ref_y: float
    color: str
    client_width: int
    client_height: int

    @property
    def snippet(self) -> str:
        return format_snippet(self.ref_x, self.ref_y)

    @property
    def color_snippet(self) -> str:
        return f'"color": "{self.color}"'

    def summary(self) -> str:
        return (
            f"客户区 ({self.client_x}, {self.client_y}) → "
            f"参考 ({self.ref_x:g}, {self.ref_y:g})，颜色 {self.color}"
        )


def pick(frame, x: int, y: int, width: int | None = None, height: int | None = None) -> PickResult:
    """把一次点击换算成 PickResult。"""
    if width is None or height is None:
        try:
            height, width = frame.shape[:2]
        except Exception:
            width, height = REFERENCE_WIDTH, REFERENCE_HEIGHT
    ref_x, ref_y = client_to_reference(x, y, int(width or REFERENCE_WIDTH), int(height or REFERENCE_HEIGHT))
    return PickResult(
        client_x=int(x),
        client_y=int(y),
        ref_x=ref_x,
        ref_y=ref_y,
        color=color_hex(frame, int(x), int(y)),
        client_width=int(width or REFERENCE_WIDTH),
        client_height=int(height or REFERENCE_HEIGHT),
    )
