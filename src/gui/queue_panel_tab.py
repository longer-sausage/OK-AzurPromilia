"""「队列悬浮窗」设置页（ok-script 3.x / v0.3.7 版本）。

控件用 3.x 自带的 ``ok.ui.qt.kit``；窗口与逻辑在 ``src/gui/queue_panel.py``，
预计耗时在 ``src/core/queue_eta.py``。安装步骤见同目录 README。
"""

from __future__ import annotations

from ok.ui.qt.kit.buttons import Button
from ok.ui.qt.kit.cards import SettingGroup, SettingRow
from ok.ui.qt.kit.inputs import NumberBox
from ok.ui.qt.kit.labels import Label
from ok.ui.qt.kit.switch import Switch
from ok.ui.qt.widget.CustomTab import CustomTab
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from src.core import queue_eta
from src.gui import queue_panel

STATUS_REFRESH_MS = 1000


class QueuePanelTab(CustomTab):
    """队列悬浮窗的外观与开关。"""

    @property
    def name(self):
        return self.tr("队列悬浮窗")

    def __init__(self):
        super().__init__()
        self._settings = queue_panel.load_settings()
        try:
            from src.patches.queue_panel_patch import ensure_panel

            ensure_panel()
        except Exception:
            pass
        self._build()
        self.reload()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_live_status)
        self._status_timer.start(STATUS_REFRESH_MS)

    # ── 构建 ─────────────────────────────────────────────
    def _build(self) -> None:
        self.add_card(self.tr("队列悬浮窗"), self._build_card())
        self.add_card(self.tr("位置与大小"), self._build_geometry_card())
        self.add_card(self.tr("当前状态"), self._build_status_card())

    def _build_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.enabled_switch = Switch()
        self.background_switch = Switch()
        self.opacity_box = NumberBox(
            round(queue_panel.DEFAULT_OPACITY * 100),
            round(queue_panel.MIN_OPACITY * 100),
            round(queue_panel.MAX_OPACITY * 100),
        )
        self.icon_label = Label(queue_panel.DEFAULT_ICON_PATH, "body")
        self.icon_button = Button(self.tr("选择图标"), variant="secondary")
        self.icon_button.clicked.connect(self._pick_icon)
        self.icon_size_box = NumberBox(
            queue_panel.DEFAULT_ICON_SIZE, queue_panel.MIN_ICON_SIZE, queue_panel.MAX_ICON_SIZE
        )

        icon_control = QWidget()
        icon_layout = QHBoxLayout(icon_control)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(10)
        icon_layout.addWidget(self.icon_label)
        icon_layout.addWidget(self.icon_button)

        group = SettingGroup()
        group.add_row(SettingRow(self.tr("启用队列悬浮窗"), control=self.enabled_switch))
        group.add_row(
            SettingRow(
                self.tr("游戏在后台时显示（游戏在前台时永远不显示）"),
                self.tr("显示正在执行和排队中的任务；每项耗时取最近 5 次实际用时的平均值。"),
                self.background_switch,
            )
        )
        group.add_row(
            SettingRow(self.tr("不透明度"), control=self._unit(self.opacity_box, "%"), indent=True)
        )
        group.add_row(SettingRow(self.tr("图标"), control=icon_control))
        group.add_row(
            SettingRow(self.tr("图标大小"), control=self._unit(self.icon_size_box, "px"), indent=True)
        )
        group.add_row(
            SettingRow(
                self.tr("左键单击图标展开队列，双击面板收回图标。"),
                self.tr("图标会一直留在任务栏上方，不会被任务栏盖住。"),
            )
        )
        layout.addWidget(group)
        return card

    def _build_geometry_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        hint = Label(
            self.tr("悬停窗口可以拖动，拖右下角改大小，松手自动保存。"), "secondary", word_wrap=True
        )
        layout.addWidget(hint)

        self.width_box = NumberBox(queue_panel.DEFAULT_WIDTH, queue_panel.MIN_WIDTH, queue_panel.MAX_WIDTH)
        self.height_box = NumberBox(queue_panel.DEFAULT_HEIGHT, queue_panel.MIN_HEIGHT, queue_panel.MAX_HEIGHT)
        self.radius_box = NumberBox(
            queue_panel.DEFAULT_CORNER_RADIUS,
            queue_panel.MIN_CORNER_RADIUS,
            queue_panel.MAX_CORNER_RADIUS,
        )
        self.rows_box = NumberBox(queue_panel.DEFAULT_MAX_ROWS, 1, queue_panel.MAX_ROWS_LIMIT)

        group = SettingGroup()
        group.add_row(SettingRow(self.tr("宽度"), control=self._unit(self.width_box, "px")))
        group.add_row(SettingRow(self.tr("高度"), control=self._unit(self.height_box, "px")))
        group.add_row(SettingRow(self.tr("圆角"), control=self._unit(self.radius_box, "px")))
        group.add_row(SettingRow(self.tr("最多显示"), control=self._unit(self.rows_box, self.tr("项"))))
        layout.addWidget(group)

        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(10)
        save_button = Button(self.tr("保存并应用"), variant="primary")
        save_button.clicked.connect(self._save)
        reset_button = Button(self.tr("恢复默认位置与大小"), variant="secondary")
        reset_button.clicked.connect(self._reset_geometry)
        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(reset_button)
        buttons_layout.addStretch(1)
        layout.addWidget(buttons)

        self.status = Label("", "secondary", word_wrap=True)
        layout.addWidget(self.status)
        return card

    def _build_status_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(
            Label(self.tr("队列里的耗时来自历史记录：每个任务保留最近 5 次，不足 5 次按已有次数平均。"),
                  "secondary", word_wrap=True)
        )
        self.live_status = Label("", "body", word_wrap=True)
        layout.addWidget(self.live_status)
        return card

    @staticmethod
    def _unit(box: NumberBox, unit: str) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(box)
        layout.addWidget(Label(unit, "secondary"))
        layout.addStretch(1)
        return holder

    # ── 交互 ─────────────────────────────────────────────
    def _pick_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("选择图标文件"), "", "Images (*.png *.jpg *.jpeg *.bmp *.ico)"
        )
        if path:
            self.icon_label.setText(path)

    def _refresh_live_status(self) -> None:
        label = getattr(self, "live_status", None)
        if label is None:
            return
        panel = queue_panel.current_panel()
        if panel is None:
            label.setText(self.tr("悬浮窗尚未创建"))
            return
        current, queued = queue_panel.queue_snapshot()
        history = queue_eta.load_history()
        rows = queue_eta.build_rows(history, current, queued)
        hidden = getattr(panel, "_hidden_by_game", False)
        state = self.tr("游戏在前台，已隐藏") if hidden else (
            self.tr("显示中") if panel.isVisible() else self.tr("未显示")
        )
        label.setText(
            f"{self.tr('状态')}: {state}    {self.tr('队列')}: {len(rows)} {self.tr('项')}    "
            f"{self.tr('合计')}: {queue_eta.total_text(history, rows)}"
        )

    # ── 读写 ─────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        self._settings = queue_panel.load_settings()
        settings = self._settings
        self.enabled_switch.setChecked(settings.enabled)
        self.background_switch.setChecked(settings.show_in_background)
        self.opacity_box.setValue(round(settings.opacity * 100))
        self.icon_label.setText(settings.icon_path)
        self.icon_size_box.setValue(settings.icon_size)
        self.width_box.setValue(settings.width)
        self.height_box.setValue(settings.height)
        self.radius_box.setValue(settings.corner_radius)
        self.rows_box.setValue(settings.max_rows)

    def _collect(self) -> queue_panel.PanelSettings:
        return self._settings.with_changes(
            enabled=bool(self.enabled_switch.isChecked()),
            show_in_background=bool(self.background_switch.isChecked()),
            opacity=self.opacity_box.value() / 100.0,
            icon_path=self.icon_label.text(),
            icon_size=int(self.icon_size_box.value()),
            width=int(self.width_box.value()),
            height=int(self.height_box.value()),
            corner_radius=int(self.radius_box.value()),
            max_rows=int(self.rows_box.value()),
        )

    def _save(self) -> None:
        settings = self._collect()
        queue_panel.save_settings(settings)
        self._settings = settings
        applied = apply_panel_settings(settings)
        self.status.setText(
            self.tr("已保存队列悬浮窗设置") if applied else self.tr("已保存；重启程序后生效")
        )

    def _reset_geometry(self) -> None:
        """两种形态的位置一起清掉，下次都回到右下角默认位置。"""
        settings = self._settings.with_changes(
            x=None, y=None, icon_x=None, icon_y=None,
            width=queue_panel.DEFAULT_WIDTH,
            height=queue_panel.DEFAULT_HEIGHT,
        )
        queue_panel.save_settings(settings)
        self._settings = settings
        self.width_box.setValue(settings.width)
        self.height_box.setValue(settings.height)
        apply_panel_settings(settings)
        self.status.setText(self.tr("已恢复默认位置与大小"))


def apply_panel_settings(settings: queue_panel.PanelSettings) -> bool:
    """把设置作用到已创建的悬浮窗；返回是否找到它。"""
    panel = queue_panel.current_panel()
    if panel is None:
        return False
    try:
        panel.apply_settings(settings)
        return True
    except Exception:
        return False
