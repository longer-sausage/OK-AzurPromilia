import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

from src.patches import capture_timestamp_patch


class TestCaptureTimestampPatch(unittest.TestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(capture_timestamp_patch, "_INSTALLED", False))
        self.pixels = np.zeros((20, 20, 4), dtype=np.uint8)
        self.patches.enter_context(
            patch.object(
                WindowsGraphicsCaptureMethod,
                "convert_dx_frame",
                lambda self, source: source.pixels if source else None,
            )
        )

        def capture(method):
            source = method.source
            if source is None:
                return None
            return method.convert_dx_frame(source)

        self.patches.enter_context(patch.object(WindowsGraphicsCaptureMethod, "do_get_frame", capture))
        self.patches.enter_context(
            patch.object(WindowsGraphicsCaptureMethod, "get_frame", WindowsGraphicsCaptureMethod.get_frame)
        )
        self.patches.enter_context(patch.object(WindowsGraphicsCaptureMethod, "frame_timestamp", None, create=True))
        self.method = WindowsGraphicsCaptureMethod.__new__(WindowsGraphicsCaptureMethod)
        self.method.get_frame_lock = threading.RLock()
        self.method.exit_event = threading.Event()
        self.method.source = SimpleNamespace(SystemRelativeTime=1234567890, pixels=self.pixels)

    def test_qpc_timestamp_matches_converted_pixels_and_install_is_idempotent(self):
        capture_timestamp_patch.install_capture_timestamp_patch()
        installed = WindowsGraphicsCaptureMethod.convert_dx_frame
        getter = WindowsGraphicsCaptureMethod.get_frame
        capture_timestamp_patch.install_capture_timestamp_patch()
        self.assertIs(installed, WindowsGraphicsCaptureMethod.convert_dx_frame)
        self.assertIs(getter, WindowsGraphicsCaptureMethod.get_frame)
        np.testing.assert_array_equal(self.method.get_frame(), self.pixels[:, :, :3])
        self.assertEqual(self.method.frame_timestamp, 123.456789)

    def test_preview_capture_does_not_replace_task_threads_timestamp(self):
        capture_timestamp_patch.install_capture_timestamp_patch()
        np.testing.assert_array_equal(self.method.get_frame(), self.pixels[:, :, :3])
        preview_timestamps = []

        def preview():
            self.method.source = SimpleNamespace(SystemRelativeTime=2234567890, pixels=self.pixels + 1)
            self.method.get_frame()
            preview_timestamps.append(self.method.frame_timestamp)

        worker = threading.Thread(target=preview)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(preview_timestamps, [223.456789])
        self.assertEqual(self.method.frame_timestamp, 123.456789)

    def test_failed_capture_clears_previous_timestamp(self):
        capture_timestamp_patch.install_capture_timestamp_patch()
        self.method.get_frame()
        self.method.source = None
        self.assertIsNone(self.method.get_frame())
        self.assertIsNone(self.method.frame_timestamp)


if __name__ == "__main__":
    unittest.main()
