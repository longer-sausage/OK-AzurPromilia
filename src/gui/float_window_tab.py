"""「悬浮窗」设置页：开关、图标大小、面板大小、颜色、不透明度（≥10%）、位置复位。

设置写到 ``configs/float_window.json``；保存后**立刻**作用到已经打开的悬浮窗
（通过 ``apply_float_settings`` 回调，找不到窗口也不报错）。
"""

from __future__ import annotations

from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    FluentIcon,
    NavigationItemPosition,
    PushButton,
    Slider,
    SpinBox,
)

from src.core import float_window as fw
from src.core import run_state

PREBUILD_DELAY_MS = 3000


class FloatWindowTab(CustomTab):
    """悬浮窗外观与行为设置。"""

    def __init__(self):
        super().__init__()
        self._built = False
        self._build_scheduled = False
        self._settings = fw.FloatSettings()

        self.enabled_box: CheckBox | None = None
        self.icon_size_box: SpinBox | None = None
        self.panel_width_box: SpinBox | None = None
        self.panel_height_box: SpinBox | None = None
        self.radius_box: SpinBox | None = None
        self.opacity_slider: Slider | None = None
        self.opacity_label: BodyLabel | None = None
        self.color_label: BodyLabel | None = None
        self.show_queue_box: CheckBox | None = None
        self.max_tasks_box: SpinBox | None = None
        self.status: BodyLabel | None = None

        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._schedule_build)

    # ── CustomTab 元信息 ─────────────────────────────────
    @property
    def name(self):
        return self.tr("悬浮窗")

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return True

    @property
    def icon(self):
        return FluentIcon.MIX_VOLUMES

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_build()
        if self._built:
            self.reload()

    # ── 构建 ─────────────────────────────────────────────
    def _schedule_build(self):
        if self._build_scheduled:
            return
        self._build_scheduled = True
        QTimer.singleShot(0, self, self._build)

    def _build(self):
        if self._built:
            return
        self._built = True
        self.add_card(self.tr("悬浮窗"), self._build_card())
        self.add_card(self.tr("当前状态"), self._build_status_card())
        QTimer.singleShot(0, self.reload)

    def _build_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.enabled_box = CheckBox(self.tr("显示悬浮窗（图标可拖动，点一下展开任务队列）"))
        layout.addWidget(self.enabled_box)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(BodyLabel(self.tr("悬浮图标大小")))
        self.icon_size_box = SpinBox()
        self.icon_size_box.setRange(fw.MIN_ICON_SIZE, fw.MAX_ICON_SIZE)
        self.icon_size_box.setSuffix(" px")
        row.addWidget(self.icon_size_box)

        row.addSpacing(12)
        row.addWidget(BodyLabel(self.tr("面板宽度")))
        self.panel_width_box = SpinBox()
        self.panel_width_box.setRange(fw.MIN_PANEL_WIDTH, fw.MAX_PANEL_WIDTH)
        self.panel_width_box.setSuffix(" px")
        row.addWidget(self.panel_width_box)

        row.addWidget(BodyLabel(self.tr("面板高度")))
        self.panel_height_box = SpinBox()
        self.panel_height_box.setRange(fw.MIN_PANEL_HEIGHT, fw.MAX_PANEL_HEIGHT)
        self.panel_height_box.setSuffix(" px")
        row.addWidget(self.panel_height_box)
        row.addStretch(1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(BodyLabel(self.tr("圆角")))
        self.radius_box = SpinBox()
        self.radius_box.setRange(0, 30)
        self.radius_box.setSuffix(" px")
        row2.addWidget(self.radius_box)

        row2.addSpacing(12)
        row2.addWidget(BodyLabel(self.tr("背景颜色")))
        self.color_label = BodyLabel(fw.DEFAULT_BG_COLOR)
        row2.addWidget(self.color_label)
        color_button = PushButton(FluentIcon.PALETTE, self.tr("选择颜色"))
        color_button.clicked.connect(self._pick_color)
        row2.addWidget(color_button)
        row2.addStretch(1)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        row3.addWidget(BodyLabel(self.tr("不透明度")))
        self.opacity_slider = Slider(Qt.Horizontal)
        self.opacity_slider.setRange(int(fw.MIN_OPACITY * 100), int(fw.MAX_OPACITY * 100))
        self.opacity_slider.setMinimumWidth(220)
        self.opacity_slider.valueChanged.connect(self._update_opacity_label)
        row3.addWidget(self.opacity_slider)
        self.opacity_label = BodyLabel("92%")
        row3.addWidget(self.opacity_label)
        row3.addStretch(1)
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.setSpacing(8)
        self.show_queue_box = CheckBox(self.tr("在悬浮窗里显示任务队列"))
        row4.addWidget(self.show_queue_box)
        row4.addWidget(BodyLabel(self.tr("最多显示")))
        self.max_tasks_box = SpinBox()
        self.max_tasks_box.setRange(1, fw.MAX_TASKS_LIMIT)
        row4.addWidget(self.max_tasks_box)
        row4.addWidget(BodyLabel(self.tr("项")))
        row4.addStretch(1)
        layout.addLayout(row4)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        save_button = PushButton(FluentIcon.SAVE, self.tr("保存并应用"))
        save_button.clicked.connect(self._save)
        buttons.addWidget(save_button)
        reset_button = PushButton(FluentIcon.RETURN, self.tr("恢复默认位置与大小"))
        reset_button.clicked.connect(self._reset_position)
        buttons.addWidget(reset_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status = BodyLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        return card

    def _build_status_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        hint = BodyLabel(
            self.tr("悬浮窗显示当前任务与队列里每一项的预计用时；数据来自运行记录与队列执行器。")
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.live_status = BodyLabel("")
        self.live_status.setWordWrap(True)
        layout.addWidget(self.live_status)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_live_status)
        self._status_timer.start(1000)
        return card

    def _refresh_live_status(self):
        label = getattr(self, "live_status", None)
        if label is None:
            return
        try:
            snapshot = run_state.snapshot()
        except Exception:
            snapshot = {}
        task = snapshot.get("task") or self.tr("空闲")
        queue = snapshot.get("queue") or self.tr("无队列")
        label.setText(f"{self.tr('当前任务')}: {task}    {self.tr('队列')}: {queue}")

    # ── 读写 ─────────────────────────────────────────────
    def reload(self):
        self._settings = fw.load_settings()
        if self.enabled_box is not None:
            self.enabled_box.setChecked(self._settings.enabled)
            self.icon_size_box.setValue(self._settings.icon_size)
            self.panel_width_box.setValue(self._settings.panel_width)
            self.panel_height_box.setValue(self._settings.panel_height)
            self.radius_box.setValue(self._settings.corner_radius)
            self.opacity_slider.setValue(round(self._settings.opacity * 100))
            self.color_label.setText(self._settings.bg_color)
            self.show_queue_box.setChecked(self._settings.show_queue)
            self.max_tasks_box.setValue(self._settings.max_tasks)
        self._update_opacity_label(self.opacity_slider.value() if self.opacity_slider else 92)

    def _update_opacity_label(self, value: int):
        if self.opacity_label is not None:
            self.opacity_label.setText(f"{max(int(fw.MIN_OPACITY * 100), int(value))}%")

    def _pick_color(self):
        """用 Qt 自带的取色对话框（qfluentwidgets 的 ColorDialog 没有静态 getColor）。"""
        current = QColor(self._settings.bg_color)
        if not current.isValid():
            current = QColor(fw.DEFAULT_BG_COLOR)
        color = QColorDialog.getColor(current, self, self.tr("选择悬浮窗背景颜色"))
        if color.isValid() and self.color_label is not None:
            self.color_label.setText(f"#{color.red():02x}{color.green():02x}{color.blue():02x}")

    def _collect(self) -> fw.FloatSettings:
        return self._settings.with_changes(
            enabled=bool(self.enabled_box.isChecked()) if self.enabled_box else True,
            icon_size=self.icon_size_box.value() if self.icon_size_box else self._settings.icon_size,
            panel_width=self.panel_width_box.value() if self.panel_width_box else self._settings.panel_width,
            panel_height=self.panel_height_box.value() if self.panel_height_box else self._settings.panel_height,
            corner_radius=self.radius_box.value() if self.radius_box else self._settings.corner_radius,
            opacity=(self.opacity_slider.value() / 100.0) if self.opacity_slider else self._settings.opacity,
            bg_color=self.color_label.text() if self.color_label else self._settings.bg_color,
            show_queue=bool(self.show_queue_box.isChecked()) if self.show_queue_box else True,
            max_tasks=self.max_tasks_box.value() if self.max_tasks_box else self._settings.max_tasks,
        )

    def _save(self):
        settings = self._collect()
        fw.save_settings(settings)
        self._settings = settings
        applied = apply_float_settings(settings)
        message = self.tr("已保存悬浮窗设置") if applied else self.tr("已保存；重启程序后生效")
        if self.status is not None:
            self.status.setText(message)

    def _reset_position(self):
        settings = self._settings.with_changes(
            icon_x=None, icon_y=None, panel_x=None, panel_y=None, collapsed=True
        )
        fw.save_settings(settings)
        self._settings = settings
        apply_float_settings(settings)
        if self.status is not None:
            self.status.setText(self.tr("已恢复默认位置与大小"))


def apply_float_settings(settings: fw.FloatSettings) -> bool:
    """把设置作用到已打开的悬浮窗；返回是否找到窗口。"""
    try:
        from src.gui.float_window import current_window

        target = current_window()
        if target is None:
            return False
        target.apply_settings(settings)
        if settings.enabled:
            target.show()
        else:
            target.hide()
        return True
    except Exception:
        return False
