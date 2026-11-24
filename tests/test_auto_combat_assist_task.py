"""实时辅助任务测试：序列解析、冷却、前置模板、连续次数上限、轮转。

框架调用（按键 / 识图 / 时钟）全部 stub，只验证调度逻辑。
"""

import unittest

from src.tasks.trigger.auto_combat_assist_task import (
    DEFAULT_MAX_ACTIONS,
    DEFAULT_SEQUENCE,
    AutoCombatAssistTask,
    parse_sequence,
)

DEFAULT_CONFIG = {
    "按键序列": "q,e,r",
    "动作间隔(秒)": 0.8,
    "前置模板": "",
    "最大连续次数": 3,
    "记录日志": False,
    "_按下时长(秒)": 0.05,
}


class Harness:
    """把任务从框架里剥出来：假时钟 + 假按键 + 假识图。"""

    def __init__(self, visible=True, **config):
        task = AutoCombatAssistTask.__new__(AutoCombatAssistTask)
        task.config = dict(DEFAULT_CONFIG)
        task.config.update(config)
        task._next_index = 0
        task._last_action_at = None
        task.now = 0.0
        task.pressed: list[tuple[str, float]] = []
        task.logs: list[str] = []
        task.visible = visible

        task.active_time = lambda: task.now
        task.press_key = lambda key, down_time=0.02, **kwargs: task.pressed.append((key, down_time))
        task.log_info = lambda message, notify=False, **kwargs: task.logs.append(str(message))
        task.find_one = lambda feature=None, **kwargs: object() if task.visible else None
        self.task = task

    def advance(self, seconds: float):
        self.task.now += seconds

    def run(self):
        return self.task.run()


class TestParseSequence(unittest.TestCase):
    def test_comma(self):
        self.assertEqual(parse_sequence("q,e,r"), ["q", "e", "r"])

    def test_mixed_separators(self):
        self.assertEqual(parse_sequence("q，e;r|f"), ["q", "e", "r", "f"])

    def test_spaces_and_empty(self):
        self.assertEqual(parse_sequence(" q , , e "), ["q", "e"])

    def test_dedupes_adjacent(self):
        self.assertEqual(parse_sequence("q,q,e"), ["q", "e"])

    def test_empty(self):
        self.assertEqual(parse_sequence(""), [])
        self.assertEqual(parse_sequence(None), [])


class TestAssistBehaviour(unittest.TestCase):
    def test_presses_sequence_in_order(self):
        h = Harness()
        h.advance(10)
        self.assertTrue(h.run())
        # 一次 run 内按满「最大连续次数」，顺序是配置序列
        self.assertEqual([key for key, _ in h.task.pressed], ["q", "e", "r"])
        # 冷却过后继续轮转
        h.advance(1.0)
        h.run()
        self.assertEqual([key for key, _ in h.task.pressed], ["q", "e", "r", "q", "e", "r"])

    def test_cooldown_blocks_early_action(self):
        h = Harness()
        h.advance(10)
        h.run()
        pressed_before = len(h.task.pressed)
        h.advance(0.3)
        self.assertFalse(h.run())
        self.assertEqual(len(h.task.pressed), pressed_before)

    def test_press_duration_from_config(self):
        h = Harness()
        h.advance(10)
        h.run()
        self.assertAlmostEqual(h.task.pressed[0][1], 0.05)

    def test_max_actions_per_run(self):
        h = Harness(**{"最大连续次数": 2})
        h.advance(10)
        h.run()
        self.assertEqual(len(h.task.pressed), 2)

    def test_gate_template_blocks(self):
        h = Harness(visible=False, **{"前置模板": "auto_combat_check"})
        h.advance(10)
        self.assertFalse(h.run())
        self.assertEqual(h.task.pressed, [])

    def test_gate_template_allows(self):
        h = Harness(visible=True, **{"前置模板": "auto_combat_check"})
        h.advance(10)
        self.assertTrue(h.run())

    def test_empty_sequence_does_nothing(self):
        h = Harness(**{"按键序列": ""})
        h.advance(10)
        self.assertFalse(h.run())
        self.assertEqual(h.task.pressed, [])

    def test_rotates_across_runs(self):
        h = Harness(**{"最大连续次数": 1})
        for _ in range(5):
            h.advance(1.0)
            h.run()
        self.assertEqual([key for key, _ in h.task.pressed], ["q", "e", "r", "q", "e"])

    def test_logging_flag(self):
        h = Harness(**{"记录日志": True, "最大连续次数": 1})
        h.advance(10)
        h.run()
        self.assertTrue(any("实时辅助" in log for log in h.task.logs))

    def test_reset_state(self):
        h = Harness(**{"最大连续次数": 1})
        h.advance(10)
        h.run()
        h.task.reset_state()
        self.assertEqual(h.task._next_index, 0)
        self.assertIsNone(h.task._last_action_at)

    def test_defaults(self):
        self.assertEqual(DEFAULT_SEQUENCE, "q,e,r")
        self.assertEqual(DEFAULT_MAX_ACTIONS, 3)

    def test_task_is_registered(self):
        from src.config import config

        pairs = {(module, name) for module, name in config["trigger_tasks"]}
        self.assertIn(("src.tasks.trigger.auto_combat_assist_task", "AutoCombatAssistTask"), pairs)


if __name__ == "__main__":
    unittest.main()
