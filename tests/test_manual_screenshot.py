import threading
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from ok import og
from ok.device.capture import CAPTURE_BUSY_WAIT
from ok.device.capture_methods.hwnd_window import HwndWindow

from src.core import game_window
from src.interaction.mouse import active_and_send_mouse_delta
from src.patches import manual_screenshot_patch as integration
from src.tasks.test.test_screenshot_task import TestScreenshotTask


class TestManualScreenshot(unittest.TestCase):
    def setUp(self):
        self.front = 12
        self.minimized = True
        self.events = []
        self.pixels = np.full((32, 32, 3), 40, np.uint8)
        self.window = HwndWindow.__new__(HwndWindow)
        self.window.hwnd = self.window.top_hwnd = 73
        self.window.do_update_window_size = Mock()
        self.method = Mock()
        self.method.get_frame.side_effect = self.read_frame
        self.device = SimpleNamespace(capture_method=self.method, hwnd_window=self.window, config={})
        self.enterContext(patch.object(game_window.win32gui, "GetForegroundWindow", side_effect=lambda: self.front))
        self.enterContext(patch.object(game_window.win32gui, "IsWindow", side_effect=lambda hwnd: hwnd == 73))
        self.enterContext(patch.object(game_window.win32gui, "IsIconic", side_effect=lambda _: self.minimized))
        self.enterContext(patch.object(game_window.win32gui, "ShowWindow", side_effect=self.restore))
        self.enterContext(patch.object(game_window.win32gui, "BringWindowToTop"))
        self.activate = self.enterContext(patch.object(game_window.win32gui, "SetForegroundWindow",
                                                       side_effect=self.set_foreground))
        self.fallback = self.enterContext(patch.object(game_window, "active_and_send_mouse_delta", return_value=False))

    def restore(self, hwnd, mode):
        self.assertEqual(mode, 9)  # SW_RESTORE
        self.minimized = False
        self.events.append("restore")

    def set_foreground(self, hwnd):
        self.front = hwnd
        self.events.append("foreground")

    def read_frame(self, *, busy_wait):
        self.assertEqual(busy_wait, CAPTURE_BUSY_WAIT)
        self.assertEqual(self.front, 73)
        self.assertFalse(self.minimized)
        self.events.append("capture")
        return self.pixels

    def test_minimized_and_already_foreground(self):
        for _ in range(2):
            self.assertIs(game_window.capture_manual_frame(self.device), self.pixels)
        self.assertEqual(self.events, ["restore", "foreground", "capture", "capture"])
        self.window.do_update_window_size.assert_called_once()

    def test_minimized_window_without_cached_handle_recovers_before_read(self):
        self.window.hwnd = self.window.top_hwnd = 0
        self.window.exe_names, self.window.hwnd_class = ["game.exe"], "UnityWndClass"
        self.device.config["selected_hwnd"] = 73
        self.fallback.side_effect = lambda *a, **kw: self.restore(73, 9)
        self.window.do_update_window_size.side_effect = lambda: setattr(self.window, "hwnd", 73)
        with (patch.object(self.window, "bring_to_front", return_value=False),
              patch.object(game_window, "find_game_hwnd", return_value=73) as find,
              patch.object(game_window.time, "sleep", side_effect=lambda _: self.set_foreground(73))):
            self.assertIs(game_window.capture_manual_frame(self.device), self.pixels)
        find.assert_called_once_with({"exe": ["game.exe"], "hwnd_class": "UnityWndClass"}, timeout=0, preferred_hwnd=73)
        self.assertEqual(self.events, ["restore", "foreground", "capture"])

    def test_focus_fallback_preserves_the_size_of_a_visible_window(self):
        self.minimized = False
        self.fallback.side_effect = active_and_send_mouse_delta
        with (patch.object(self.window, "bring_to_front", return_value=False),
              patch.object(game_window.win32gui, "IsWindowVisible", return_value=True),
              patch.object(game_window.win32gui, "ShowWindow") as show):
            self.assertIs(game_window.capture_manual_frame(self.device), self.pixels)
        show.assert_called_once_with(73, 5)  # SW_SHOW preserves a maximized game.

    def test_lookup_prefers_the_selected_game_window_and_filters_other_processes(self):
        def enumerate_windows(callback, data):
            for handle in (12, 73, 99):
                callback(handle, data)

        with (patch.object(game_window.win32gui, "EnumWindows", side_effect=enumerate_windows),
              patch.object(game_window.win32gui, "IsWindowVisible", return_value=True),
              patch.object(game_window.win32gui, "GetClassName", return_value="UnityWndClass"),
              patch.object(game_window.win32gui, "GetWindowRect", return_value=(0, 0, 1920, 1080)),
              patch.object(game_window.win32process, "GetWindowThreadProcessId", side_effect=lambda hwnd: (1, hwnd)),
              patch.object(game_window.psutil, "Process", side_effect=lambda pid: SimpleNamespace(
                  name=lambda: "other.exe" if pid == 99 else "game.exe"))):
            settings = {"exe": "game.exe", "hwnd_class": "UnityWndClass"}
            self.assertEqual(game_window.find_game_hwnd(settings, timeout=0, preferred_hwnd=73), 73)
            self.assertEqual(game_window.find_game_hwnd(settings, timeout=0, preferred_hwnd=99), 12)

    def test_focus_refused_and_focus_lost_during_capture(self):
        self.activate.side_effect = None  # Windows receives the request but leaves another window in front.
        with self.assertRaisesRegex(game_window.ManualCaptureError, "无法将游戏窗口置于前台"):
            game_window.capture_manual_frame(self.device)
        self.method.get_frame.assert_not_called()
        self.fallback.assert_called_once_with(73, only_activate=True)
        self.front = 73

        def lose_focus(**kwargs):
            frame = self.read_frame(**kwargs)
            self.front = 12
            return frame

        self.method.get_frame.side_effect = lose_focus
        with self.assertRaisesRegex(game_window.ManualCaptureError, "失去焦点"):
            game_window.capture_manual_frame(self.device)

    def test_screenshot_task_saves_only_the_fresh_frame(self):
        task = Mock(config={}, executor=SimpleNamespace(device_manager=self.device))
        task.tr.side_effect = str
        self.assertFalse(TestScreenshotTask.needs_frame)
        TestScreenshotTask.run(task)
        task.screenshot.assert_called_once_with("test_shot", frame=self.pixels)
        self.device.hwnd_window = None
        TestScreenshotTask.run(task)
        task.screenshot.assert_called_once()
        task.log_warning.assert_called_once()

    def test_framework_screenshot_template_and_manual_ocr_entry_points(self):
        diagnostics, templates = integration.diagnostics, integration.TemplateTab
        self.enterContext(patch.multiple(diagnostics, capture=Mock(), _ocr_log=Mock()))
        self.enterContext(patch.object(templates, "save_template_screenshot"))
        self.enterContext(patch.object(integration.DiagnosisPage, "run_ocr"))
        integration.install_manual_screenshot_patch()
        shots = Mock(screenshot_folder=str(self.enterContext(TemporaryDirectory())))
        shots.generate_screen_shot.return_value = "diagnostic.png"
        task = Mock()
        task.ocr.return_value = []
        executor = Mock(paused=False)
        executor.get_all_tasks.return_value = [task]
        processor = Mock(side_effect=lambda frame: frame + 1)
        self.enterContext(patch.multiple(og, device_manager=self.device, ok=SimpleNamespace(screenshot=shots),
                                         executor=executor, app=SimpleNamespace(tr=str),
                                         config={"screenshot_processor": processor}, global_config=None))
        ready = threading.Event()
        paths = []

        def done(path):
            paths.append(path)
            ready.set()

        diagnostics.capture_screenshot(done)
        self.assertTrue(ready.wait(3))
        self.assertEqual(paths, ["diagnostic.png"])
        self.assertIs(shots.generate_screen_shot.call_args.args[0], self.pixels)
        self.assertIs(shots.generate_screen_shot.call_args.kwargs["processor"], processor)

        with chdir(shots.screenshot_folder):
            self.front = 12
            saved = templates.save_template_screenshot()
            np.testing.assert_array_equal(cv2.imread(saved), self.pixels + 1)

        self.front = 12
        self.enterContext(patch.object(integration, "alert_info"))
        self.enterContext(patch.object(diagnostics, "open_screenshot_folder"))
        diagnostics._ocr_log()
        task.ocr.assert_called_once_with(frame=self.pixels, log=True, screenshot=True)
        self.front = 12
        with patch.object(integration, "submit_call") as submit:
            integration.DiagnosisPage.run_ocr(Mock())
            self.assertEqual(self.front, 12)
            submit.call_args.args[1]()  # Run the queued OCR job after the button handler has returned.
        self.assertEqual(task.ocr.call_count, 2)
        self.assertEqual(self.events.count("foreground"), 4)

        self.device.hwnd_window = None
        with patch.object(integration, "alert_error") as alert:
            self.assertIsNone(diagnostics.capture())
            alert.assert_called_once()
        shots.generate_screen_shot.assert_called_once()
        with chdir(shots.screenshot_folder), self.assertRaises(game_window.ManualCaptureError):
            templates.save_template_screenshot()
        self.assertEqual(len(list(Path(shots.screenshot_folder).rglob("*.png"))), 1)
