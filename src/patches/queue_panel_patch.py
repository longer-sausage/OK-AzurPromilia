"""把「队列悬浮窗」接进 ok-script **3.x**（v0.3.7）。

两件事：

1. :func:`ensure_overlay` / :func:`ensure_panel` —— 创建/复用悬浮窗（幂等）。
   由「队列悬浮窗」设置页在构造时调用（页面在 MainWindow 启动时就会建）；
2. :func:`install_task_timing` —— 订阅 ``communicate.task_done``，把每个任务的实际用时
   记进 ``configs/task_times.json``（每个任务保留最近 5 次）。悬停窗的「约 N 分钟」
   就是拿这个平均值算的。

可选：:func:`install_queue_panel_patch` 把创建挂到 ``MainWindow.__init__`` 上，
这样即使页面没被创建也会有悬浮窗（将来 ``main.py`` 里调 ``install_startup_patches()`` 时用）。
"""

from __future__ import annotations

import contextlib
import time

_INSTALLED = False
_TIMING_INSTALLED = False


def ensure_panel():
    """创建/复用队列悬浮窗；失败返回 ``None``（不抛，别把主窗口拖崩）。"""
    try:
        from src.gui.queue_panel import QueuePanel, current_panel, load_settings, set_current_panel

        panel = current_panel()
        if panel is not None:
            return panel
        panel = QueuePanel(load_settings())
        set_current_panel(panel)
        with contextlib.suppress(Exception):
            from PySide6.QtCore import QCoreApplication

            app = QCoreApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(panel.close)
        install_task_timing()
        # 首次刷新一次，让设置页/任务一开始就显示正确内容
        with contextlib.suppress(Exception):
            panel.refresh()
        return panel
    except Exception:
        return None


#: 兼容旧名字（和遮挡层那套保持一致）
ensure_overlay = ensure_panel


def install_task_timing() -> bool:
    """订阅任务完成信号，记录实际用时（幂等）。"""
    global _TIMING_INSTALLED
    if _TIMING_INSTALLED:
        return True
    try:
        from ok.core.events import communicate
    except Exception:
        return False

    def _on_task_done(task) -> None:
        try:
            from src.core import queue_eta

            name = str(getattr(task, "name", "") or "").strip()
            started = float(getattr(task, "start_time", 0) or 0)
            if not name or started <= 0:
                return
            seconds = time.time() - started
            history = queue_eta.load_history()
            queue_eta.record_run(history, name, seconds)
            queue_eta.save_history(history)
        except Exception:
            pass

    try:
        communicate.task_done.connect(_on_task_done)
    except Exception:
        return False
    _TIMING_INSTALLED = True
    return True


def install_queue_panel_patch() -> bool:
    """挂在 MainWindow.__init__ 上（可选，非必需）。"""
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        from ok.ui.qt.MainWindow import MainWindow
    except Exception:
        return False

    original_init = MainWindow.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._ok_ap_queue_panel = ensure_panel()

    MainWindow.__init__ = __init__
    _INSTALLED = True
    return True
