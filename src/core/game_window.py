import time

import psutil
import pywintypes
import win32gui
import win32process
from ok.device.capture import CAPTURE_BUSY_WAIT
from ok.task.exceptions import CaptureException

from src.interaction.mouse import active_and_send_mouse_delta


class ManualCaptureError(RuntimeError):
    """本次手动截图未取得可用的游戏画面。"""


def _is_capture_window_foreground(window) -> bool:
    if window is None:
        return False
    try:
        hwnd = win32gui.GetForegroundWindow()
        return bool(hwnd and hwnd in (window.hwnd, window.top_hwnd) and not win32gui.IsIconic(hwnd))
    except pywintypes.error:
        return False


def _focus_capture_window(window, device) -> bool:
    if window is None:
        return False
    if _is_capture_window_foreground(window):
        return True
    window.bring_to_front()
    if not _is_capture_window_foreground(window):
        hwnd = window.top_hwnd or window.hwnd
        if not hwnd or not win32gui.IsWindow(hwnd):
            # 最小化时框架可能暂时没有客户区。按进程和窗口类重新核对已选 HWND。
            settings = {"exe": window.exe_names or device.config.get("selected_exe") or (),
                        "hwnd_class": window.hwnd_class or ()}
            hwnd = find_game_hwnd(settings, timeout=0, preferred_hwnd=device.config.get("selected_hwnd"))
        if hwnd:
            active_and_send_mouse_delta(hwnd, only_activate=True)
    window.do_update_window_size()
    # Windows 切换焦点时 GetForegroundWindow 可能短暂返回 0。
    for _ in range(10):
        if _is_capture_window_foreground(window):
            return True
        time.sleep(0.02)
    return _is_capture_window_foreground(window)


def capture_manual_frame(device):
    """手动截图先置前当前设备的游戏窗口，取回新帧时再次核对窗口和焦点。"""
    method = getattr(device, "capture_method", None)
    if method is None:
        raise ManualCaptureError("请先连接游戏窗口，再截图。")
    window = getattr(device, "hwnd_window", None)
    try:
        focused = _focus_capture_window(window, device)
    except (OSError, pywintypes.error):
        focused = False
    if not focused or device.capture_method is not method:
        raise ManualCaptureError("无法将游戏窗口置于前台，已取消截图。")
    hwnd = window.hwnd
    try:
        frame = method.get_frame(busy_wait=CAPTURE_BUSY_WAIT)
    except CaptureException as exc:
        raise ManualCaptureError("未能获取游戏画面，请重试。") from exc
    if (device.capture_method is not method or device.hwnd_window is not window
            or window.hwnd != hwnd or not _is_capture_window_foreground(window)):
        raise ManualCaptureError("截图时游戏窗口已失去焦点，请重试。")
    if frame is None:
        raise ManualCaptureError("未能获取游戏画面，请重试。")
    return frame


def find_game_hwnd(window_config: dict, timeout: float = 10.0, interval: float = 0.2,
                   *, preferred_hwnd: int | None = None) -> int:
    """Find the visible top-level game window matching the configured features."""
    exe_config = window_config.get("exe", ())
    if isinstance(exe_config, str):
        exe_config = (exe_config,)
    exe_names = {str(name).casefold() for name in exe_config if name}

    class_config = window_config.get("hwnd_class", ())
    if isinstance(class_config, str):
        class_config = (class_config,)
    class_names = {str(name) for name in class_config if name}

    if not exe_names and not class_names:
        return 0

    deadline = time.monotonic() + timeout

    while True:
        candidates = []
        foreground_hwnd = win32gui.GetForegroundWindow()

        def collect(hwnd, _, candidates=candidates, foreground_hwnd=foreground_hwnd):
            """Collect candidate window handles matching the configured criteria."""
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True

                if class_names and win32gui.GetClassName(hwnd) not in class_names:
                    return True

                if exe_names:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if psutil.Process(pid).name().casefold() not in exe_names:
                        return True

                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                area = max(0, right - left) * max(0, bottom - top)
                candidates.append((hwnd == preferred_hwnd, hwnd == foreground_hwnd, area, hwnd))
            except (OSError, psutil.Error, pywintypes.error):
                pass
            return True

        win32gui.EnumWindows(collect, None)

        if candidates:
            return max(candidates)[-1]

        if time.monotonic() >= deadline:
            return 0

        time.sleep(interval)
