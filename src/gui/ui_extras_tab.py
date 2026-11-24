"""「界面与提醒」页：关闭行为、提醒、任务栏进度、全局快捷键。

这些都是「一次设好就不常改」的开关，所以集中一页、写完立刻落盘，
不需要重启（线程每轮都会重新读设置）。
"""

from __future__ import annotations

from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    SpinBox,
)

from src.core.close_behavior import (
    CHOICE_CLOSE,
    CHOICE_TRAY,
    CloseBehavior,
)
from src.core.close_behavior import (
    load_settings as load_close_behavior,
)
from src.core.close_behavior import (
    save_settings as save_close_behavior,
)
from src.core.hotkeys import (
    ACTION_LABELS,
    DEFAULT_HOTKEYS,
    HotkeyError,
    build_plan,
    load_hotkeys,
    save_hotkeys,
    validate,
)
from src.core.reminders import ReminderSettings
from src.core.reminders import load_settings as load_reminders
from src.core.reminders import save_settings as save_reminders
from src.core.taskbar import load_enabled as load_taskbar_enabled
from src.core.taskbar import save_enabled as save_taskbar_enabled

PREBUILD_DELAY_MS = 3000

CLOSE_MODES = ("ask", "close", "tray")


class UiExtrasTab(CustomTab):
    """关闭行为 / 提醒 / 任务栏 / 快捷键。"""

    def __init__(self):
        super().__init__()
        self._built = False
        self._build_scheduled = False

        self.close_mode: ComboBox | None = None
        self.reminder_enabled: CheckBox | None = None
        self.daily_enabled: CheckBox | None = None
        self.daily_hour: SpinBox | None = None
        self.stamina_enabled: CheckBox | None = None
        self.stamina_interval: SpinBox | None = None
        self.taskbar_enabled: CheckBox | None = None
        self.hotkey_edits: dict[str, QLineEdit] = {}
        self.status_label: BodyLabel | None = None

        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._schedule_build)

    # ── CustomTab 元信息 ─────────────────────────────────
    @property
    def name(self):
        return self.tr("界面与提醒")

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return True

    @property
    def icon(self):
        return FluentIcon.CHAT

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
        self.add_card(self.tr("关闭行为"), self._build_close_card())
        self.add_card(self.tr("提醒"), self._build_reminder_card())
        self.add_card(self.tr("任务栏进度与快捷键"), self._build_labour_card())
        QTimer.singleShot(0, self.reload)

    def _build_close_card(self) -> QWidget:
        card = QWidget()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(BodyLabel(self.tr("点关闭按钮时")))
        self.close_mode = ComboBox()
        self.close_mode.addItems([self.tr("每次都问"), self.tr("直接关闭"), self.tr("最小化到托盘")])
        layout.addWidget(self.close_mode)
        apply = PrimaryPushButton(FluentIcon.SAVE, self.tr("保存"))
        apply.clicked.connect(self._save_close)
        layout.addWidget(apply)
        layout.addStretch(1)
        return card

    def _build_reminder_card(self) -> QWidget:
        card = QWidget()
        layout = QGridLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.reminder_enabled = CheckBox(self.tr("启用提醒"))
        layout.addWidget(self.reminder_enabled, 0, 0)
        self.daily_enabled = CheckBox(self.tr("每日重置提醒"))
        layout.addWidget(self.daily_enabled, 0, 1)
        layout.addWidget(BodyLabel(self.tr("重置小时")), 0, 2)
        self.daily_hour = SpinBox()
        self.daily_hour.setRange(0, 23)
        layout.addWidget(self.daily_hour, 0, 3)

        self.stamina_enabled = CheckBox(self.tr("体力提醒"))
        layout.addWidget(self.stamina_enabled, 1, 0)
        layout.addWidget(BodyLabel(self.tr("间隔（分钟）")), 1, 1)
        self.stamina_interval = SpinBox()
        self.stamina_interval.setRange(0, 720)
        layout.addWidget(self.stamina_interval, 1, 2)

        save = PrimaryPushButton(FluentIcon.SAVE, self.tr("保存提醒设置"))
        save.clicked.connect(self._save_reminders)
        layout.addWidget(save, 1, 3)
        return card

    def _build_labour_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.taskbar_enabled = CheckBox(self.tr("在任务栏显示进度"))
        row.addWidget(self.taskbar_enabled)
        row.addStretch(1)
        save_taskbar = PushButton(FluentIcon.SAVE, self.tr("保存"))
        save_taskbar.clicked.connect(self._save_taskbar)
        row.addWidget(save_taskbar)
        layout.addLayout(row)

        hotkey_row = QGridLayout()
        hotkey_row.setContentsMargins(0, 0, 0, 0)
        hotkey_row.setSpacing(6)
        for index, (action, label) in enumerate(ACTION_LABELS.items()):
            hotkey_row.addWidget(BodyLabel(self.tr(label)), index, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(self.tr("如 ctrl+alt+p"))
            self.hotkey_edits[action] = edit
            hotkey_row.addWidget(edit, index, 1)
        layout.addLayout(hotkey_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        check = PushButton(FluentIcon.CHECKBOX, self.tr("校验快捷键"))
        check.clicked.connect(self._check_hotkeys)
        buttons.addWidget(check)
        save_hotkeys_button = PrimaryPushButton(FluentIcon.SAVE, self.tr("保存快捷键"))
        save_hotkeys_button.clicked.connect(self._save_hotkeys)
        buttons.addWidget(save_hotkeys_button)
        restore = PushButton(FluentIcon.HISTORY, self.tr("恢复默认"))
        restore.clicked.connect(self._restore_hotkeys)
        buttons.addWidget(restore)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status_label = BodyLabel(
            self.tr("快捷键在程序启动时注册；改完需要重启生效。F9–F12 属于框架的启动/停止，不能再绑定。")
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return card

    # ── 载入 ─────────────────────────────────────────────
    def reload(self):
        if not self._built:
            return
        behavior = load_close_behavior()
        if self.close_mode is not None:
            if not behavior.ask:
                self.close_mode.setCurrentIndex(1)
            elif behavior.remember == CHOICE_TRAY:
                self.close_mode.setCurrentIndex(2)
            elif behavior.remember == CHOICE_CLOSE:
                self.close_mode.setCurrentIndex(1)
            else:
                self.close_mode.setCurrentIndex(0)

        reminders = load_reminders()
        if self.reminder_enabled is not None:
            self.reminder_enabled.setChecked(reminders.enabled)
        if self.daily_enabled is not None:
            self.daily_enabled.setChecked(reminders.daily_reset_enabled)
        if self.daily_hour is not None:
            self.daily_hour.setValue(int(reminders.daily_reset_hour))
        if self.stamina_enabled is not None:
            self.stamina_enabled.setChecked(reminders.stamina_enabled)
        if self.stamina_interval is not None:
            self.stamina_interval.setValue(int(reminders.stamina_interval_minutes))

        if self.taskbar_enabled is not None:
            self.taskbar_enabled.setChecked(load_taskbar_enabled())
        hotkeys = load_hotkeys()
        for action, edit in self.hotkey_edits.items():
            edit.setText(hotkeys.get(action, DEFAULT_HOTKEYS.get(action, "")))
        self._set_status(self.tr("设置已载入"))

    # ── 保存 ─────────────────────────────────────────────
    def _save_close(self):
        index = self.close_mode.currentIndex() if self.close_mode else 0
        if index == 0:
            behavior = CloseBehavior(ask=True, remember="")
        elif index == 1:
            behavior = CloseBehavior(ask=False, remember="")
        else:
            behavior = CloseBehavior(ask=True, remember=CHOICE_TRAY)
        try:
            path = save_close_behavior(behavior)
        except Exception as exc:
            self._notify(self.tr("保存失败：{error}").format(error=exc), error=True)
            return
        self._set_status(self.tr("关闭行为已保存到 {path}").format(path=path))
        self._notify(self.tr("关闭行为已保存"))

    def _save_reminders(self):
        try:
            settings = ReminderSettings(
                enabled=bool(self.reminder_enabled and self.reminder_enabled.isChecked()),
                daily_reset_enabled=bool(self.daily_enabled and self.daily_enabled.isChecked()),
                daily_reset_hour=self.daily_hour.value() if self.daily_hour else 4,
                stamina_enabled=bool(self.stamina_enabled and self.stamina_enabled.isChecked()),
                stamina_interval_minutes=self.stamina_interval.value() if self.stamina_interval else 0,
            )
            path = save_reminders(settings)
        except Exception as exc:
            self._notify(self.tr("保存失败：{error}").format(error=exc), error=True)
            return
        self._set_status(self.tr("提醒设置已保存到 {path}（30 秒内生效）").format(path=path))
        self._notify(self.tr("提醒设置已保存"))

    def _save_taskbar(self):
        enabled = bool(self.taskbar_enabled and self.taskbar_enabled.isChecked())
        try:
            save_taskbar_enabled(enabled)
        except Exception as exc:
            self._notify(self.tr("保存失败：{error}").format(error=exc), error=True)
            return
        # 悬浮窗的开关在「悬浮窗」页（configs/float_window.json），这里只管任务栏进度
        self._notify(
            self.tr("任务栏进度已{state}").format(state=self.tr("开启") if enabled else self.tr("关闭"))
        )

    def _collect_hotkeys(self) -> dict[str, str]:
        return {action: edit.text().strip() for action, edit in self.hotkey_edits.items()}

    def _check_hotkeys(self) -> bool:
        issues = validate(self._collect_hotkeys())
        if issues:
            self._set_status(self.tr("快捷键有问题：{issues}").format(issues="；".join(issues)))
            self._notify(self.tr("快捷键有问题"), error=True)
            return False
        try:
            build_plan(self._collect_hotkeys())
        except HotkeyError as exc:
            self._set_status(str(exc))
            self._notify(str(exc), error=True)
            return False
        self._set_status(self.tr("快捷键校验通过"))
        return True

    def _save_hotkeys(self):
        if not self._check_hotkeys():
            return
        try:
            path = save_hotkeys(self._collect_hotkeys())
        except Exception as exc:
            self._notify(self.tr("保存失败：{error}").format(error=exc), error=True)
            return
        self._set_status(self.tr("快捷键已保存到 {path}，重启后生效").format(path=path))
        self._notify(self.tr("快捷键已保存"))

    def _restore_hotkeys(self):
        for action, edit in self.hotkey_edits.items():
            edit.setText(DEFAULT_HOTKEYS.get(action, ""))
        self._set_status(self.tr("已填入默认快捷键（记得保存）"))

    # ── 工具 ─────────────────────────────────────────────
    def _set_status(self, text: str):
        if self.status_label is not None:
            self.status_label.setText(text)

    def _notify(self, message: str, error: bool = False):
        try:
            window = self.window()
            factory = InfoBar.error if error else InfoBar.success
            factory(
                title=self.tr("界面与提醒"),
                content=message,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=5000 if error else 3000,
                parent=window,
            )
        except Exception:
            self.logger.info(message)
