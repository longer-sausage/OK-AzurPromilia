"""自动拾取触发式任务单元测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from ok.feature.FeatureSet import FeatureSet

from src.config import config as app_config
from src.data.feature_list import FeatureList
from src.icons import Icons
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
    return AutoPickupTask(executor, mock.MagicMock())


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
