"""HUD 的状态文案逻辑：显示什么、放哪里、剩多久。

> 悬浮窗（图标态 / 面板态）已经取代旧的「HUD 悬浮条」：窗口在
> :mod:`src.gui.float_window`，设置在 :mod:`src.core.float_window`。
> 这里保留**可测的纯文本逻辑**（``format_remaining`` / ``build_hud_text``）。

- 剩余时间怎么格式化（``12 分 30 秒`` / ``58 秒`` / ``未知``）；
- 状态文案怎么拼（运行中 / 已暂停 / 任务名 + 预计剩余）；
- 悬浮条贴在哪（按屏幕可用区域底部居中，留 DPI 无关的逻辑像素边距）；
- 旧 HUD 的开关在 ``configs/ui_extras.json`` 的 ``hud_enabled``；
  新悬浮窗用 ``configs/float_window.json``。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SETTINGS_FILE = Path("configs") / "ui_extras.json"
DEFAULT_MARGIN = 24
DEFAULT_MIN_WIDTH = 260
DEFAULT_HEIGHT = 36
UNKNOWN_REMAINING = "未知"


@dataclass(frozen=True)
class HudSettings:
    enabled: bool = True
    margin: int = DEFAULT_MARGIN
    show_remaining: bool = True

    def to_dict(self) -> dict:
        return {"hud_enabled": self.enabled, "hud_margin": self.margin, "hud_show_remaining": self.show_remaining}


def format_remaining(seconds: float | None) -> str:
    """把剩余秒数格式化成中文可读串。"""
    if seconds is None:
        return UNKNOWN_REMAINING
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return UNKNOWN_REMAINING
    if value != value or value < 0:  # NaN / 负数
        return UNKNOWN_REMAINING
    total = round(value)
    if total < 60:
        return f"{total} 秒"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        if secs == 0:
            return f"{minutes} 分钟"
        return f"{minutes} 分 {secs} 秒"
    hours, minutes = divmod(minutes, 60)
    if minutes == 0:
        return f"{hours} 小时"
    return f"{hours} 小时 {minutes} 分"


def build_hud_text(
    task_name: str = "",
    paused: bool = False,
    progress: float | None = None,
    remaining_seconds: float | None = None,
    show_remaining: bool = True,
) -> str:
    """拼一行状态；没任务时返回空串（调用方据此隐藏悬浮条）。"""
    name = str(task_name or "").strip()
    if not name:
        return ""
    state = "已暂停" if paused else "运行中"
    parts = [f"{state} · {name}"]
    if progress is not None:
        try:
            percent = max(0.0, min(0.97, float(progress)))
            parts.append(f"{percent * 100:.0f}%")
        except (TypeError, ValueError):
            pass
    if show_remaining and remaining_seconds is not None:
        parts.append(f"预计还剩 {format_remaining(remaining_seconds)}")
    return "  ".join(parts)


def remaining_seconds(estimate_seconds: float | None, elapsed_seconds: float | None) -> float | None:
    """预计剩余 = 历史平均 - 已用；任一缺失就返回 ``None``。"""
    if estimate_seconds is None or elapsed_seconds is None:
        return None
    try:
        kept = max(0.0, float(estimate_seconds) - float(elapsed_seconds))
    except (TypeError, ValueError):
        return None
    return kept


def hud_position(
    screen_x: int,
    screen_y: int,
    screen_width: int,
    screen_height: int,
    window_width: int,
    window_height: int,
    margin: int = DEFAULT_MARGIN,
) -> tuple[int, int]:
    """底部居中（按屏幕可用区域给坐标，逻辑像素）。"""
    margin = max(0, int(margin))
    x = int(screen_x + (screen_width - window_width) // 2)
    y = int(screen_y + screen_height - window_height - margin)
    # 防止窗口比屏幕还大时跑到屏幕外
    x = max(int(screen_x), min(x, int(screen_x + screen_width - window_width)))
    y = max(int(screen_y), min(y, int(screen_y + screen_height - window_height)))
    return x, y


def load_settings(path: str | Path | None = None) -> HudSettings:
    file_path = Path(path) if path is not None else SETTINGS_FILE
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return HudSettings()
    if not isinstance(data, dict):
        return HudSettings()
    try:
        margin = int(data.get("hud_margin", DEFAULT_MARGIN) or DEFAULT_MARGIN)
    except (TypeError, ValueError):
        margin = DEFAULT_MARGIN
    return HudSettings(
        enabled=bool(data.get("hud_enabled", True)),
        margin=max(0, margin),
        show_remaining=bool(data.get("hud_show_remaining", True)),
    )


def save_settings(settings: HudSettings, path: str | Path | None = None) -> Path:
    file_path = Path(path) if path is not None else SETTINGS_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.update(settings.to_dict())
    tmp = file_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file_path)
    return file_path
