"""Exercise ok-script's real GDI state and blur worker, without a desktop or game."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from ok.feature.Box import Box
from ok.ui.overlay import Win32GdiOverlay
from ok.util.blur import BlurOverlayProcessor

from src.patches import overlay_blur_persist_patch as persist


def area(width, height):
    return [Box(1, height - 2, width // 2, 2)]


class TestUidOverlayPersistence(unittest.TestCase):
    def setUp(self):
        # Restore global framework methods after each test (other suites also use them).
        for name in ('update_overlay', 'update_blur_patches', 'clear_blur_patches'):
            self.enterContext(patch.object(Win32GdiOverlay, name, getattr(Win32GdiOverlay, name)))
        self.poll = self.enterContext(patch.object(Win32GdiOverlay, '_poll_input'))
        self.enterContext(patch.object(persist, '_PATCH_INSTALLED', False))
        self.enabled = True
        self.proc = SimpleNamespace(enabled=lambda: self.enabled, blur_area=area)
        self.enterContext(patch.object(persist, '_processor', return_value=self.proc))
        persist.install_overlay_blur_persist_patch()
        self.overlay = Win32GdiOverlay(native=False)
        self.addCleanup(self.overlay.close)
        self.overlay.set_boxes_enabled(False)
        self.window(True)
        self.patch = [(1, 8, 10, 2, np.full((2, 10, 3), 109, dtype=np.uint8))]

    def window(self, visible, width=20, height=10):
        self.overlay.update_overlay(visible, 200, 100, width, height, width, height, 1)

    def test_idle_and_focus_change_keep_last_patch_but_hide_window(self):
        self.overlay.update_blur_patches(self.patch)
        self.overlay.clear_drawing()  # overlay box cleanup is independent
        self.assertTrue(self.overlay.isVisible())
        self.overlay.clear_blur_overlay()  # processor clear delivered ahead of window signal
        self.window(False)
        self.assertFalse(self.overlay.isVisible())
        self.assertFalse(self.overlay.blur_images)
        self.overlay.update_blur_patches(self.patch)  # already in flight before Alt+Tab
        self.assertFalse(self.overlay.isVisible())
        self.window(True)
        self.overlay.clear_blur_overlay()  # inverse delivery order
        self.assertTrue(self.overlay.isVisible())
        self.assertIs(self.overlay.blur_images[0][4], self.patch[0][4])

    def test_resize_while_hidden_covers_new_roi_not_old_pixels(self):
        self.overlay.update_blur_patches(self.patch)
        self.window(False, 0, 0)
        self.window(True, 40, 20)
        self.overlay.update_blur_patches(self.patch)  # late old-size signal
        x, y, w, h, pixels = self.overlay.blur_images[0]
        self.assertEqual((x, y, w, h), (1, 18, 20, 2))
        self.assertFalse(pixels.any())
        replacement = np.full((2, 20, 3), 51, dtype=np.uint8)
        self.overlay.update_blur_patches([(x, y, w, h, replacement)])
        self.assertIs(self.overlay.blur_images[0][4], replacement)

    def test_switch_off_while_idle_and_late_delivery(self):
        self.overlay.update_blur_patches(self.patch)
        self.proc.last_patches = self.patch
        self.enabled = False
        self.overlay._poll_input()  # even without frames or a geometry change
        self.poll.assert_called_once()
        self.assertIsNone(self.proc.last_patches)
        self.assertFalse(self.overlay.isVisible())
        self.overlay.update_blur_patches(self.patch)
        self.assertFalse(self.overlay.blur_images)
        self.enabled = True
        self.window(True)
        self.assertFalse(self.overlay.isVisible())
        self.overlay.update_blur_patches(self.patch)
        self.assertTrue(self.overlay.isVisible())

    def test_real_worker_focus_clear_does_not_require_frame_after_return(self):
        finished = threading.Event()
        received = threading.Event()

        def emit(patches):
            self.overlay.update_blur_patches(patches)
            received.set()

        proc = BlurOverlayProcessor(area, lambda: self.enabled, emit, self.overlay.clear_blur_overlay,
                                    finished, algorithm=lambda: 'Blur')
        try:
            proc.set_visible(True)
            proc.next_frame(np.full((10, 20, 3), 60, dtype=np.uint8))
            self.assertTrue(received.wait(2))
            proc.queue.join()
            self.assertTrue(self.overlay.isVisible())
            self.window(False)
            proc.set_visible(False)
            self.assertFalse(self.overlay.isVisible())
            self.window(True)
            proc.set_visible(True)
            self.assertTrue(self.overlay.isVisible())
        finally:
            finished.set()
            proc.thread.join(2)

    def test_install_is_idempotent(self):
        before = Win32GdiOverlay.update_overlay
        persist.install_overlay_blur_persist_patch()
        self.assertIs(Win32GdiOverlay.update_overlay, before)
