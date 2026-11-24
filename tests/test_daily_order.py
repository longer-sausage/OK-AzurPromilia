"""一条龙顺序测试：配置串解析、重排规则、移动、与 DailyTask 的接线。"""

import unittest
from types import SimpleNamespace

from src.tasks.daily.order import (
    ORDER_KEY,
    available_names,
    move_item,
    order_plan_items,
    order_text,
    split_order,
)
from src.tasks.onetime.daily_task import DEFAULT_ORDER, DailyTask


def plan():
    return [("家园每日", "h"), ("委托每日", "c"), ("每日收菜", "d")]


class TestSplitOrder(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(split_order("家园每日,委托每日"), ["家园每日", "委托每日"])

    def test_mixed_separators(self):
        self.assertEqual(split_order("a，b、c;d|e"), ["a", "b", "c", "d", "e"])

    def test_strips_and_dedupes(self):
        self.assertEqual(split_order(" a , a ,b "), ["a", "b"])

    def test_empty(self):
        self.assertEqual(split_order(""), [])
        self.assertEqual(split_order(None), [])


class TestOrderPlanItems(unittest.TestCase):
    def test_reorder(self):
        ordered = order_plan_items(plan(), "每日收菜,家园每日")
        self.assertEqual([name for name, _ in ordered], ["每日收菜", "家园每日", "委托每日"])

    def test_unknown_names_ignored(self):
        ordered = order_plan_items(plan(), "幽灵任务,委托每日")
        self.assertEqual([name for name, _ in ordered], ["委托每日", "家园每日", "每日收菜"])

    def test_missing_names_appended_in_original_order(self):
        ordered = order_plan_items(plan(), "每日收菜")
        self.assertEqual([name for name, _ in ordered], ["每日收菜", "家园每日", "委托每日"])

    def test_empty_order_keeps_default(self):
        ordered = order_plan_items(plan(), "")
        self.assertEqual([name for name, _ in ordered], ["家园每日", "委托每日", "每日收菜"])

    def test_list_input(self):
        ordered = order_plan_items(plan(), ["委托每日", "每日收菜"])
        self.assertEqual([name for name, _ in ordered], ["委托每日", "每日收菜", "家园每日"])

    def test_no_items(self):
        self.assertEqual(order_plan_items([], "x"), [])

    def test_items_are_preserved(self):
        ordered = order_plan_items(plan(), "每日收菜")
        self.assertEqual(ordered[0][1], "d")

    def test_order_text_roundtrip(self):
        text = order_text(plan())
        self.assertEqual(text, "家园每日,委托每日,每日收菜")
        self.assertEqual([name for name, _ in order_plan_items(plan(), text)], available_names(plan()))


class TestMoveItem(unittest.TestCase):
    def test_up(self):
        self.assertEqual(move_item(["a", "b", "c"], "b", -1), ["b", "a", "c"])

    def test_down(self):
        self.assertEqual(move_item(["a", "b", "c"], "b", 1), ["a", "c", "b"])

    def test_clamped_at_edges(self):
        self.assertEqual(move_item(["a", "b"], "a", -3), ["a", "b"])
        self.assertEqual(move_item(["a", "b"], "b", 5), ["a", "b"])

    def test_unknown_name(self):
        self.assertEqual(move_item(["a"], "zz", 1), ["a"])


class TestDailyTaskWiring(unittest.TestCase):
    def _task(self, config):
        task = DailyTask.__new__(DailyTask)
        task.config = dict(config)
        task.default_config = {ORDER_KEY: DEFAULT_ORDER}
        task.home_daily = SimpleNamespace(plan_item=lambda: ("家园每日", "h"))
        task.commission_daily = SimpleNamespace(plan_item=lambda: ("委托每日", "c"))
        task.claim_daily = SimpleNamespace(plan_item=lambda: ("每日收菜", "d"))
        return task

    def test_uses_configured_order(self):
        task = self._task({ORDER_KEY: "每日收菜,委托每日"})
        self.assertEqual([name for name, _ in task.build_task_plan()], ["每日收菜", "委托每日", "家园每日"])

    def test_default_order_when_unset(self):
        task = self._task({})
        self.assertEqual([name for name, _ in task.build_task_plan()], split_order(DEFAULT_ORDER))

    def test_default_order_matches_documented_sequence(self):
        self.assertEqual(DEFAULT_ORDER, "家园每日,委托每日,每日收菜")

    def test_order_key_is_registered(self):
        self.assertEqual(ORDER_KEY, "任务顺序")


if __name__ == "__main__":
    unittest.main()
