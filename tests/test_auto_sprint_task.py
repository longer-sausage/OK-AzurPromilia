"""用物理按键事件和可推进的时钟验证自动冲刺，不安装系统钩子。"""

import heapq
import json
import tempfile
import threading
import unittest
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import win32con
from ok import ExitEvent, Handler, og
from ok.ui.qt.tasks.config_editors import NumberEditor
from ok.ui.qt.tasks.config_form import ConfigForm
from ok.util.config import Config
from pynput.keyboard import Key, KeyCode
from PySide6.QtWidgets import QApplication

from src.gui.account_config_tab import InMemoryConfig
from src.tasks.trigger import auto_sprint_task as sprint

COUNT = sprint.CHAR_COUNT_CONFIG_KEY
LEFT, RIGHT = win32con.VK_LSHIFT, win32con.VK_RSHIFT
DOWN, UP = win32con.WM_KEYDOWN, win32con.WM_KEYUP
ONE, TWO, THREE = (KeyCode.from_vk(ord(digit)) for digit in "123")


class ManualHandler:
    def __init__(self):
        self.now = 0.0
        self.queue = []
        self.order = count()

    def post(self, callback, delay):
        heapq.heappush(self.queue, (self.now + delay, next(self.order), callback))

    def advance_to(self, target):
        while self.queue and self.queue[0][0] <= target:
            self.now, _, callback = heapq.heappop(self.queue)
            callback()
        self.now = target

    def stop(self):
        self.queue.clear()

    def join(self):
        pass


class TestAutoSprintTask(unittest.TestCase):
    def setUp(self):
        self.handler = ManualHandler()
        self.handler_factory = self.enterContext(patch.object(sprint, "Handler", return_value=self.handler))
        self.enterContext(patch.object(sprint, "monotonic", side_effect=lambda: self.handler.now))
        self.enterContext(patch.object(sprint, "KEY_DOWN_SECONDS", 0))
        self.enterContext(patch("ok.task.task.communicate"))
        self.listeners = self.enterContext(patch.object(sprint.keyboard, "Listener"))
        self.listeners.return_value.is_alive.return_value = True
        self.keys = self.enterContext(patch.object(sprint, "Controller")).return_value
        self.foreground = self.enterContext(patch.object(sprint.win32gui, "GetForegroundWindow", return_value=42))
        self.executor = MagicMock(paused=False, exclusive_owner=None, exit_event=ExitEvent())
        self.executor.device_manager.hwnd_window.hwnd = 42
        self.app = SimpleNamespace(tr=str)
        self.task = sprint.AutoSprintTask(self.executor, self.app)
        self.task.config = dict(self.task.default_config)
        self.addCleanup(self.task.on_destroy)

    def key(self, msg, vk=LEFT, flags=0, scan=0):
        callback = self.listeners.call_args.kwargs["win32_event_filter"]
        return callback(msg, SimpleNamespace(vkCode=vk, flags=flags, scanCode=scan))

    def presses(self):
        return [args[0] for args, _ in self.keys.press.call_args_list]

    def test_settings_show_only_party_size_with_bounds_and_default(self):
        self.assertEqual(self.task.default_config, {"_enabled": False, COUNT: 3})
        self.assertFalse(self.task.needs_frame)
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        values = InMemoryConfig(self.task.default_config, self.task.default_config)
        with patch.object(og, "app", self.app):
            form = ConfigForm(
                self.task, values, self.task.default_config, self.task.config_description, self.task.config_type
            )
            self.assertEqual(list(form.config_widget_by_key), [COUNT])
            editor = form.config_widget_by_key[COUNT]
            self.assertIsInstance(editor, NumberEditor)
            self.assertEqual(editor.spin_box.value(), 3)
            editor.spin_box.setValue(0)
            self.assertEqual(values[COUNT], 1)
            editor.spin_box.setValue(4)
            self.assertEqual(values[COUNT], 3)
            form.deleteLater()
            app.processEvents()

    def test_saved_counts_and_enabled_flag_survive_upgrade(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(Config, "config_folder", folder):
            path = Path(folder) / "AutoSprintTask.json"
            for saved, expected in ((1, 1), (2, 2), (3, 3), (4, 3), (0, 1), (True, 3), ("bad", 3)):
                with self.subTest(saved=saved):
                    path.write_text(
                        json.dumps({"_enabled": True, COUNT: saved, "Shift冲刺模式": False}), encoding="utf-8"
                    )
                    self.task.load_config()
                    self.assertEqual(self.task.char_count(), expected)
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"_enabled": True, COUNT: expected})

    def test_short_press_other_keys_and_injected_events_pass_through(self):
        self.task.enable()
        for msg in (DOWN, UP):
            self.key(msg, ord("W"))
            self.key(msg, flags=win32con.LLKHF_INJECTED)
            self.key(msg, RIGHT, flags=win32con.LLKHF_LOWER_IL_INJECTED)
        self.assertFalse(self.handler.queue)
        self.key(DOWN)
        self.handler.advance_to(0.49)
        self.key(UP)
        self.handler.advance_to(1)
        self.assertFalse(self.keys.mock_calls)
        self.listeners.return_value.suppress_event.assert_not_called()

    def test_default_rotates_three_slots_with_sprints_between_switches(self):
        self.task.enable()
        self.key(DOWN)
        self.handler.advance_to(0.49)
        self.keys.press.assert_not_called()
        self.handler.advance_to(0.5)
        self.assertEqual(self.keys.mock_calls, [call.release(Key.shift_l), call.press(ONE), call.release(ONE)])
        self.handler.advance_to(0.749)
        self.assertEqual(self.presses(), [ONE])
        self.handler.advance_to(2.61)
        shifts = [Key.shift_l] * 3
        self.assertEqual(self.presses(), [ONE, *shifts, TWO, *shifts, THREE, *shifts, ONE])

    def test_late_callback_sprints_before_switching_and_does_not_catch_up_in_a_burst(self):
        self.task.enable()
        self.key(DOWN)
        self.handler.advance_to(0.5)
        _, _, delayed_sprint = heapq.heappop(self.handler.queue)
        self.handler.now = 5
        delayed_sprint()
        self.handler.advance_to(5.19)
        self.assertEqual(self.presses(), [ONE, Key.shift_l])
        self.handler.advance_to(5.46)
        self.assertEqual(self.presses(), [ONE, Key.shift_l, Key.shift_l, Key.shift_l, TWO])

    def test_two_characters_and_live_count_change(self):
        self.task.config[COUNT] = 2
        self.task.enable()
        self.key(DOWN)
        self.handler.advance_to(2)
        self.assertEqual([key for key in self.presses() if key != Key.shift_l], [ONE, TWO, ONE])
        self.task.config[COUNT] = 1
        self.keys.reset_mock()
        self.handler.advance_to(3.5)
        self.assertTrue(self.presses())
        self.assertEqual(set(self.presses()), {Key.shift_l})

    def test_one_character_sprints_without_number_keys(self):
        self.task.config[COUNT] = 1
        self.task.enable()
        self.key(DOWN, RIGHT)
        self.handler.advance_to(1.01)
        self.assertEqual(self.presses(), [Key.shift_l] * 3)
        self.assertEqual(self.keys.mock_calls[0], call.release(Key.shift_r))
        self.key(UP, RIGHT)
        self.handler.advance_to(2)
        self.assertEqual(len(self.presses()), 3)

    def test_both_shift_keys_share_one_hold_and_only_physical_shift_is_suppressed(self):
        self.task.enable()
        self.key(win32con.WM_SYSKEYDOWN, win32con.VK_SHIFT, scan=0x36)
        self.key(DOWN, LEFT)
        self.key(DOWN, RIGHT)
        self.assertEqual(len(self.handler.queue), 1)
        self.handler.advance_to(0.5)
        self.assertCountEqual(self.keys.mock_calls[:2], [call.release(Key.shift_l), call.release(Key.shift_r)])
        suppress = self.listeners.return_value.suppress_event
        self.key(DOWN, ord("W"))
        self.key(UP, flags=win32con.LLKHF_INJECTED)
        suppress.assert_not_called()
        self.key(DOWN, RIGHT)
        self.key(win32con.WM_SYSKEYUP, RIGHT)
        self.assertEqual(suppress.call_count, 2)
        self.handler.advance_to(0.76)
        self.assertEqual(self.presses(), [ONE, Key.shift_l])
        self.key(UP, LEFT)
        self.assertEqual(suppress.call_count, 3)
        self.handler.advance_to(2)
        self.assertEqual(self.presses(), [ONE, Key.shift_l])

    def test_repress_starts_a_new_hold_even_with_an_old_callback_queued(self):
        self.task.enable()
        self.key(DOWN)
        self.handler.advance_to(0.3)
        self.key(UP)
        self.key(DOWN)
        self.handler.advance_to(0.79)
        self.keys.press.assert_not_called()
        self.handler.advance_to(0.81)
        self.assertEqual(self.presses(), [ONE])
        self.key(UP)
        self.key(DOWN)
        self.handler.advance_to(1.32)
        self.assertEqual(self.presses(), [ONE, ONE])

    def test_release_during_key_down_pairs_the_release_and_cancels_followup(self):
        self.task.enable()
        self.keys.press.side_effect = lambda _: self.key(UP)
        self.key(DOWN)
        self.handler.advance_to(2)
        self.assertEqual(self.keys.mock_calls, [call.release(Key.shift_l), call.press(ONE), call.release(ONE)])
        self.assertFalse(self.handler.queue)

    def test_physical_up_during_takeover_passes_before_synthetic_up_is_confirmed(self):
        self.task.enable()
        self.keys.release.side_effect = lambda _: self.key(UP)
        self.key(DOWN)
        self.handler.advance_to(1)
        self.listeners.return_value.suppress_event.assert_not_called()
        self.keys.press.assert_not_called()

    def test_second_shift_pressed_after_takeover_keeps_sprinting_until_both_are_up(self):
        self.task.enable()
        self.key(DOWN)
        self.handler.advance_to(0.5)
        self.key(DOWN, RIGHT)
        self.key(UP, LEFT)
        self.handler.advance_to(0.76)
        self.key(UP, RIGHT)
        self.handler.advance_to(2)
        self.assertEqual(self.listeners.return_value.suppress_event.call_count, 3)
        self.assertEqual(self.presses(), [ONE, Key.shift_l])

    def test_focus_loss_during_takeover_prevents_first_slot(self):
        self.task.enable()
        self.keys.release.side_effect = lambda _: setattr(self.foreground, "return_value", 99)
        self.key(DOWN)
        self.handler.advance_to(1)
        self.keys.press.assert_not_called()
        self.key(UP)
        self.listeners.return_value.suppress_event.assert_not_called()

    def test_press_started_outside_game_requires_a_fresh_press(self):
        self.task.enable()
        self.foreground.return_value = 99
        self.key(DOWN)
        self.foreground.return_value = 42
        self.key(DOWN)
        self.handler.advance_to(1)
        self.assertFalse(self.keys.mock_calls)
        self.key(UP)
        self.key(DOWN)
        self.handler.advance_to(1.5)
        self.assertEqual(self.presses(), [ONE])

    def test_guards_cancel_an_active_cycle_until_shift_is_released(self):
        self.task.enable()
        for obj, attr, blocked in (
            (self.foreground, "return_value", 99),
            (self.task, "_paused", True),
            (self.executor, "paused", True),
            (self.executor, "exclusive_owner", "another task"),
            (self.executor, "interaction", None),
            (self.executor.device_manager, "hwnd_window", None),
        ):
            with self.subTest(guard=attr):
                self.keys.reset_mock()
                self.key(DOWN)
                self.handler.advance_to(self.handler.now + 0.51)
                self.assertEqual(self.presses(), [ONE])
                original = getattr(obj, attr)
                try:
                    setattr(obj, attr, blocked)
                    self.handler.advance_to(self.handler.now + 1)
                finally:
                    setattr(obj, attr, original)
                self.key(DOWN)
                self.handler.advance_to(self.handler.now + 1)
                self.assertEqual(self.presses(), [ONE])
                self.key(UP)

    def test_input_failure_releases_the_key_and_allows_next_hold(self):
        self.task.enable()
        self.key(DOWN)
        self.keys.press.side_effect = OSError("input failed")
        with self.assertRaises(OSError):
            self.handler.advance_to(0.5)
        self.keys.release.assert_called_with(ONE)
        self.key(UP)
        self.keys.press.side_effect = None
        self.key(DOWN)
        self.handler.advance_to(1.26)
        self.assertEqual(self.presses(), [ONE, ONE, Key.shift_l])

    def test_restore_disable_restart_and_destroy(self):
        self.task.on_create()
        self.listeners.assert_not_called()
        self.task.config["_enabled"] = True
        self.task.on_create()
        self.task.enable()
        self.task.run()
        self.listeners.assert_called_once()
        self.handler_factory.assert_called_once()
        old_filter = self.listeners.call_args.kwargs["win32_event_filter"]
        self.key(DOWN)
        self.handler.advance_to(0.5)
        self.task.disable()
        self.handler.advance_to(1)
        self.assertFalse(self.task.config["_enabled"])
        self.assertEqual(self.presses(), [ONE])
        self.task.enable()
        old_filter(DOWN, SimpleNamespace(vkCode=LEFT, flags=0))
        self.assertFalse(self.handler.queue)
        self.key(DOWN)
        self.handler.advance_to(1.5)
        self.task.on_destroy()
        self.task.run()
        self.handler.advance_to(3)
        self.assertEqual(self.listeners.call_count, 2)
        self.assertEqual(self.listeners.return_value.stop.call_count, 2)
        self.assertEqual(self.presses(), [ONE, ONE])

    def test_dead_listener_cancels_input_and_is_replaced_on_next_framework_run(self):
        self.task.enable()
        self.key(DOWN)
        self.handler.advance_to(0.5)
        self.listeners.return_value.is_alive.return_value = False
        self.handler.advance_to(2)
        self.assertEqual(self.presses(), [ONE])
        self.task.run()
        self.assertEqual(self.listeners.call_count, 2)
        self.listeners.return_value.is_alive.return_value = True
        self.key(DOWN)
        self.handler.advance_to(2.5)
        self.assertEqual(self.presses(), [ONE, ONE])

    def test_reenable_after_listener_stop_failure(self):
        self.task.enable()
        self.key(DOWN)
        self.listeners.return_value.stop.side_effect = OSError("stop failed")
        with patch.object(sprint.logger, "exception") as log:
            self.task.disable()
        log.assert_called_once()
        self.assertFalse(self.handler.queue)
        self.listeners.return_value.stop.side_effect = None
        self.task.enable()
        self.key(DOWN)
        self.handler.advance_to(0.5)
        self.assertEqual(self.presses(), [ONE])

    def test_real_handler_finishes_in_flight_key_release_on_disable(self):
        pressed = threading.Event()
        self.keys.press.side_effect = lambda _: pressed.set()
        with (
            patch.object(sprint, "Handler", Handler),
            patch.object(sprint, "HOLD_SECONDS", 0),
            patch.object(sprint, "KEY_DOWN_SECONDS", 5),
        ):
            self.task.enable()
            self.key(DOWN)
            self.assertTrue(pressed.wait(2))
            self.task.disable()
        self.assertEqual(self.keys.mock_calls, [call.release(Key.shift_l), call.press(ONE), call.release(ONE)])

    def test_executor_exit_cancels_pending_sprint(self):
        self.task.enable()
        self.key(DOWN)
        self.handler.advance_to(0.5)
        self.executor.exit_event.set()
        self.handler.advance_to(2)
        self.assertEqual(self.presses(), [ONE])
        self.assertFalse(self.handler.queue)


if __name__ == "__main__":
    unittest.main()
