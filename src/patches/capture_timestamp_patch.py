"""保留 WGC 帧的 QPC 时间戳，供音游预测补偿截图和识别耗时。

ok-script 2.0.6 在 convert_dx_frame 后只保留像素。SystemRelativeTime 是
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

    @wraps(original)
    def convert_with_timestamp(self, frame):
        timestamp = frame.SystemRelativeTime / 10_000_000 if frame is not None else None
        image = original(self, frame)
        if image is not None:
            self._converted_frame_timestamp = timestamp
        return image

    @wraps(original_get_frame)
    def get_frame_with_timestamp(self):
        # 框架已用此 RLock 串行化取帧请求。持锁到时间戳复制完毕，防止 GUI 的
        # 下一次截图覆盖当前响应；每个调用线程随后只读取自己取到的画面时间。
        with self.get_frame_lock:
            timestamps = getattr(self, "_capture_timestamps", None)
            if timestamps is None:
                timestamps = self._capture_timestamps = local()
            timestamps.value = None
            image = original_get_frame(self)
            if image is not None:
                timestamps.value = getattr(self, "_converted_frame_timestamp", None)
            return image

    def frame_timestamp(self):
        return getattr(getattr(self, "_capture_timestamps", None), "value", None)

    WindowsGraphicsCaptureMethod.convert_dx_frame = convert_with_timestamp
    WindowsGraphicsCaptureMethod.get_frame = get_frame_with_timestamp
    WindowsGraphicsCaptureMethod.frame_timestamp = property(frame_timestamp)
    _INSTALLED = True
