"""UID 遮挡：最多 8 块纯色 / 马赛克，坐标用 1920×1080 参考空间。

为什么坐标不用像素：换分辨率后像素坐标全废，而比例/参考坐标不用重调。
所以配置里一律存 **1920×1080 参考坐标**，真正画的时候按当前画面宽高等比换算。

两种遮盖方式：

- ``solid``：纯色块（默认黑色，可填 ``#RRGGBB``），最彻底；
- ``mosaic``：把该区域缩小再放大（真正的马赛克），保留一点画面感但看不清字。

两个入口：

1. **截图路径**（必须的）：`:func:`apply_privacy_masks`` 在截图落盘前把块画上去，
   这样日志里导出的截图、失败帧都不会泄露 UID；
2. **屏幕显示**（可选）：``only_foreground=True`` 时只有游戏在前台才需要显示，
   这条供覆盖层实现使用 —— 切到别的程序自动隐藏。

配置存成一条全局配置项 ``Privacy Mask Config``，界面在「隐私遮挡」页里拖动调整。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
MAX_BLOCKS = 8
DEFAULT_COLOR = "#000000"
MODES = ("solid", "mosaic")
MOSAIC_BLOCK = 12

_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")


class PrivacyConfigError(ValueError):
    """遮挡配置不合法。"""


def parse_color(value: Any, default: str = DEFAULT_COLOR) -> tuple[int, int, int]:
    """``#RRGGBB`` → BGR 元组（OpenCV 顺序）。非法值退回 ``default``。"""
    text = str(value or "").strip()
    match = _HEX_COLOR.match(text)
    if not match:
        match = _HEX_COLOR.match(str(default))
        if not match:
            return (0, 0, 0)
    hex_value = match.group(1)
    red = int(hex_value[0:2], 16)
    green = int(hex_value[2:4], 16)
    blue = int(hex_value[4:6], 16)
    return (blue, green, red)


def format_color(bgr: Sequence[int]) -> str:
    """BGR 元组 → ``#RRGGBB``。"""
    blue, green, red = (int(max(0, min(255, value))) for value in list(bgr)[:3])
    return f"#{red:02X}{green:02X}{blue:02X}"


@dataclass(frozen=True)
class PrivacyBlock:
    """一块遮挡。坐标与尺寸都是 1920×1080 参考空间里的**像素**。"""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    color: str = DEFAULT_COLOR
    mode: str = "solid"
    label: str = ""

    def __post_init__(self):
        if self.mode not in MODES:
            raise PrivacyConfigError(f"遮挡方式只能是 {', '.join(MODES)}")
        if self.width <= 0 or self.height <= 0:
            raise PrivacyConfigError("遮挡块的宽高必须大于 0")

    # ── 换算 ─────────────────────────────────────────────
    def to_pixels(self, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
        """换算成当前画面的像素矩形 ``(x1, y1, x2, y2)``，并夹在画面内。"""
        scale_x = frame_width / REFERENCE_WIDTH
        scale_y = frame_height / REFERENCE_HEIGHT
        x1 = round(self.x * scale_x)
        y1 = round(self.y * scale_y)
        x2 = round((self.x + self.width) * scale_x)
        y2 = round((self.y + self.height) * scale_y)
        x1 = max(0, min(frame_width, x1))
        x2 = max(0, min(frame_width, x2))
        y1 = max(0, min(frame_height, y1))
        y2 = max(0, min(frame_height, y2))
        if x2 <= x1 or y2 <= y1:
            return (0, 0, 0, 0)
        return (x1, y1, x2, y2)

    def label_or_default(self, index: int = 0) -> str:
        return self.label or f"遮挡 {index + 1}"

    def to_dict(self) -> dict:
        return {
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "width": round(self.width, 1),
            "height": round(self.height, 1),
            "color": self.color,
            "mode": self.mode,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PrivacyBlock:
        if not isinstance(data, Mapping):
            raise PrivacyConfigError("遮挡块必须是对象")

        def number(key: str) -> float:
            try:
                return float(data.get(key, 0) or 0)
            except (TypeError, ValueError) as exc:
                raise PrivacyConfigError(f"遮挡块字段 {key} 必须是数字") from exc

        return cls(
            x=number("x"),
            y=number("y"),
            width=number("width"),
            height=number("height"),
            color=str(data.get("color", DEFAULT_COLOR) or DEFAULT_COLOR),
            mode=str(data.get("mode", "solid") or "solid"),
            label=str(data.get("label", "") or ""),
        )


@dataclass
class PrivacyConfig:
    enabled: bool = True
    only_foreground: bool = True
    blocks: tuple[PrivacyBlock, ...] = ()

    # ── 存取 ─────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PrivacyConfig:
        if not data:
            return cls()
        if not isinstance(data, Mapping):
            raise PrivacyConfigError("遮挡配置必须是对象")
        raw = data.get("blocks") or []
        if not isinstance(raw, list):
            raise PrivacyConfigError("blocks 必须是数组")
        if len(raw) > MAX_BLOCKS:
            raise PrivacyConfigError(f"最多 {MAX_BLOCKS} 块遮挡，当前 {len(raw)} 块")
        blocks = tuple(PrivacyBlock.from_dict(item) for item in raw)
        return cls(
            enabled=bool(data.get("enabled", True)),
            only_foreground=bool(data.get("only_foreground", True)),
            blocks=blocks,
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "only_foreground": self.only_foreground,
            "blocks": [block.to_dict() for block in self.blocks],
        }

    # ── 视图 ─────────────────────────────────────────────
    @property
    def count(self) -> int:
        return len(self.blocks)

    def can_add(self) -> bool:
        return len(self.blocks) < MAX_BLOCKS

    def with_block(self, block: PrivacyBlock) -> PrivacyConfig:
        if not self.can_add():
            raise PrivacyConfigError(f"最多 {MAX_BLOCKS} 块遮挡")
        return PrivacyConfig(self.enabled, self.only_foreground, (*self.blocks, block))

    def without_block(self, index: int) -> PrivacyConfig:
        blocks = list(self.blocks)
        if not 0 <= index < len(blocks):
            raise PrivacyConfigError(f"没有第 {index} 块遮挡")
        blocks.pop(index)
        return PrivacyConfig(self.enabled, self.only_foreground, tuple(blocks))

    def replace_block(self, index: int, block: PrivacyBlock) -> PrivacyConfig:
        blocks = list(self.blocks)
        if not 0 <= index < len(blocks):
            raise PrivacyConfigError(f"没有第 {index} 块遮挡")
        blocks[index] = block
        return PrivacyConfig(self.enabled, self.only_foreground, tuple(blocks))


#: 默认配置：盖住左下角 UID（与 ``config.make_bottom_left_black`` 的位置一致）
DEFAULT_BLOCKS = (
    PrivacyBlock(x=165, y=1056, width=84, height=22, label="UID"),
)


def default_config() -> PrivacyConfig:
    return PrivacyConfig(enabled=True, only_foreground=True, blocks=DEFAULT_BLOCKS)


# ── 绘制 ─────────────────────────────────────────────────
def mask_rect(frame: np.ndarray, rect: tuple[int, int, int, int], color=(0, 0, 0), mode: str = "solid") -> None:
    """就地把矩形区域涂成纯色或马赛克。"""
    x1, y1, x2, y2 = rect
    if x2 <= x1 or y2 <= y1:
        return
    region = frame[y1:y2, x1:x2]
    if region.size == 0:
        return
    if mode == "mosaic":
        height, width = region.shape[:2]
        small_w = max(1, width // MOSAIC_BLOCK)
        small_h = max(1, height // MOSAIC_BLOCK)
        small = cv2.resize(region, (small_w, small_h), interpolation=cv2.INTER_AREA)
        frame[y1:y2, x1:x2] = cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)
        return
    frame[y1:y2, x1:x2] = color


def apply_privacy_masks(
    frame: np.ndarray,
    config: PrivacyConfig | None = None,
    force: bool = True,
) -> np.ndarray:
    """把配置里的遮挡块画到 ``frame`` 上（原地修改并返回）。

    ``force=False`` 时只有 ``config.enabled`` 为真才画；截图路径用 ``force=True``
    （显式调用就意味着「这次一定要遮」）。
    """
    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
        return frame
    if config is None:
        return frame
    if not force and not config.enabled:
        return frame

    height, width = frame.shape[:2]
    for block in config.blocks:
        rect = block.to_pixels(width, height)
        mask_rect(frame, rect, parse_color(block.color), block.mode)
    return frame


def blocks_as_reference(config: PrivacyConfig) -> list[dict]:
    """给 UI 用：把块列表转成便于展示的行。"""
    rows = []
    for index, block in enumerate(config.blocks):
        rows.append(
            {
                "index": index,
                "label": block.label_or_default(index),
                "x": block.x,
                "y": block.y,
                "width": block.width,
                "height": block.height,
                "color": block.color,
                "mode": block.mode,
            }
        )
    return rows


def normalize_blocks(blocks: Iterable[PrivacyBlock]) -> tuple[PrivacyBlock, ...]:
    """夹到参考画面内，并丢掉完全出界的块。"""
    result = []
    for block in blocks:
        x = max(0.0, min(float(block.x), REFERENCE_WIDTH - 1))
        y = max(0.0, min(float(block.y), REFERENCE_HEIGHT - 1))
        width = min(float(block.width), REFERENCE_WIDTH - x)
        height = min(float(block.height), REFERENCE_HEIGHT - y)
        if width <= 0 or height <= 0:
            continue
        result.append(
            PrivacyBlock(
                x=x,
                y=y,
                width=width,
                height=height,
                color=block.color,
                mode=block.mode,
                label=block.label,
            )
        )
    return tuple(result)


# ── 持久化（configs/privacy.json） ───────────────────────
PRIVACY_FILE = Path("configs") / "privacy.json"


def load_config(path: str | Path | None = None) -> PrivacyConfig:
    """读遮挡配置；文件缺失或损坏时退回默认配置（不抛异常）。"""
    file_path = Path(path) if path is not None else PRIVACY_FILE
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_config()
    try:
        config = PrivacyConfig.from_dict(data)
    except PrivacyConfigError:
        return default_config()
    if not config.blocks:
        return default_config()
    return PrivacyConfig(
        enabled=config.enabled,
        only_foreground=config.only_foreground,
        blocks=normalize_blocks(config.blocks),
    )


def save_config(config: PrivacyConfig, path: str | Path | None = None) -> Path:
    """原子写遮挡配置。"""
    file_path = Path(path) if path is not None else PRIVACY_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(".json.tmp")
    payload = PrivacyConfig(
        enabled=config.enabled,
        only_foreground=config.only_foreground,
        blocks=normalize_blocks(config.blocks),
    ).to_dict()
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file_path)
    return file_path


def make_screenshot_processor():
    """给 ``app_config['screenshot_processor']`` 用：落盘截图前套上遮挡。

    配置读不到或用户关掉了遮挡时，退回默认的单块 UID 遮挡 —— 宁可多遮一点，
    也不要因为配置文件坏了把 UID 泄露到日志里。
    """

    def processor(frame):
        try:
            config = load_config()
        except Exception:
            config = default_config()
        if not config.enabled:
            return frame
        if not config.blocks:
            config = default_config()
        return apply_privacy_masks(frame, config)

    return processor
