"""任务配置项可见性与调试任务门禁。

固化两条 ok-script 机制在本项目里的用法，避免以后改动把它们悄悄破坏：

1. **配置键以 ``_`` 开头时不渲染**（``ok/ui/qt/tasks/ConfigCard.py`` 里
   ``if not key.startswith('_')``）。用来把「数值调优项」留在 ``default_config``
   里（保留默认值、仍可读、仍可写进 configs/*.json），但不占用 UI。
2. **任务 ``self.visible`` 为假时不进任务列表**（``OneTimeTaskTab`` /
   ``TriggerTaskTab`` / ``ScheduleTaskTab`` 都读 ``getattr(task, 'visible', True)``）。
   纯调试任务用 ``self.visible = self.debug`` 只在 debug 模式露面。
"""

import unittest
from unittest import mock

from src.tasks.onetime.TestScreenshotTask import TestScreenshotTask
from src.tasks.test.TestInteractionTask import TestInteractionTask
from src.tasks.test.TestTreasureBandTask import TestTreasureBandTask
from src.tasks.trigger.SkipDialogTask import SkipDialogTask
from src.tasks.trigger.StarLinkAssistTask import StarLinkAssistTask
from src.tasks.trigger.TreasureUnlockTask import TreasureUnlockTask

#: 纯调试任务：应满足 visible == debug
DEBUG_TASKS = [TestScreenshotTask, TestInteractionTask, TestTreasureBandTask]

#: 正式业务任务：不设 visible，任何模式下都应可见
BUSINESS_TASKS = [SkipDialogTask, StarLinkAssistTask, TreasureUnlockTask]

#: 期望在 UI 里出现的配置键（未列出的键必须已用 _ 前缀隐藏）
EXPECTED_VISIBLE_KEYS = {
    TreasureUnlockTask: ["画调试框"],
    StarLinkAssistTask: [
        "检测绿色", "检测黄色", "检测红色", "要求环形", "记录点击日志", "画调试框",
    ],
    TestTreasureBandTask: [
        "V 下限", "S 上限", "最小高度", "最大宽度", "最小长宽比",
        "显示被过滤候选", "自动开启覆盖层", "结束时保存截图",
    ],
}


def build_task(task_class, debug=False):
    """用假 executor / app 构造任务实例，只验证构造期行为。"""
    executor = mock.MagicMock()
    executor.debug = debug
    return task_class(executor, mock.MagicMock())


def visible_keys(task):
    """复刻 ConfigCard 的渲染过滤规则，返回 UI 上会出现的配置键。"""
    return [key for key in task.default_config if not key.startswith("_")]


class TestDebugTaskVisibility(unittest.TestCase):
    """调试任务只在 debug 模式下出现在任务列表。"""

    def test_debug_tasks_follow_debug_flag(self):
        for task_class in DEBUG_TASKS:
            with self.subTest(task=task_class.__name__):
                self.assertIs(build_task(task_class, debug=False).visible, False)
                self.assertIs(build_task(task_class, debug=True).visible, True)

    def test_business_tasks_always_visible(self):
        for task_class in BUSINESS_TASKS:
            for debug in (False, True):
                with self.subTest(task=task_class.__name__, debug=debug):
                    self.assertIs(build_task(task_class, debug=debug).visible, True)


class TestHiddenConfigKeys(unittest.TestCase):
    """数值调优项用 _ 前缀隐藏，但仍保留在 default_config 中。"""

    def test_visible_keys_match_expectation(self):
        for task_class, expected in EXPECTED_VISIBLE_KEYS.items():
            with self.subTest(task=task_class.__name__):
                self.assertEqual(visible_keys(build_task(task_class)), expected)

    def test_hidden_keys_keep_their_defaults(self):
        """隐藏 ≠ 删除：键仍在 default_config 里，值仍是可用的默认值。"""
        task = build_task(TreasureUnlockTask)
        self.assertEqual(task.default_config["_校准稳定帧数"], 5)
        self.assertEqual(task.default_config["_条带存在阈值"], 0.15)
        self.assertIn("_单次运行时长上限(秒)", task.default_config)

    def test_config_description_keys_are_consistent(self):
        """config_description 的键必须存在于 default_config，且隐藏状态一致。"""
        for task_class in (StarLinkAssistTask, TreasureUnlockTask, TestTreasureBandTask):
            task = build_task(task_class)
            hidden = {k for k in task.default_config if k.startswith("_")}
            with self.subTest(task=task_class.__name__):
                for key in task.config_description:
                    self.assertIn(key, task.default_config)
                    # 描述键与配置键同进退：隐藏项的说明也带 _ 前缀
                    self.assertEqual(
                        key.startswith("_"),
                        key in hidden,
                        f"{task_class.__name__}: {key} 的隐藏状态与 default_config 不一致",
                    )

    def test_hidden_keys_are_still_readable(self):
        """_ 前缀只是 UI 过滤，代码里照常能读到值。"""
        task = build_task(TreasureUnlockTask)
        task.config = dict(task.default_config)
        self.assertEqual(task.config.get("_校准稳定帧数", 5), 5)
        self.assertEqual(task.config.get("_检测间隔(秒)", 0.08), 0.08)


if __name__ == "__main__":
    unittest.main()
