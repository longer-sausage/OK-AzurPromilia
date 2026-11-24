"""悬浮窗：圆角矩形面板 + 可拖动的悬浮图标。

两种形态，切换是「缩放」语义：

- **图标态**（默认）：圆形/圆角的软件图标，大小可在设置里调（32~160），拖到屏幕任意位置；
  **点一下**展开成面板；
- **面板态**：圆角矩形，显示当前任务、进度、队列里每项「约 N 分钟」以及总剩余；
  可拖动、可拖右下角改大小；点「收起」或双击标题回到图标态。

实现约束：

- 无边框 + 置顶 + 不抢焦点（``Tool``/``WA_ShowWithoutActivating``），不打断游戏；
- 背景色与不透明度来自 :mod:`src.core.float_window`（不透明度下限 10%），
  面板用 ``paintEvent`` 画圆角，图标态用遮罩裁成圆角；
- 位置/大小/形态都写回 ``configs/float_window.json``，重启后原地复原；
- 每 ``poll_ms`` 从 :mod:`src.core.run_state` 取一次快照；读不到就当空闲。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from src.core import float_window as fw
from src.core import run_state

REPO_ROOT = Path(__file__).resolve().parents[2]
GRIP_SIZE = 14

_CURRENT: FloatWindow | None = None


def set_current_window(window: FloatWindow | None) -> None:
    """记录当前打开的悬浮窗（设置页保存后要立刻作用到它）。"""
    global _CURRENT
    _CURRENT = window


def current_window() -> FloatWindow | None:
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


def load_icon_pixmap(icon_path: str) -> QPixmap:
    """读软件图标；相对路径按仓库根解析，读不到就画一个占位圆。"""
    candidates = [Path(icon_path), REPO_ROOT / icon_path]
    for candidate in candidates:
        try:
            if candidate.is_file():
                pixmap = QPixmap(str(candidate))
                if not pixmap.isNull():
                    return pixmap
        except OSError:
            continue
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#3A7BD5"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(8, 8, 112, 112)
    painter.end()
    return pixmap


class FloatWindow(QWidget):
    """悬浮窗（图标态 / 面板态）。"""

    settings_changed = Signal(object)

    def __init__(self, settings: fw.FloatSettings | None = None, parent=None):
        super().__init__(parent)
        self._settings = settings or fw.load_settings()
        self._collapsed = bool(self._settings.collapsed)
        self._press_pos: QPoint | None = None
        self._drag_origin: QPoint | None = None
        self._resize_origin: tuple[QPoint, int, int] | None = None
        self._snapshot: dict = {}

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self.apply_settings(self._settings, persist=False)
        self._timer.start(self._settings.poll_ms)
        self.refresh()

    # ── 设置 ─────────────────────────────────────────────
    @property
    def settings(self) -> fw.FloatSettings:
        return self._settings

    def apply_settings(self, settings: fw.FloatSettings, persist: bool = True) -> None:
        self._settings = settings
        self._collapsed = bool(settings.collapsed)
        self.setWindowOpacity(max(fw.MIN_OPACITY, float(settings.opacity)))
        if self._timer.isActive():
            self._timer.start(settings.poll_ms)
        self._apply_geometry()
        if persist:
            fw.save_settings(self._settings)
        self.settings_changed.emit(settings)
        self.update()

    def _apply_geometry(self) -> None:
        if self._collapsed:
            size = self._settings.icon_size
            self.setFixedSize(size, size)
            self._move_to(self._settings.icon_x, self._settings.icon_y, size, size)
        else:
            width = self._settings.panel_width
            height = self._settings.panel_height
            self.setFixedSize(width, height)
            self._move_to(self._settings.panel_x, self._settings.panel_y, width, height)

    def _available_rect(self):
        """屏幕**可用区域**（已排除任务栏）；任务栏在左/上时原点不是 (0,0)。"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return 0, 0, 1920, 1080
        geometry = screen.availableGeometry()
        return geometry.x(), geometry.y(), geometry.width(), geometry.height()

    def _move_to(self, x: int | None, y: int | None, width: int, height: int) -> None:
        offset_x, offset_y, screen_width, screen_height = self._available_rect()
        if x is None and y is None:
            # 首次显示：右下角，并让开通栏通知区（见 fw.default_position 的说明）
            safe_x, safe_y = fw.default_position(
                width, height, screen_width, screen_height,
                offset_x=offset_x, offset_y=offset_y,
            )
        else:
            safe_x, safe_y = fw.clamp_position(
                x, y, width, height, screen_width, screen_height,
                margin=fw.DEFAULT_MARGIN, offset_x=offset_x, offset_y=offset_y,
            )
        self.move(safe_x, safe_y)

    def remember_geometry(self) -> None:
        """把当前位置写回设置（拖动/缩放结束时调）。"""
        position = self.pos()
        if self._collapsed:
            merged = self._settings.with_changes(icon_x=position.x(), icon_y=position.y())
        else:
            merged = self._settings.with_changes(
                panel_x=position.x(),
                panel_y=position.y(),
                panel_width=self.width(),
                panel_height=self.height(),
            )
        self.apply_settings(merged)

    # ── 形态切换 ─────────────────────────────────────────
    def expand(self) -> None:
        merged = self._settings.with_changes(collapsed=False)
        self.apply_settings(merged)

    def collapse(self) -> None:
        merged = self._settings.with_changes(collapsed=True)
        self.apply_settings(merged)

    def toggle(self) -> None:
        self.collapse() if not self._collapsed else self.expand()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    # ── 数据刷新 ─────────────────────────────────────────
    def refresh(self) -> None:
        try:
            self._snapshot = run_state.snapshot(run_log=_default_run_log())
        except Exception:
            self._snapshot = {}
        if not self._collapsed:
            self.update()

    # ── 绘制 ─────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = float(self._settings.corner_radius)

        if self._collapsed:
            pixmap = load_icon_pixmap(self._settings.icon_path)
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            painter.setClipPath(path)
            scaled = pixmap.scaled(
                self.width(), self.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            painter.drawPixmap(
                (self.width() - scaled.width()) // 2,
                (self.height() - scaled.height()) // 2,
                scaled,
            )
            painter.end()
            return

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._settings.bg_color))
        painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(QColor("#FFFFFF"))
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        title = fw.title_text(self._snapshot)
        painter.drawText(
            QRectF(12, 8, self.width() - 24, 24),
            Qt.AlignLeft | Qt.AlignVCenter,
            title,
        )

        font.setBold(False)
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QColor("#B9B9C0"))
        painter.drawText(
            QRectF(12, 32, self.width() - 24, 20),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"{_tr('预计剩余')}: {fw.remaining_text(self._snapshot)}",
        )

        y = 58
        if self._settings.show_queue:
            rows = fw.queue_rows(self._snapshot, self._settings.max_tasks)
            painter.setPen(QColor("#E6E6EA"))
            for row in rows:
                if y > self.height() - 44:
                    break
                state = row["state"]
                color = "#8FD18F" if state == "完成" else ("#E07070" if state == "失败" else "#E6E6EA")
                painter.setPen(QColor(color))
                painter.drawText(
                    QRectF(14, y, self.width() - 28, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    f"{row['name']} · {row['eta_text']}",
                )
                y += 20
        painter.end()

    # ── 鼠标：拖动 / 点击展开 / 右下角缩放 ────────────────
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._press_pos = event.globalPosition().toPoint()
        self._drag_origin = self.pos()
        if not self._collapsed and self._near_grip(self._press_pos):
            self._resize_origin = (
                self._press_pos,
                self.width(),
                self.height(),
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is None:
            return
        current = event.globalPosition().toPoint()
        if self._resize_origin is not None:
            origin, start_width, start_height = self._resize_origin
            delta = current - origin
            width = max(fw.MIN_PANEL_WIDTH, min(fw.MAX_PANEL_WIDTH, start_width + delta.x()))
            height = max(fw.MIN_PANEL_HEIGHT, min(fw.MAX_PANEL_HEIGHT, start_height + delta.y()))
            self.setFixedSize(width, height)
            self.update()
            return
        if self._drag_origin is not None:
            self.move(self._drag_origin + (current - self._press_pos))

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

        if resizing or (moved and not self._collapsed) or (moved and self._collapsed):
            self.remember_geometry()
        elif not moved:
            self.toggle()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.collapse()
        super().mouseDoubleClickEvent(event)

    def _near_grip(self, point: QPoint) -> bool:
        local = self.mapFromGlobal(point)
        return local.x() >= self.width() - GRIP_SIZE and local.y() >= self.height() - GRIP_SIZE

    def closeEvent(self, event):
        with_ = getattr(self, "_timer", None)
        if with_ is not None:
            with_.stop()
        super().closeEvent(event)


def _default_run_log():
    try:
        from ok.util.file import get_relative_path

        from src.core.ledger import RunLog

        return RunLog(get_relative_path("runs.jsonl"))
    except Exception:
        return None
