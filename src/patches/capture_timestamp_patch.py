"""保留 WGC 帧的 QPC 时间戳，供音游预测补偿截图和识别耗时。

ok-script 3.0.13 在 convert_dx_frame 后只保留像素。SystemRelativeTime 是
100ns 单位的 QPC 时间，与 time.perf_counter 使用同一时间轴。升级框架时复核。
"""

from functools import wraps
from threading import local

_INSTALLED = False


def install_capture_timestamp_patch():
    global _INSTALLED
    if _INSTALLED:
        return
    from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

    original = WindowsGraphicsCaptureMethod.convert_dx_frame
    original_get_frame = WindowsGraphicsCaptureMethod.get_frame
    original_capture = WindowsGraphicsCaptureMethod._do_get_frame

    @wraps(original)
    def convert_with_timestamp(self, frame):
        timestamp = frame.SystemRelativeTime / 10_000_000 if frame is not None else None
        image = original(self, frame)
        if image is not None:
            self._converted_frame_timestamp = timestamp
        return image

    @wraps(original_capture)
    def capture_with_timestamp(self, frame_deadline=None):
        # Both normal captures and probes enter here with get_frame_lock held.
        image = original_capture(self, frame_deadline=frame_deadline)
        if image is not None:
            if not hasattr(self, "_capture_timestamps"):
                self._capture_timestamps = local()
            self._capture_timestamps.value = self._converted_frame_timestamp
        return image

    @wraps(original_get_frame)
    def get_frame_with_timestamp(self, busy_wait=0):
        timestamps = getattr(self, "_capture_timestamps", None)
        if timestamps is not None:
            timestamps.value = None
        image = original_get_frame(self, busy_wait=busy_wait)
        if image is None and hasattr(self, "_capture_timestamps"):
            self._capture_timestamps.value = None
        return image

    def frame_timestamp(self):
        return getattr(getattr(self, "_capture_timestamps", None), "value", None)

    WindowsGraphicsCaptureMethod.convert_dx_frame = convert_with_timestamp
    WindowsGraphicsCaptureMethod._do_get_frame = capture_with_timestamp
    WindowsGraphicsCaptureMethod.get_frame = get_frame_with_timestamp
    WindowsGraphicsCaptureMethod.frame_timestamp = property(frame_timestamp)
    _INSTALLED = True
