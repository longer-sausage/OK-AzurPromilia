"""任务队列悬浮窗：把「正在跑的 + 排队等着的」任务和预计耗时摆在桌面上。

和仓库里 2.x 的 ``float_window`` 是两回事（那个是图标态/面板态的任务状态窗）。

行为（对应需求）：

- **游戏在前台时永远不显示**——挡在游戏画面上没有意义，还会影响操作；
- **游戏在后台时**由用户决定显不显示（``show_in_background``）；
- 透明度 / 位置 / 大小 / 图标都能改，写 ``configs/queue_panel.json``，重启后原地复原；
- 内容：每个排队任务的「约 N 分钟」+ 整体合计；耗时取该任务**最近 5 次**实际用时的平均值
  （见 :mod:`src.core.queue_eta`），没跑过就显示「未知」；
- 窗口是置顶 ``Tool`` 窗、``WA_ShowWithoutActivating``：**显示时不抢焦点**，可以拖动、拖右下角改大小。

数据来源：``og.executor`` 的 ``current_task`` / ``onetime_task_queue``（ok-script 3.x）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from src.core import queue_eta

SETTINGS_FILE = Path("configs") / "queue_panel.json"
DEFAULT_ICON_PATH = "icons/icon.png"

MIN_OPACITY = 0.20
MAX_OPACITY = 1.00
DEFAULT_OPACITY = 0.92

MIN_WIDTH = 220
MAX_WIDTH = 720
DEFAULT_WIDTH = 320
MIN_HEIGHT = 120
MAX_HEIGHT = 900
DEFAULT_HEIGHT = 200

#: 图标态：只显示图标，点一下展开面板
MIN_ICON_SIZE = 32
MAX_ICON_SIZE = 160
DEFAULT_ICON_SIZE = 64

MIN_CORNER_RADIUS = 0
MAX_CORNER_RADIUS = 30
DEFAULT_CORNER_RADIUS = 12

DEFAULT_MAX_ROWS = 8
MAX_ROWS_LIMIT = 20
DEFAULT_POLL_MS = 500
MIN_POLL_MS = 200
MAX_POLL_MS = 5000

DEFAULT_MARGIN = 16
DEFAULT_BG_COLOR = "#16161C"
GRIP_SIZE = 14
TITLE_HEIGHT = 34
#: 单击展开后这么久内忽略「双击收回」，避免手快时刚展开又收回去
EXPAND_GUARD_SECONDS = 0.3

_CURRENT: QueuePanel | None = None


def set_current_panel(panel: QueuePanel | None) -> None:
    global _CURRENT
    _CURRENT = panel


def current_panel() -> QueuePanel | None:
    return _CURRENT


def _tr(text: str) -> str:
    try:
        from ok import og

        app = getattr(og, "app", None)
        if app is not None:
            return app.tr(text)
    except Exception:
        pass
    return text


def _clamp(value, low, high, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:
        return fallback
    return max(low, min(high, number))


def _clamp_int(value, low, high, fallback) -> int:
    return round(_clamp(value, low, high, fallback))


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PanelSettings:
    """悬浮窗的全部可调项。"""

    enabled: bool = True
    show_in_background: bool = True
    opacity: float = DEFAULT_OPACITY
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    x: int | None = None
    y: int | None = None
    icon_path: str = DEFAULT_ICON_PATH
    icon_size: int = DEFAULT_ICON_SIZE
    icon_x: int | None = None
    icon_y: int | None = None
    corner_radius: int = DEFAULT_CORNER_RADIUS
    max_rows: int = DEFAULT_MAX_ROWS
    poll_ms: int = DEFAULT_POLL_MS
    #: 形态：True = 单个图标（收起），False = 队列面板（展开）
    collapsed: bool = True

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "show_in_background": self.show_in_background,
            "opacity": self.opacity,
            "width": self.width,
            "height": self.height,
            "x": self.x,
            "y": self.y,
            "icon_path": self.icon_path,
            "icon_size": self.icon_size,
            "icon_x": self.icon_x,
            "icon_y": self.icon_y,
            "corner_radius": self.corner_radius,
            "max_rows": self.max_rows,
            "poll_ms": self.poll_ms,
            "collapsed": self.collapsed,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> PanelSettings:
        data = data if isinstance(data, dict) else {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            show_in_background=bool(data.get("show_in_background", True)),
            opacity=round(_clamp(data.get("opacity"), MIN_OPACITY, MAX_OPACITY, DEFAULT_OPACITY), 2),
            width=_clamp_int(data.get("width"), MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH),
            height=_clamp_int(data.get("height"), MIN_HEIGHT, MAX_HEIGHT, DEFAULT_HEIGHT),
            x=_optional_int(data.get("x")),
            y=_optional_int(data.get("y")),
            icon_path=str(data.get("icon_path") or DEFAULT_ICON_PATH),
            icon_size=_clamp_int(data.get("icon_size"), MIN_ICON_SIZE, MAX_ICON_SIZE, DEFAULT_ICON_SIZE),
            icon_x=_optional_int(data.get("icon_x")),
            icon_y=_optional_int(data.get("icon_y")),
            corner_radius=_clamp_int(
                data.get("corner_radius"), MIN_CORNER_RADIUS, MAX_CORNER_RADIUS, DEFAULT_CORNER_RADIUS
            ),
            max_rows=_clamp_int(data.get("max_rows"), 1, MAX_ROWS_LIMIT, DEFAULT_MAX_ROWS),
            poll_ms=_clamp_int(data.get("poll_ms"), MIN_POLL_MS, MAX_POLL_MS, DEFAULT_POLL_MS),
            collapsed=bool(data.get("collapsed", True)),
        )

    def with_changes(self, **kwargs) -> PanelSettings:
        merged = self.to_dict()
        merged.update({key: value for key, value in kwargs.items() if key in merged})
        return PanelSettings.from_dict(merged)


def load_settings(path: str | Path | None = None) -> PanelSettings:
    target = Path(path) if path is not None else SETTINGS_FILE
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PanelSettings()
    return PanelSettings.from_dict(data)


def save_settings(settings: PanelSettings, path: str | Path | None = None) -> Path:
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
    screen_width: int, screen_height: int, margin: int = DEFAULT_MARGIN,
    offset_x: int = 0, offset_y: int = 0,
) -> tuple[int, int]:
    """把窗口位置夹进屏幕（换分辨率后不会跑到屏幕外）。"""
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
    margin: int = DEFAULT_MARGIN, offset_x: int = 0, offset_y: int = 0,
) -> tuple[int, int]:
    """默认放**屏幕右下角**（离任务栏和屏幕边缘各留 ``margin``）。

    屏幕高宽都是「工作区」尺寸——``availableGeometry()`` 已经扣掉任务栏，
    所以贴右下角也不会被任务栏压住。
    """
    screen_width = max(1, int(screen_width))
    screen_height = max(1, int(screen_height))
    width = min(max(1, int(width)), screen_width)
    height = min(max(1, int(height)), screen_height)
    x = int(offset_x) + screen_width - width - margin
    y = int(offset_y) + screen_height - height - margin
    return clamp_position(
        x, y, width, height, screen_width, screen_height,
        margin=margin, offset_x=offset_x, offset_y=offset_y,
    )


def centered_on(
    anchor: tuple[int, int, int, int], width: int, height: int,
    screen_width: int, screen_height: int,
    offset_x: int = 0, offset_y: int = 0,
) -> tuple[int, int]:
    """以 ``anchor`` 矩形的**中心**摆一个 ``width×height`` 的窗口，并夹进工作区。

    展开面板时用它：面板出现在图标的正中央；靠近屏幕边缘时「尽量居中」——
    夹取会把它推回工作区内，但方向仍朝着图标那侧的对面。
    """
    ax, ay, aw, ah = (int(value) for value in anchor)
    center_x = ax + aw // 2
    center_y = ay + ah // 2
    x = center_x - max(1, int(width)) // 2
    y = center_y - max(1, int(height)) // 2
    return clamp_position(
        x, y, width, height, screen_width, screen_height,
        margin=0, offset_x=offset_x, offset_y=offset_y,
    )


# ── 数据源 ───────────────────────────────────────────────
def game_hwnd() -> int:
    """游戏窗口句柄：取框架当前选中的那个（``device_manager.hwnd_window``）。

    拿不到就返回 0——那说明游戏还没被程序连上，此时「不在前台」是安全判断。
    """
    try:
        from ok import og

        device_manager = getattr(og, "device_manager", None)
        return int(getattr(getattr(device_manager, "hwnd_window", None), "hwnd", 0) or 0)
    except Exception:
        return 0


def _foreground_process_name() -> str:
    """前台窗口的可执行文件名（小写）；取不到返回空串。"""
    try:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().casefold()
    except Exception:
        return ""


def _configured_exe_names() -> set[str]:
    """``config['windows']['exe']`` 里的进程名（小写）。"""
    try:
        from src.config import config as app_config

        exe = (app_config.get("windows", {}) or {}).get("exe", ())
        if isinstance(exe, str):
            exe = (exe,)
        return {str(name).casefold() for name in exe if name}
    except Exception:
        return set()


def game_in_foreground(hwnd: int) -> bool:
    """游戏是不是前台窗口。

    已知句柄时按「句柄相同 / 同进程」判断（游戏自己弹的小窗也算）；
    句柄未知时退回按前台进程名判断——这样游戏在跑但还没被连上也不会把悬浮窗盖上去。
    """
    try:
        import win32gui
        import win32process
    except Exception:
        return False
    try:
        foreground = int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        return False
    if not foreground:
        return False
    if hwnd:
        if foreground == int(hwnd):
            return True
        try:
            _, game_pid = win32process.GetWindowThreadProcessId(int(hwnd))
            _, foreground_pid = win32process.GetWindowThreadProcessId(foreground)
            return bool(game_pid) and int(game_pid) == int(foreground_pid)
        except Exception:
            return False
    exe_names = _configured_exe_names()
    return bool(exe_names) and _foreground_process_name() in exe_names


def queue_snapshot() -> tuple[str | None, list[str]]:
    """``(当前任务名, 排队中的任务名列表)``；拿不到执行器就是 ``(None, [])``。"""
    try:
        from ok import og

        executor = getattr(og, "executor", None)
        if executor is None:
            return None, []
        current = getattr(executor, "current_task", None)
        current_name = None
        if current is not None and getattr(current, "running", False):
            current_name = str(getattr(current, "name", "") or "") or None
        lock = getattr(executor, "lock", None)
        names: list[str] = []
        try:
            if lock is not None:
                with lock:
                    names = [str(getattr(task, "name", "") or "") for task in executor.onetime_task_queue]
            else:
                names = [str(getattr(task, "name", "") or "") for task in executor.onetime_task_queue]
        except Exception:
            names = []
        return current_name, [name for name in names if name]
    except Exception:
        return None, []


class QueuePanel(QWidget):
    """置顶的队列悬浮窗。"""

    settings_changed = Signal(object)

    def __init__(self, settings: PanelSettings | None = None, hwnd_provider=None, parent=None):
        super().__init__(parent)
        self._settings = settings or load_settings()
        self._hwnd_provider = hwnd_provider
        self._rows: list[dict] = []
        self._total_text = queue_eta.UNKNOWN_TEXT
        self._history: dict[str, list[float]] = {}
        self._press_pos: QPoint | None = None
        self._drag_origin: QPoint | None = None
        self._resize_origin: tuple[QPoint, int, int] | None = None
        self._hidden_by_game = False
        # 形态切换的时间戳与「吞掉双击后多出来的那次 release」的标记
        self._expanded_at = 0.0
        self._collapsed_at = 0.0
        self._suppress_next_release = False

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self.apply_settings(self._settings, persist=False)

    # ── 设置 ─────────────────────────────────────────────
    @property
    def settings(self) -> PanelSettings:
        return self._settings

    def apply_settings(self, settings: PanelSettings, persist: bool = True) -> None:
        self._settings = settings
        self.setWindowOpacity(max(MIN_OPACITY, float(settings.opacity)))
        self._apply_geometry()
        self._timer.start(settings.poll_ms)
        if persist:
            save_settings(settings)
        self.settings_changed.emit(settings)
        self.update()

    @property
    def collapsed(self) -> bool:
        return bool(self._settings.collapsed)

    def expand(self) -> None:
        """图标态 → 面板态（左键单击图标）。

        面板以**图标中心**为基准生成：靠近屏幕边缘时夹回工作区（``centered_on``），
        所以仍然不会被任务栏或屏幕边缘切掉。
        """
        if not self.collapsed:
            return
        self._expanded_at = time.monotonic()
        offset_x, offset_y, screen_width, screen_height = self._available_rect()
        icon_rect = (self.pos().x(), self.pos().y(), self.width(), self.height())
        x, y = centered_on(
            icon_rect, self._settings.width, self._settings.height,
            screen_width, screen_height, offset_x=offset_x, offset_y=offset_y,
        )
        self.apply_settings(self._settings.with_changes(collapsed=False, x=x, y=y))
        self._reload_rows()

    def collapse(self) -> None:
        """面板态 → 图标态（左键双击面板）。"""
        if self.collapsed:
            return
        self._collapsed_at = time.monotonic()
        self.apply_settings(self._settings.with_changes(collapsed=True))

    def toggle(self) -> None:
        self.collapse() if not self.collapsed else self.expand()

    def _apply_geometry(self) -> None:
        settings = self._settings
        if settings.collapsed:
            size = max(MIN_ICON_SIZE, min(MAX_ICON_SIZE, int(settings.icon_size)))
            self.setFixedSize(size, size)
            self._move_to(settings.icon_x, settings.icon_y, size, size)
        else:
            self.setFixedSize(settings.width, settings.height)
            self._move_to(settings.x, settings.y, settings.width, settings.height)

    def _available_rect(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return 0, 0, 1920, 1080
        geometry = screen.availableGeometry()
        return geometry.x(), geometry.y(), geometry.width(), geometry.height()

    def _move_to(self, x, y, width, height) -> None:
        offset_x, offset_y, screen_width, screen_height = self._available_rect()
        if x is None and y is None:
            # 没存过位置：右下角（工作区内，任务栏上方）
            safe_x, safe_y = default_position(
                width, height, screen_width, screen_height,
                offset_x=offset_x, offset_y=offset_y,
            )
        else:
            # 夹到工作区但不留边距：以图标为中心展开时才能真的贴着边缘
            safe_x, safe_y = clamp_position(
                x, y, width, height, screen_width, screen_height,
                margin=0, offset_x=offset_x, offset_y=offset_y,
            )
        self.move(safe_x, safe_y)

    def remember_geometry(self) -> None:
        """拖动/缩放结束后落盘。图标态只记图标位置，别把面板尺寸写坏。"""
        position = self.pos()
        if self.collapsed:
            merged = self._settings.with_changes(icon_x=position.x(), icon_y=position.y())
        else:
            merged = self._settings.with_changes(
                x=position.x(), y=position.y(), width=self.width(), height=self.height()
            )
        self.apply_settings(merged)

    # ── 门控与数据刷新 ────────────────────────────────────
    def refresh(self) -> None:
        """一拍：先决定该不该显示，再更新队列内容。"""
        try:
            settings = self._settings
            hwnd = 0
            if self._hwnd_provider is not None:
                try:
                    hwnd = int(self._hwnd_provider() or 0)
                except Exception:
                    hwnd = 0
            if not hwnd:
                hwnd = game_hwnd()
            foreground = game_in_foreground(hwnd)
            self._hidden_by_game = foreground
            # 需求 1：游戏在前台时永远不显示；需求 2：后台时按开关决定
            should_show = bool(settings.enabled and settings.show_in_background and not foreground)
            if should_show and not self.isVisible():
                self.show()  # WA_ShowWithoutActivating：不抢焦点
            elif not should_show and self.isVisible():
                self.hide()
            if not should_show:
                return
            # 分辨率/任务栏变了也要把窗口推回工作区（图标不能被任务栏盖住）
            self._ensure_on_screen()
            if not self.collapsed:
                self._reload_rows()
            self.update()
        except Exception:
            if self.isVisible():
                self.hide()

    def _ensure_on_screen(self) -> None:
        """把窗口夹回「工作区」——``availableGeometry()`` 已排除任务栏。"""
        offset_x, offset_y, screen_width, screen_height = self._available_rect()
        max_x = offset_x + screen_width - self.width()
        max_y = offset_y + screen_height - self.height()
        new_x = max(offset_x, min(self.pos().x(), max_x))
        new_y = max(offset_y, min(self.pos().y(), max_y))
        if (new_x, new_y) == (self.pos().x(), self.pos().y()):
            return
        self.move(new_x, new_y)
        self._save_position(new_x, new_y)

    def _save_position(self, x: int, y: int) -> None:
        """只记位置（不动大小），避免收起状态下把面板尺寸写坏。"""
        if self.collapsed:
            merged = self._settings.with_changes(icon_x=x, icon_y=y)
        else:
            merged = self._settings.with_changes(x=x, y=y)
        self._settings = merged
        save_settings(merged)

    def _reload_rows(self) -> None:
        self._history = queue_eta.load_history()
        current, queued = queue_snapshot()
        self._rows = queue_eta.build_rows(self._history, current, queued)
        self._total_text = queue_eta.total_text(self._history, self._rows)

    # ── 绘制 ─────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        settings = self._settings
        rect = QRectF(self.rect())
        if settings.collapsed:
            self._paint_icon(painter, rect)
            painter.end()
            return
        radius = float(settings.corner_radius)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, QColor(DEFAULT_BG_COLOR))

        # 标题行：图标 + 「任务队列」
        icon = self._icon_pixmap()
        title_x = 12
        if icon is not None:
            size = TITLE_HEIGHT - 12
            painter.drawPixmap(QRect(12, 6, size, size), icon)
            title_x = 12 + size + 8
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(
            QRectF(title_x, 6, self.width() - title_x - 12, TITLE_HEIGHT - 12),
            Qt.AlignLeft | Qt.AlignVCenter,
            _tr("任务队列"),
        )
        painter.setPen(QPen(QColor("#2A2A33"), 1))
        painter.drawLine(12, TITLE_HEIGHT, self.width() - 12, TITLE_HEIGHT)

        # 行
        font.setBold(False)
        font.setPointSize(9)
        painter.setFont(font)
        rows = self._rows[: max(1, int(settings.max_rows))]
        y = TITLE_HEIGHT + 8
        if not rows:
            painter.setPen(QColor("#8A8A96"))
            painter.drawText(
                QRectF(14, y, self.width() - 28, 20),
                Qt.AlignLeft | Qt.AlignVCenter,
                _tr("队列空闲"),
            )
            y += 22
        for row in rows:
            if y > self.height() - 34:
                break
            color = "#8FD18F" if row["state"] == "running" else "#E6E6EA"
            painter.setPen(QColor(color))
            prefix = "▶ " if row["state"] == "running" else ""
            painter.drawText(
                QRectF(14, y, self.width() - 150, 18),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{prefix}{row['name']}",
            )
            painter.setPen(QColor("#B9B9C0"))
            painter.drawText(
                QRectF(self.width() - 136, y, 122, 18),
                Qt.AlignRight | Qt.AlignVCenter,
                row["eta_text"],
            )
            y += 20
        if len(self._rows) > len(rows):
            painter.setPen(QColor("#8A8A96"))
            painter.drawText(
                QRectF(14, y, self.width() - 28, 18),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"+{len(self._rows) - len(rows)} " + _tr("项"),
            )

        # 底部合计
        painter.setPen(QColor("#2A2A33"))
        painter.drawLine(12, self.height() - 30, self.width() - 12, self.height() - 30)
        painter.setPen(QColor("#E6E6EA"))
        painter.drawText(
            QRectF(14, self.height() - 28, self.width() - 28, 22),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"{_tr('合计')}: {self._total_text}",
        )
        painter.end()

    def _icon_pixmap(self) -> QPixmap | None:
        for candidate in (Path(self._settings.icon_path), Path.cwd() / self._settings.icon_path):
            try:
                if candidate.is_file():
                    pixmap = QPixmap(str(candidate))
                    if not pixmap.isNull():
                        return pixmap
            except OSError:
                continue
        return None

    def _paint_icon(self, painter: QPainter, rect: QRectF) -> None:
        """图标态：只画图标（圆角裁切），没有任何队列文字。"""
        radius = max(6.0, rect.width() * 0.22)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        icon = self._icon_pixmap()
        if icon is None:
            painter.fillPath(path, QColor("#3A7BD5"))
            return
        painter.save()
        painter.setClipPath(path)
        size = int(min(rect.width(), rect.height()))
        scaled = icon.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap(
            int(rect.x() + (rect.width() - scaled.width()) / 2),
            int(rect.y() + (rect.height() - scaled.height()) / 2),
            scaled,
        )
        painter.restore()

    # ── 鼠标：单击展开 / 双击收回 / 拖动 / 右下角改大小 ────
    def _on_click(self) -> None:
        """左键单击：图标态 → 展开面板（面板态单击不做事，留给拖动）。"""
        if not self.collapsed:
            return
        # 刚收回图标时可能还会飘来一次「单击」（双击序列里多出来的那次），别立刻又展开
        if time.monotonic() - getattr(self, "_collapsed_at", 0.0) < EXPAND_GUARD_SECONDS:
            return
        self.expand()

    def _on_double_click(self) -> None:
        """左键双击：面板态 → 收回图标态。

        Qt 的双击序列是 ``press → release → dblclick → release``：``dblclick`` 之后还跟着
        一次 ``release``。收回图标必须把那次 release 吞掉，否则它会被当成「单击图标」
        立刻又展开——这正是「双击后瞬间又弹回面板」的原因。

        ``EXPAND_GUARD_SECONDS`` 内忽略双击：手快双击图标时会先触发一次单击展开，
        紧跟着的双击不该立刻收回去。
        """
        if self.collapsed:
            return
        if time.monotonic() - getattr(self, "_expanded_at", 0.0) < EXPAND_GUARD_SECONDS:
            return
        self._suppress_next_release = True
        self.collapse()

    def _handle_release(self, moved: bool, resizing: bool) -> None:
        """一次左键松手的语义：拖动/缩放 → 存位置；纯点击 → 按形态切换。"""
        if self._suppress_next_release:
            self._suppress_next_release = False
            return
        if resizing or moved:
            self.remember_geometry()
            return
        self._on_click()

    def _clamped_position(self, point: QPoint) -> QPoint:
        """拖动时就把窗口夹在工作区里——不能拖到任务栏底下。"""
        offset_x, offset_y, screen_width, screen_height = self._available_rect()
        x = max(offset_x, min(point.x(), offset_x + screen_width - self.width()))
        y = max(offset_y, min(point.y(), offset_y + screen_height - self.height()))
        return QPoint(x, y)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._press_pos = event.globalPosition().toPoint()
        self._drag_origin = self.pos()
        if not self.collapsed and self._near_grip(self._press_pos):
            self._resize_origin = (self._press_pos, self.width(), self.height())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is None:
            return
        current = event.globalPosition().toPoint()
        if self._resize_origin is not None:
            origin, start_width, start_height = self._resize_origin
            delta = current - origin
            self.setFixedSize(
                max(MIN_WIDTH, min(MAX_WIDTH, start_width + delta.x())),
                max(MIN_HEIGHT, min(MAX_HEIGHT, start_height + delta.y())),
            )
            self.update()
            return
        if self._drag_origin is not None:
            self.move(self._clamped_position(self._drag_origin + (current - self._press_pos)))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        moved = False
        if self._press_pos is not None:
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength() > 4
        resizing = self._resize_origin is not None
        self._press_pos = None
        self._drag_origin = None
        self._resize_origin = None
        self._handle_release(moved, resizing)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_double_click()
            return
        super().mouseDoubleClickEvent(event)

    def _near_grip(self, point: QPoint) -> bool:
        local = self.mapFromGlobal(point)
        return local.x() >= self.width() - GRIP_SIZE and local.y() >= self.height() - GRIP_SIZE

    def closeEvent(self, event):
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
        super().closeEvent(event)
