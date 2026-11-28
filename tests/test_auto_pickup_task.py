"""自动拾取触发式任务单元测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np
from ok.feature.FeatureSet import FeatureSet

from src.config import config as app_config
from src.data.feature_list import FeatureList
from src.icons import Icons
from src.tasks.trigger import auto_pickup_task as pickup
from src.tasks.trigger.auto_pickup_task import (
    COLLECT_AFTER_SLEEP,
    COLLECT_KEY,
    COLLECT_TIMEOUT,
    AutoPickupTask,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SINGLE_COLLECT_SCREENSHOT = REPO_ROOT / "ok_templates" / "screenshot_20261127_005435.png"
MULTI_COLLECT_SCREENSHOT = REPO_ROOT / "ok_templates" / "screenshot_20261127_005441.png"


def _build_task(frame=None, feature_set=None):
    executor = mock.MagicMock()
    executor.debug = False
    executor.frame = frame
    executor.pause_start = None
    executor.feature_set = feature_set
    if frame is not None:
        height, width = frame.shape[:2]
        executor.method.width = width
        executor.method.height = height
    else:
        executor.method.width = 1920
        executor.method.height = 1080
    task = AutoPickupTask(executor, mock.MagicMock())
    # 流程用例沿用记录器；首键的真实实现单独测试。
    task._send_first_collect = lambda: task.send_key(COLLECT_KEY, after_sleep=COLLECT_AFTER_SLEEP)
    return task


class TestAutoPickupTask(unittest.TestCase):
    def test_metadata_and_registration(self):
        task = _build_task()
        self.assertEqual(task.name, "自动拾取")
        self.assertEqual(task.icon, Icons.Trigger)
        self.assertIs(getattr(task, "needs_frame", True), True)
        self.assertIs(task.visible, True)
        self.assertEqual(task.default_config["_enabled"], False)
        self.assertEqual(COLLECT_KEY, "f")
        self.assertAlmostEqual(COLLECT_AFTER_SLEEP, 0.1)
        self.assertIn(
            ["src.tasks.trigger.auto_pickup_task", "AutoPickupTask"],
            app_config["trigger_tasks"],
        )

    def test_run_does_nothing_when_interact_collect_not_found(self):
        task = _build_task()
        task.find_one = mock.Mock(return_value=None)
        task.send_key = mock.Mock()
        task.next_frame = mock.Mock()

        result = task.run()

        self.assertFalse(result)
        task.find_one.assert_called_once_with(FeatureList.interact_collect)
        task.send_key.assert_not_called()
        task.next_frame.assert_not_called()

    def test_run_presses_f_once_when_icon_disappears_immediately(self):
        task = _build_task()
        frame_after = object()
        hit = mock.Mock(name="interact_collect_hit")
        events: list[tuple] = []

        def fake_find_one(feature, frame=None):
            events.append(("find", feature, frame))
            return hit if frame is None else None

        def fake_send_key(key, after_sleep=0):
            events.append(("send_key", key, after_sleep))
            return True

        def fake_next_frame():
            events.append(("next_frame",))
            return frame_after

        task.find_one = mock.Mock(side_effect=fake_find_one)
        task.send_key = mock.Mock(side_effect=fake_send_key)
        task.next_frame = mock.Mock(side_effect=fake_next_frame)
        task.active_time = mock.Mock(side_effect=[0.0, 0.1])

        result = task.run()

        self.assertTrue(result)
        self.assertEqual(
            events,
            [
                ("find", FeatureList.interact_collect, None),
                ("send_key", "f", 0.1),
                ("next_frame",),
                ("find", FeatureList.interact_collect, frame_after),
            ],
        )

    def test_run_repeats_f_with_delay_until_interact_collect_disappears(self):
        task = _build_task()
        frame1, frame2, frame3 = object(), object(), object()
        hit = mock.Mock(name="interact_collect_hit")

        task.find_one = mock.Mock(side_effect=[hit, hit, hit, None])
        task.send_key = mock.Mock(return_value=True)
        task.next_frame = mock.Mock(side_effect=[frame1, frame2, frame3])
        task.active_time = mock.Mock(side_effect=[0.0, 0.1, 0.2, 0.3])

        result = task.run()

        self.assertTrue(result)
        self.assertEqual(task.send_key.call_count, 3)
        self.assertEqual(
            task.send_key.call_args_list,
            [
                mock.call("f", after_sleep=0.1),
                mock.call("f", after_sleep=0.1),
                mock.call("f", after_sleep=0.1),
            ],
        )
        self.assertEqual(
            task.find_one.call_args_list,
            [
                mock.call(FeatureList.interact_collect),
                mock.call(FeatureList.interact_collect, frame=frame1),
                mock.call(FeatureList.interact_collect, frame=frame2),
                mock.call(FeatureList.interact_collect, frame=frame3),
            ],
        )

    def test_run_stops_cleanly_when_next_frame_returns_none(self):
        task = _build_task()
        hit = mock.Mock(name="interact_collect_hit")
        task.find_one = mock.Mock(return_value=hit)
        task.send_key = mock.Mock(return_value=True)
        task.next_frame = mock.Mock(return_value=None)
        task.active_time = mock.Mock(side_effect=[0.0, 0.1])

        result = task.run()

        self.assertTrue(result)
        task.send_key.assert_called_once_with("f", after_sleep=0.1)
        task.find_one.assert_called_once_with(FeatureList.interact_collect)

    def test_run_exits_cleanly_when_loop_times_out(self):
        task = _build_task()
        hit = mock.Mock(name="interact_collect_hit")
        frame1 = object()
        task.find_one = mock.Mock(return_value=hit)
        task.send_key = mock.Mock(return_value=True)
        task.next_frame = mock.Mock(return_value=frame1)
        task.active_time = mock.Mock(side_effect=[0.0, 0.5, COLLECT_TIMEOUT + 0.1])

        result = task.run()

        self.assertTrue(result)
        self.assertEqual(task.send_key.call_count, 2)


class TestFirstPickupCapture(unittest.TestCase):
    def test_region_match_uses_same_coordinates_and_threshold_as_full_frame(self):
        settings = app_config['template_matching']
        features = FeatureSet(debug=False, coco_json=settings['coco_feature_json'],
                              default_horizontal_variance=settings['default_horizontal_variance'],
                              default_vertical_variance=settings['default_vertical_variance'],
                              default_threshold=settings['default_threshold'])
        task = _build_task(feature_set=features)
        task.executor.can_capture.return_value = True
        for width, height in ((1920, 1080), (2560, 1440)):
            with self.subTest(size=(width, height)):
                frame = np.zeros((height, width, 3), dtype=np.uint8)
                features.check_size(frame)
                name = FeatureList.interact_collect
                features.ensure_feature(name)
                f = features.feature_dict[name]
                method = SimpleNamespace(last_size=SimpleNamespace(Width=width, Height=height),
                                         get_pickup_region=lambda r, image=frame: image[r[1]:r[3], r[0]:r[2]])
                for hit in (False, True):
                    if hit:
                        frame[f.y:f.y + f.height, f.x:f.x + f.width] = f.mat
                    self.assertEqual(bool(task._match_pickup_region(method)),
                                     bool(features.find_feature(frame, name, limit=1)))

    def test_reuses_fresh_full_frame_instead_of_waiting_for_region(self):
        task = _build_task()
        task.executor.method = SimpleNamespace(last_size=SimpleNamespace(Width=40, Height=20))
        task.executor._frame = np.zeros((20, 40, 3), dtype=np.uint8)
        task.executor._last_frame_time = 99.98
        task.find_one = mock.Mock(return_value=True)
        task._match_pickup_region = mock.Mock()
        with mock.patch.object(pickup.AutoPickupTask, 'needs_frame', new_callable=mock.PropertyMock, return_value=False), \
                mock.patch.object(pickup, 'time', return_value=100):
            self.assertTrue(task._find_first_collect())
        task._match_pickup_region.assert_not_called()
        task.find_one.assert_called_once_with(FeatureList.interact_collect, frame=task.executor._frame)

    def test_stale_full_frame_does_not_override_region_result(self):
        task = _build_task()
        task.executor._frame = np.zeros((20, 40, 3), dtype=np.uint8)
        task.executor._last_frame_time = 98
        task._match_pickup_region = mock.Mock(return_value=False)
        task.next_frame = mock.Mock()
        with mock.patch.object(pickup.AutoPickupTask, 'needs_frame', new_callable=mock.PropertyMock, return_value=False), \
                mock.patch.object(pickup, 'time', return_value=100):
            self.assertFalse(task._find_first_collect())
            task._match_pickup_region.return_value = None
            task.next_frame.return_value = None
            self.assertFalse(task._find_first_collect())
        task.next_frame.assert_called_once()
        self.assertFalse(task._full_frame_path)


class TestFirstPickupInput(unittest.TestCase):
    def test_foreground_first_key_does_not_activate_and_releases(self):
        task = _build_task()
        interaction = task.executor.interaction
        interaction.hwnd_window.hwnd = 42
        interaction.send_key_down.return_value = True
        task.sleep = mock.Mock()
        with mock.patch.object(pickup.win32gui, "GetForegroundWindow", return_value=42), \
                mock.patch.object(pickup, "sleep") as hold:
            self.assertTrue(AutoPickupTask._send_first_collect(task))
        self.assertEqual(interaction.method_calls, [
            mock.call.send_key_down("f", activate=False), mock.call.send_key_up("f"),
        ])
        hold.assert_called_once_with(0.04)
        task.sleep.assert_called_once_with(COLLECT_AFTER_SLEEP)

    def test_focus_lost_after_reset_does_not_send_key(self):
        task = _build_task()
        interaction = task.executor.interaction
        interaction.hwnd_window.hwnd = 42
        with mock.patch.object(pickup.win32gui, "GetForegroundWindow", side_effect=[42, 99]):
            self.assertFalse(AutoPickupTask._send_first_collect(task))
        interaction.send_key_down.assert_not_called()


class TestManualPickupBurst(unittest.TestCase):
    def setUp(self):
        self.task = _build_task()
        self.handler = self.enterContext(mock.patch.object(pickup, "Handler")).return_value
        self.listener = self.enterContext(mock.patch.object(pickup.keyboard, "Listener")).return_value
        self.listener.is_alive.return_value = True
        self.keyboard = self.enterContext(mock.patch.object(pickup, "Controller")).return_value
        self.clock = self.enterContext(mock.patch.object(pickup, "monotonic", return_value=1.0))
        self.window = mock.Mock(return_value=42)
        self.task._burst_window = self.window
        self.burst = pickup._PickupBurst(self.task)
        self.task._burst = self.burst
        self.addCleanup(self.burst.stop)
        self.enterContext(mock.patch.object(self.burst._stopped, "wait", return_value=False))
        self.f = SimpleNamespace(vkCode=pickup.F_VK, flags=0)

    def tap(self):
        self.assertFalse(self.burst._filter(pickup.win32con.WM_KEYDOWN, self.f))
        self.assertFalse(self.burst._filter(pickup.win32con.WM_KEYUP, self.f))

    def tick(self, now):
        self.clock.return_value = now
        self.handler.post.call_args.args[0]()

    def test_physical_f_starts_timed_burst_without_recognition(self):
        self.tap()
        self.assertTrue(self.burst.active())
        self.assertEqual(self.handler.post.call_args.kwargs["delay"], 0.3)
        self.assertFalse(self.task.should_trigger())
        self.task.find_one = mock.Mock()
        self.task.run()
        self.task.find_one.assert_not_called()
        self.tick(1.3)
        self.keyboard.press.assert_called_once()
        self.keyboard.release.assert_called_once_with(self.keyboard.press.call_args.args[0])
        self.tap()  # 不延长已开启的窗口
        self.tick(11.0)
        self.assertFalse(self.burst.active())
        self.keyboard.press.assert_called_once()
        self.task.find_one.return_value = None
        self.task.run()
        self.task.find_one.assert_called_once_with(FeatureList.interact_collect)

    def test_custom_interval_applies_to_first_and_later_presses(self):
        self.task.config = {pickup.BURST_INTERVAL_KEY: 640}
        self.tap()
        self.assertEqual(self.handler.post.call_args.kwargs["delay"], 0.64)
        self.tick(1.64)
        self.assertAlmostEqual(self.handler.post.call_args.kwargs["delay"], 0.64)
        self.task.config[pickup.BURST_INTERVAL_KEY] = 1
        self.assertEqual(self.task._burst_interval(), 0.2)

    def test_synthetic_f_never_starts_burst(self):
        self.f.flags = pickup.INJECTED
        self.tap()
        self.handler.post.assert_not_called()

    def test_loss_of_game_window_cancels_instead_of_resuming(self):
        self.tap()
        self.window.return_value = None
        self.tick(1.1)
        self.window.return_value = 42
        self.assertFalse(self.burst.active())
        self.keyboard.press.assert_not_called()
        self.handler.post.assert_called_once()

    def test_held_f_is_not_simulated_until_released(self):
        self.burst._filter(pickup.win32con.WM_KEYDOWN, self.f)
        self.tick(1.1)
        self.keyboard.press.assert_not_called()
        self.burst._filter(pickup.win32con.WM_KEYUP, self.f)
        self.tick(1.2)
        self.keyboard.press.assert_called_once()

    def test_focus_pause_and_settings_gate(self):
        task = _build_task()
        task.executor.paused = False
        task.executor.exit_event.is_set.return_value = False
        task.executor.exclusive_owner = None
        task.executor.device_manager.hwnd_window.hwnd = 42
        task._enabled = True
        task.config = dict(task.default_config)
        with mock.patch.object(pickup.win32gui, "GetForegroundWindow", return_value=42):
            self.assertIsNone(task._burst_window())
            task.config[pickup.MANUAL_BURST] = True
            self.assertEqual(task._burst_window(), 42)
            task.executor.paused = True
            self.assertIsNone(task._burst_window())


class TestAutoPickupRealScreenshots(unittest.TestCase):
    """使用仓库本地截图与真实 FeatureSet 验证 interact_collect 模板识别与连按流程。"""

    @classmethod
    def setUpClass(cls):
        tm = app_config["template_matching"]
        cls.feature_set = FeatureSet(
            debug=False,
            coco_json=tm["coco_feature_json"],
            default_horizontal_variance=tm["default_horizontal_variance"],
            default_vertical_variance=tm["default_vertical_variance"],
            default_threshold=tm["default_threshold"],
        )

    @unittest.skipUnless(
        SINGLE_COLLECT_SCREENSHOT.exists() and MULTI_COLLECT_SCREENSHOT.exists(),
        "本地缺少拾取交互截图样本",
    )
    def test_real_screenshots_trigger_repeated_f_until_cleared(self):
        multi_frame = cv2.imread(str(MULTI_COLLECT_SCREENSHOT))
        single_frame = cv2.imread(str(SINGLE_COLLECT_SCREENSHOT))
        blank_frame = np.zeros_like(single_frame)
        self.feature_set.check_size(multi_frame)

        task = _build_task(frame=multi_frame, feature_set=self.feature_set)
        task.send_key = mock.Mock(return_value=True)
        task.next_frame = mock.Mock(side_effect=[single_frame, blank_frame])
        task.active_time = mock.Mock(side_effect=[0.0, 0.1, 0.2])

        result = task.run()

        self.assertTrue(result)
        self.assertEqual(
            task.send_key.call_args_list,
            [
                mock.call("f", after_sleep=0.1),
                mock.call("f", after_sleep=0.1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
