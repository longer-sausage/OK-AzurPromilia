"""关闭行为补丁：把 ``MainWindow.closeEvent`` 换成「问一下 / 记住选择」。

只改一件事：用户点 × 时候弹一个三选一（完全关闭 / 收到托盘 / 取消），
可勾选记住（只记 close / tray）。原有行为全部保留：

- 退出流程中（``exit_event`` 已置位）直接走框架逻辑；
- 选择「完全关闭」时仍调用**原来的** ``closeEvent``，看门狗、清理、托盘图标回收都不变；
- 选「收到托盘」只 ``hide()``；
- 任何异常都退回框架逻辑，不让补丁把关闭流程搞坏。
"""

from __future__ import annotations

from src.core.close_behavior import (
    CHOICE_CANCEL,
    CHOICE_CLOSE,
    CHOICE_TRAY,
    CloseBehavior,
    decide,
    load_settings,
    resolve,
    save_settings,
)

_INSTALLED = False


def _tr(text: str) -> str:
    try:
        from ok import og

        app = getattr(og, "app", None)
        if app is not None:
            return app.tr(text)
    except Exception:
        pass
    return text


def ask_close_action(parent, behavior: CloseBehavior) -> tuple[str, CloseBehavior]:
    """弹窗问用户；返回 ``(动作, 新配置)``。异常时按「取消」处理。"""
    try:
        from PySide6.QtWidgets import QCheckBox, QMessageBox

        box = QMessageBox(parent)
        box.setWindowTitle(_tr("关闭 ok-ap"))
        box.setText(_tr("要完全关闭程序，还是最小化到系统托盘？"))
        box.setIcon(QMessageBox.Question)
        close_button = box.addButton(_tr("完全关闭"), QMessageBox.AcceptRole)
        tray_button = box.addButton(_tr("收到托盘"), QMessageBox.ActionRole)
        cancel_button = box.addButton(_tr("取消"), QMessageBox.RejectRole)
        remember = QCheckBox(_tr("记住我的选择"))
        box.setCheckBox(remember)
        box.setDefaultButton(tray_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked is cancel_button or clicked is None:
            return CHOICE_CANCEL, behavior
        answer = CHOICE_CLOSE if clicked is close_button else CHOICE_TRAY
        return resolve(behavior, answer, remember.isChecked())
    except Exception:
        return CHOICE_CANCEL, behavior


def install_close_behavior_patch() -> bool:
    """挂到 MainWindow.closeEvent；重复调用幂等。"""
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        from ok.ui.qt.MainWindow import MainWindow
    except Exception:
        return False

    original = MainWindow.closeEvent

    def closeEvent(self, event):
        try:
            app = getattr(self, "app", None)
            exit_event = getattr(app, "exit_event", None)
            if exit_event is not None and exit_event.is_set():
                return original(self, event)

            behavior = load_settings()
            action = decide(behavior)
            if action is None:
                action, behavior = ask_close_action(self, behavior)
                save_settings(behavior)

            if action == CHOICE_TRAY:
                event.ignore()
                self.hide()
                return None
            if action == CHOICE_CANCEL:
                event.ignore()
                return None
        except Exception:
            pass
        return original(self, event)

    MainWindow.closeEvent = closeEvent
    _INSTALLED = True
    return True
