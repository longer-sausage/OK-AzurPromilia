"""悬浮窗设置：颜色、不透明度、大小、位置、悬浮图标大小。

窗口本身在 :mod:`src.gui.float_window`，设置页在 :mod:`src.gui.float_window_tab`。
这里只放**可离线测试的纯逻辑**：

- 不透明度下限 **10%**（再低就完全看不清，等于关掉）；
- 位置夹取（拖动后不能跑出屏幕，换分辨率也不会找不到）；
- 队列 ETA 文案（每个任务「约 N 分钟」+ 总计剩余）；
- 设置文件读写（`configs/float_window.json`），坏值一律回退默认而不是抛错。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SETTINGS_FILE = Path("configs") / "float_window.json"
DEFAULT_ICON_PATH = "icons/icon.png"

MIN_OPACITY = 0.10
MAX_OPACITY = 1.00
DEFAULT_OPACITY = 0.92
DEFAULT_BG_COLOR = "#1B1B1F"
DEFAULT_CORNER_RADIUS = 14

MIN_ICON_SIZE = 32
MAX_ICON_SIZE = 160
DEFAULT_ICON_SIZE = 64

MIN_PANEL_WIDTH = 240
MAX_PANEL_WIDTH = 720
DEFAULT_PANEL_WIDTH = 340
MIN_PANEL_HEIGHT = 160
MAX_PANEL_HEIGHT = 900
DEFAULT_PANEL_HEIGHT = 320

DEFAULT_MAX_TASKS = 8
MAX_TASKS_LIMIT = 20
DEFAULT_POLL_MS = 500

#: 默认离屏幕边缘的距离
DEFAULT_MARGIN = 16
#: 默认位置离屏幕底边再多留这么多：避开 Windows 通知弹窗（从任务栏上方升起）
NOTIFICATION_CLEARANCE = 72

UNKNOWN_ETA = "未知"


def _clamp(value, low, high, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:  # NaN
        return fallback
    return max(low, min(high, number))


def _clamp_int(value, low, high, fallback) -> int:
    return round(_clamp(value, low, high, fallback))


def _clamp_color(value, fallback: str = DEFAULT_BG_COLOR) -> str:
    text = str(value or "").strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3 and all(ch in "0123456789abcdefABCDEF" for ch in text):
        text = "".join(ch * 2 for ch in text)
    if len(text) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in text):
        return f"#{text.lower()}"
    return fallback


@dataclass(frozen=True)
class FloatSettings:
    """悬浮窗的全部可调项。"""

    enabled: bool = True
    icon_path: str = DEFAULT_ICON_PATH
    icon_size: int = DEFAULT_ICON_SIZE
    icon_x: int | None = None
    icon_y: int | None = None
    panel_width: int = DEFAULT_PANEL_WIDTH
    panel_height: int = DEFAULT_PANEL_HEIGHT
    panel_x: int | None = None
    panel_y: int | None = None
    opacity: float = DEFAULT_OPACITY
    bg_color: str = DEFAULT_BG_COLOR
    corner_radius: int = DEFAULT_CORNER_RADIUS
    show_queue: bool = True
    max_tasks: int = DEFAULT_MAX_TASKS
    poll_ms: int = DEFAULT_POLL_MS
    collapsed: bool = True

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "icon_path": self.icon_path,
            "icon_size": self.icon_size,
            "icon_x": self.icon_x,
            "icon_y": self.icon_y,
            "panel_width": self.panel_width,
            "panel_height": self.panel_height,
            "panel_x": self.panel_x,
            "panel_y": self.panel_y,
            "opacity": self.opacity,
            "bg_color": self.bg_color,
            "corner_radius": self.corner_radius,
            "show_queue": self.show_queue,
            "max_tasks": self.max_tasks,
            "poll_ms": self.poll_ms,
            "collapsed": self.collapsed,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> FloatSettings:
        data = data if isinstance(data, dict) else {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            icon_path=str(data.get("icon_path") or DEFAULT_ICON_PATH),
            icon_size=_clamp_int(data.get("icon_size"), MIN_ICON_SIZE, MAX_ICON_SIZE, DEFAULT_ICON_SIZE),
            icon_x=_optional_int(data.get("icon_x")),
            icon_y=_optional_int(data.get("icon_y")),
            panel_width=_clamp_int(
                data.get("panel_width"), MIN_PANEL_WIDTH, MAX_PANEL_WIDTH, DEFAULT_PANEL_WIDTH
            ),
            panel_height=_clamp_int(
                data.get("panel_height"), MIN_PANEL_HEIGHT, MAX_PANEL_HEIGHT, DEFAULT_PANEL_HEIGHT
            ),
            panel_x=_optional_int(data.get("panel_x")),
            panel_y=_optional_int(data.get("panel_y")),
            opacity=round(_clamp(data.get("opacity"), MIN_OPACITY, MAX_OPACITY, DEFAULT_OPACITY), 2),
            bg_color=_clamp_color(data.get("bg_color")),
            corner_radius=_clamp_int(data.get("corner_radius"), 0, 30, DEFAULT_CORNER_RADIUS),
            show_queue=bool(data.get("show_queue", True)),
            max_tasks=_clamp_int(data.get("max_tasks"), 1, MAX_TASKS_LIMIT, DEFAULT_MAX_TASKS),
            poll_ms=_clamp_int(data.get("poll_ms"), 200, 5000, DEFAULT_POLL_MS),
            collapsed=bool(data.get("collapsed", True)),
        )

    def with_changes(self, **kwargs) -> FloatSettings:
        """改几项、重新夹取（UI 里调完值直接用它落盘）。"""
        merged = self.to_dict()
        merged.update({key: value for key, value in kwargs.items() if key in merged})
        return FloatSettings.from_dict(merged)


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def load_settings(path: str | Path | None = None) -> FloatSettings:
    target = Path(path) if path is not None else SETTINGS_FILE
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FloatSettings()
    return FloatSettings.from_dict(data)


def save_settings(settings: FloatSettings, path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else SETTINGS_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass
    return target


def clamp_position(
    x: int | None, y: int | None, width: int, height: int,
    screen_width: int, screen_height: int, margin: int = 0,
    offset_x: int = 0, offset_y: int = 0,
) -> tuple[int, int]:
    """把窗口位置夹到屏幕内（允许贴边，不允许多数面积跑到屏幕外）。

    ``offset_x/offset_y`` 是屏幕可用区域的左上角：任务栏在左边或上边时
    ``availableGeometry()`` 不是从 (0,0) 开始，夹取必须按那个原点算。
    """
    screen_width = max(1, int(screen_width))
    screen_height = max(1, int(screen_height))
    width = min(max(1, int(width)), screen_width)
    height = min(max(1, int(height)), screen_height)
    left = int(offset_x) + margin
    top = int(offset_y) + margin
    max_x = max(left, int(offset_x) + screen_width - width - margin)
    max_y = max(top, int(offset_y) + screen_height - height - margin)
    safe_x = left if x is None else int(x)
    safe_y = top if y is None else int(y)
    return max(left, min(safe_x, max_x)), max(top, min(safe_y, max_y))


def default_position(
    width: int, height: int, screen_width: int, screen_height: int,
    margin: int = DEFAULT_MARGIN, clearance: int = NOTIFICATION_CLEARANCE,
    offset_x: int = 0, offset_y: int = 0,
) -> tuple[int, int]:
    """默认放在**屏幕右下角**，离底边再留出通知区的高度。

    为什么不是贴到底：Windows 的通知弹窗从任务栏上方升起，正好盖住右下角那块；
    留 ``clearance``（默认 72px）能避开它，视觉上仍然在右下角。
    """
    screen_width = max(1, int(screen_width))
    screen_height = max(1, int(screen_height))
    width = min(max(1, int(width)), screen_width)
    height = min(max(1, int(height)), screen_height)
    x = int(offset_x) + screen_width - width - margin
    y = int(offset_y) + screen_height - height - margin - max(0, int(clearance))
    return clamp_position(
        x, y, width, height, screen_width, screen_height,
        margin=margin, offset_x=offset_x, offset_y=offset_y,
    )


def format_eta(seconds: float | None) -> str:
    """``约 3 分钟`` / ``约 1 分 20 秒`` / ``未知``。"""
    if seconds is None:
        return UNKNOWN_ETA
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return UNKNOWN_ETA
    if value != value or value < 0:
        return UNKNOWN_ETA
    total = round(value)
    if total < 60:
        return f"约 {total} 秒"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"约 {minutes} 分钟" if secs == 0 else f"约 {minutes} 分 {secs} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"约 {hours} 小时" if minutes == 0 else f"约 {hours} 小时 {minutes} 分"


def queue_rows(snapshot: dict | None, max_tasks: int = DEFAULT_MAX_TASKS) -> list[dict]:
    """把快照里的队列转成要显示的若干行：``[{name, state, eta, eta_text}]``。"""
    if not isinstance(snapshot, dict):
        return []
    items = snapshot.get("items") or []
    rows = []
    for item in list(items)[: max(1, int(max_tasks))]:
        if not isinstance(item, dict):
            continue
        eta = item.get("eta_seconds")
        rows.append(
            {
                "name": str(item.get("name", "")),
                "state": str(item.get("state", "")),
                "eta": eta,
                "eta_text": format_eta(eta),
            }
        )
    return rows


def remaining_text(snapshot: dict | None) -> str:
    """队列总剩余（已完成的项按已过时间扣掉）。"""
    if not isinstance(snapshot, dict):
        return UNKNOWN_ETA
    total = snapshot.get("remaining_seconds")
    return format_eta(total)


def title_text(snapshot: dict | None) -> str:
    if not isinstance(snapshot, dict):
        return "空闲"
    task = str(snapshot.get("task", "") or "")
    paused = bool(snapshot.get("paused"))
    queue = snapshot.get("queue") or ""
    if paused and task:
        return f"已暂停 · {task}"
    if task:
        return task
    if queue:
        return f"队列 · {queue}"
    return "空闲"
