"""自动拾取触发式任务单元测试。

关注两类保证：
1. 每次 ``run()`` **至多按一次 F 就返回**，不长时间独占执行器（避免拖慢其他触发任务）。
2. 节流与 F 键唤醒的边界行为。
"""

from __future__ import annotations

import time
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
    POLL_INTERVAL_SECONDS,
    WAKE_ON_F_CONFIG_KEY,
    AutoPickupTask,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SINGLE_COLLECT_SCREENSHOT = REPO_ROOT / "ok_templates" / "screenshot_20261127_005435.png"
MULTI_COLLECT_SCREENSHOT = REPO_ROOT / "ok_templates" / "screenshot_20261127_005441.png"


def _build_task(frame=None, feature_set=None, **config_overrides):
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
    # 与框架运行时一致：先按 default_config 载入配置，再应用用例覆盖。
    task.load_config()
    task.config.update(config_overrides)
    return task


class TestAutoPickupTask(unittest.TestCase):
    def test_metadata_and_registration(self):
        task = _build_task()
        self.assertEqual(task.name, "自动拾取")
        self.assertEqual(task.icon, Icons.Trigger)
        self.assertIs(task.visible, True)
        self.assertEqual(task.default_config["_enabled"], False)
        self.assertEqual(COLLECT_KEY, "f")
        self.assertAlmostEqual(COLLECT_AFTER_SLEEP, 0.1)
        self.assertEqual(task.trigger_interval, POLL_INTERVAL_SECONDS)
        self.assertAlmostEqual(task.trigger_interval, 0.3)
        self.assertIn(
            ["src.tasks.trigger.auto_pickup_task", "AutoPickupTask"],
            app_config["trigger_tasks"],
        )

    def test_wake_on_f_enabled_by_default(self):
        task = _build_task()
        self.assertTrue(task.default_config[WAKE_ON_F_CONFIG_KEY])
        self.assertIn(WAKE_ON_F_CONFIG_KEY, task.config_description)

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

    def test_run_presses_f_at_most_once_and_returns_without_looping(self):
        """关键性能约束：按一次 F 后立即返回，不再进入长时间循环。"""
        task = _build_task()
        hit = mock.Mock(name="interact_collect_hit")
        task.find_one = mock.Mock(return_value=hit)
        task.send_key = mock.Mock(return_value=True)
        task.next_frame = mock.Mock()

        result = task.run()

        self.assertIsNone(result)
        self.assertEqual(task.send_key.call_count, 1)
        task.send_key.assert_called_once_with("f", after_sleep=COLLECT_AFTER_SLEEP)
        # 不再用 self.loop 抓帧循环
        task.next_frame.assert_not_called()
        self.assertEqual(task.find_one.call_count, 1)

    def test_press_cooldown_prevents_repeated_press_for_same_prompt(self):
        task = _build_task()
        hit = mock.Mock(name="interact_collect_hit")
        task.find_one = mock.Mock(return_value=hit)
        task.send_key = mock.Mock(return_value=True)

        task.run()
        task.run()  # 冷却未到，不应再次按键

        self.assertEqual(task.send_key.call_count, 1)

    def test_poll_interval_throttles_when_not_awake(self):
        """轮询节奏交给框架 trigger_interval，run() 内不得再按时间戳自行限流。

        回归用例：曾用 time.monotonic() 与框架的 time.time() 混算，
        导致限流条件永久成立、任务在运行一段时间后彻底不动作。
        """
        task = _build_task(**{WAKE_ON_F_CONFIG_KEY: False})
        hit = mock.Mock(name="interact_collect_hit")
        task.find_one = mock.Mock(return_value=hit)
        task.send_key = mock.Mock(return_value=True)
        task.trigger_interval = 0.3
        # 框架用 Unix 时间戳写入 last_trigger_time
        task.last_trigger_time = time.time()

        task.run()

        # 即使 last_trigger_time 是真实时间戳，也必须照常判定并按键
        self.assertEqual(task.send_key.call_count, 1)

    def test_f_key_edge_extends_active_window(self):
        """物理 F 按下（边沿）时本轮跳过补按，避免一次拾取变双击。"""
        task = _build_task()
        hit = mock.Mock(name="interact_collect_hit")
        task.find_one = mock.Mock(return_value=hit)
        task.send_key = mock.Mock(return_value=True)
        task._f_was_down = False

        with mock.patch.object(AutoPickupTask, "_is_f_pressed", return_value=True):
            task.run()

        self.assertTrue(task._f_was_down)
        task.send_key.assert_not_called()

    def test_held_f_does_not_retrigger_edge(self):
        """长按 F 只认一次边沿，不会每次 run() 都刷新唤醒。"""
        task = _build_task()
        task.find_one = mock.Mock(return_value=None)
        task._f_was_down = False

        with mock.patch.object(AutoPickupTask, "_is_f_pressed", return_value=True):
            task.run()
            task.run()

        self.assertTrue(task._f_was_down)

    def test_wake_on_f_can_be_disabled(self):
        task = _build_task(**{WAKE_ON_F_CONFIG_KEY: False})
        task.find_one = mock.Mock(return_value=None)
        with mock.patch.object(AutoPickupTask, "_is_f_pressed", return_value=True) as pressed:
            task.run()
        pressed.assert_not_called()


class TestAutoPickupRealScreenshots(unittest.TestCase):
    """使用仓库本地截图与真实 FeatureSet 验证 interact_collect 模板识别。"""

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
    def test_real_screenshot_recognizes_collect_and_presses_once(self):
        multi_frame = cv2.imread(str(MULTI_COLLECT_SCREENSHOT))
        blank_frame = np.zeros_like(multi_frame)
        self.feature_set.check_size(multi_frame)

        task = _build_task(frame=multi_frame, feature_set=self.feature_set)
        task.send_key = mock.Mock(return_value=True)

        task.run()

        self.assertEqual(
            task.send_key.call_args_list,
            [mock.call("f", after_sleep=COLLECT_AFTER_SLEEP)],
        )

        # 空画面不应触发按键
        blank_task = _build_task(frame=blank_frame, feature_set=self.feature_set)
        blank_task.send_key = mock.Mock(return_value=True)
        blank_task.run()
        blank_task.send_key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
