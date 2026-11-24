"""DPI 缩放策略：在创建 ``QApplication`` **之前**设置，否则 Qt 直接忽略。

框架自己也在设（``ok/ui/qt/util/app.py``），但它在 ``QApplication(sys.argv)`` 之后才调，
Qt 只会打一行警告「setHighDpiScaleFactorRoundingPolicy must be called before creating the
QGuiApplication instance」然后忽略——于是 150% 缩放下文字会被取整成模糊的一档。

这个 helper 在入口最前面调用一次（`main.py` / `main_debug.py`），幂等、无副作用：
已经存在 QApplication 时什么都不做（那种情况下确实改不了，只能靠框架）。
"""

from __future__ import annotations

_POLICY_APPLIED = False


def apply_high_dpi_policy() -> bool:
    """在 QApplication 创建前把 DPI 取整策略设成 PassThrough；返回是否设置成功。"""
    global _POLICY_APPLIED
    if _POLICY_APPLIED:
        return True
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
    except Exception:
        return False
    try:
        if QGuiApplication.instance() is not None:
            return False  # 已经创建了，Qt 会忽略这次调用
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        return False
    _POLICY_APPLIED = True
    return True
