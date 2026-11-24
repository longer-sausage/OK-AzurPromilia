"""任务队列测试：模型校验、存取、执行顺序、失败策略、预算标记。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.core.queue import (
    QueueFormatError,
    QueueItem,
    QueueRunner,
    TaskQueue,
    load_queue,
    save_queue,
)
from src.core.queue.model import list_queues, queue_path


def make_queue(*items, stop_on_failure=True, name="测试队列"):
    return TaskQueue(
        name=name,
        items=tuple(items),
        stop_on_failure=stop_on_failure,
    )


class TestQueueItem(unittest.TestCase):
    def test_valid_item(self):
        item = QueueItem(kind="flow", ref="日常刷图", budget_minutes=5, name="刷图")
        self.assertEqual(item.label, "刷图")
        self.assertAlmostEqual(item.budget_seconds, 300)

    def test_zero_budget_means_unlimited(self):
        self.assertIsNone(QueueItem(kind="task", ref="DailyTask").budget_seconds)

    def test_invalid_kind(self):
        with self.assertRaises(QueueFormatError):
            QueueItem(kind="python", ref="x")

    def test_empty_ref(self):
        with self.assertRaises(QueueFormatError):
            QueueItem(kind="flow", ref="  ")

    def test_negative_budget_rejected(self):
        with self.assertRaises(QueueFormatError):
            QueueItem.from_dict({"kind": "flow", "ref": "a", "budget_minutes": -1})

    def test_roundtrip(self):
        item = QueueItem(kind="task", ref="DailyTask", budget_minutes=2.5, enabled=False, name="日常")
        again = QueueItem.from_dict(item.to_dict())
        self.assertEqual(again, item)


class TestTaskQueueModel(unittest.TestCase):
    def test_from_dict_validates_items(self):
        queue = TaskQueue.from_dict(
            {"name": "q", "items": [{"kind": "flow", "ref": "a"}, {"kind": "task", "ref": "DailyTask"}]}
        )
        self.assertEqual(len(queue.items), 2)
        self.assertTrue(queue.stop_on_failure)

    def test_missing_items(self):
        with self.assertRaises(QueueFormatError):
            TaskQueue.from_dict({"name": "q"})

    def test_bad_item_index_reported(self):
        with self.assertRaises(QueueFormatError) as ctx:
            TaskQueue.from_dict({"items": [{"kind": "flow", "ref": "a"}, {"kind": "nope", "ref": "b"}]})
        self.assertIn("items[1]", str(ctx.exception))

    def test_enabled_items_and_budget(self):
        queue = make_queue(
            QueueItem(kind="flow", ref="a", budget_minutes=1),
            QueueItem(kind="flow", ref="b", budget_minutes=2, enabled=False),
        )
        self.assertEqual(len(queue.enabled_items), 1)
        self.assertAlmostEqual(queue.total_budget_minutes(), 1.0)

    def test_empty_queue_detection(self):
        self.assertTrue(make_queue().is_empty())

    def test_roundtrip_dict(self):
        queue = make_queue(QueueItem(kind="flow", ref="a", budget_minutes=3))
        again = TaskQueue.from_dict(json.loads(queue.to_json()))
        self.assertEqual(again.to_dict(), queue.to_dict())


class TestQueueStorage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "queues"

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_and_load(self):
        queue = make_queue(QueueItem(kind="flow", ref="a"), name="早上")
        path = save_queue(queue, self.folder)
        self.assertTrue(path.exists())
        loaded = load_queue("早上", self.folder)
        self.assertEqual(loaded.name, "早上")
        self.assertEqual(loaded.items[0].ref, "a")

    def test_list_queues(self):
        for name in ("a", "b"):
            save_queue(make_queue(name=name), self.folder)
        self.assertEqual(list_queues(self.folder), ["a", "b"])

    def test_list_missing_folder(self):
        self.assertEqual(list_queues(self.folder), [])

    def test_load_missing_raises(self):
        with self.assertRaises(QueueFormatError):
            load_queue("nope", self.folder)

    def test_unsafe_name_is_sanitized(self):
        path = queue_path("../evil", self.folder)
        self.assertEqual(path.parent, self.folder)
        self.assertTrue(path.name.endswith(".json"))

    def test_corrupt_file_raises(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "bad.json").write_text("{ not json", encoding="utf-8")
        with self.assertRaises(QueueFormatError):
            load_queue("bad", self.folder)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TestQueueRunner(unittest.TestCase):
    def _runner(self, queue, behavior, clock=None):
        calls = []
        logs = []

        def resolver(item):
            if item.ref not in behavior:
                return None
            action = behavior[item.ref]
            if action is None:
                return None

            def run(budget):
                calls.append((item.ref, budget))
                return action()

            return run

        runner = QueueRunner(queue, resolver, log=logs.append, clock=clock)
        return runner, calls, logs

    def test_runs_in_order(self):
        queue = make_queue(
            QueueItem(kind="flow", ref="a"),
            QueueItem(kind="task", ref="b"),
        )
        runner, calls, _logs = self._runner(queue, {"a": lambda: True, "b": lambda: True})
        result = runner.run()
        self.assertTrue(result.ok)
        self.assertEqual([call[0] for call in calls], ["a", "b"])
        self.assertEqual(result.completed, 2)
        self.assertIn("成功 2 项", result.summary())

    def test_budget_passed_to_item(self):
        queue = make_queue(QueueItem(kind="flow", ref="a", budget_minutes=2))
        runner, calls, _ = self._runner(queue, {"a": lambda: True})
        runner.run()
        self.assertAlmostEqual(calls[0][1], 120.0)

    def test_stop_on_failure(self):
        queue = make_queue(
            QueueItem(kind="flow", ref="a"),
            QueueItem(kind="flow", ref="b"),
            QueueItem(kind="flow", ref="c"),
        )
        runner, calls, logs = self._runner(queue, {"a": lambda: True, "b": lambda: False, "c": lambda: True})
        result = runner.run()
        self.assertFalse(result.ok)
        self.assertEqual([call[0] for call in calls], ["a", "b"])
        self.assertEqual(result.failed, 1)
        self.assertTrue(any("失败即停" in log for log in logs))

    def test_continue_on_failure(self):
        queue = make_queue(
            QueueItem(kind="flow", ref="a"),
            QueueItem(kind="flow", ref="b"),
            stop_on_failure=False,
        )
        runner, calls, _ = self._runner(queue, {"a": lambda: False, "b": lambda: True})
        result = runner.run()
        self.assertFalse(result.ok)
        self.assertEqual([call[0] for call in calls], ["a", "b"])
        self.assertEqual(result.completed, 1)

    def test_disabled_item_is_skipped(self):
        queue = make_queue(
            QueueItem(kind="flow", ref="a", enabled=False),
            QueueItem(kind="flow", ref="b"),
        )
        runner, calls, _ = self._runner(queue, {"a": lambda: True, "b": lambda: True})
        result = runner.run()
        self.assertEqual([call[0] for call in calls], ["b"])
        self.assertEqual(len(result.results), 2)
        self.assertTrue(result.results[0].skipped)

    def test_unknown_item_fails(self):
        queue = make_queue(QueueItem(kind="scheme", ref="轮椅1"))
        runner, calls, _ = self._runner(queue, {})
        result = runner.run()
        self.assertFalse(result.ok)
        self.assertEqual(calls, [])
        self.assertIn("找不到", result.results[0].error)

    def test_item_exception_is_captured(self):
        def boom():
            raise RuntimeError("炸了")

        queue = make_queue(QueueItem(kind="flow", ref="a"))
        runner, _calls, _ = self._runner(queue, {"a": boom})
        result = runner.run()
        self.assertFalse(result.ok)


class TestQueueBudget(unittest.TestCase):
    """队列总预算与「任务类项剩余时间不够就不开始」。"""

    def _runner(self, queue, behavior, clock, **kwargs):
        calls = []
        logs = []

        def resolver(item):
            action = behavior.get(item.ref)
            if action is None:
                return None

            def run(budget):
                calls.append((item.ref, budget))
                return action(budget)

            return run

        runner = QueueRunner(queue, resolver, log=logs.append, clock=clock, **kwargs)
        return runner, calls, logs

    def test_total_budget_truncates_remaining_items(self):
        clock = FakeClock()
        queue = make_queue(QueueItem(kind="flow", ref="a"), QueueItem(kind="flow", ref="b"))

        def slow(_budget):
            clock.now += 700
            return True

        runner, calls, _logs = self._runner(
            queue, {"a": slow, "b": lambda budget: True}, clock, total_budget_seconds=600
        )
        result = runner.run()
        self.assertEqual([call[0] for call in calls], ["a"])
        self.assertTrue(result.results[1].skipped)
        self.assertIn("总预算已用完", result.results[1].error or "")

    def test_budget_passed_is_remaining_total(self):
        clock = FakeClock()
        queue = make_queue(QueueItem(kind="flow", ref="a"))

        runner, calls, _ = self._runner(
            queue, {"a": lambda budget: True}, clock, total_budget_seconds=300
        )
        runner.run()
        self.assertAlmostEqual(calls[0][1], 300.0)

    def test_item_budget_capped_by_remaining_total(self):
        clock = FakeClock()
        queue = make_queue(QueueItem(kind="flow", ref="a", budget_minutes=30))

        runner, calls, _ = self._runner(
            queue, {"a": lambda budget: True}, clock, total_budget_seconds=120
        )
        runner.run()
        self.assertAlmostEqual(calls[0][1], 120.0)  # 取更小的那个

    def test_no_total_budget_keeps_old_behaviour(self):
        clock = FakeClock()
        queue = make_queue(QueueItem(kind="flow", ref="a"), QueueItem(kind="flow", ref="b"))
        runner, calls, _ = self._runner(
            queue, {"a": lambda budget: True, "b": lambda budget: True}, clock
        )
        result = runner.run()
        self.assertEqual([call[0] for call in calls], ["a", "b"])
        self.assertIsNone(calls[0][1])
        self.assertFalse(any(item.skipped for item in result.results))

    def test_estimate_skips_task_when_remaining_too_small(self):
        clock = FakeClock()
        queue = make_queue(QueueItem(kind="task", ref="HomeDailyTask"))
        runner, calls, logs = self._runner(
            queue,
            {"HomeDailyTask": lambda budget: True},
            clock,
            total_budget_seconds=180,
            estimate=lambda ref: 600.0 if ref == "HomeDailyTask" else 0.0,
        )
        result = runner.run()
        self.assertEqual(calls, [])
        self.assertTrue(result.results[0].skipped)
        self.assertIn("预算不足", result.results[0].summary())
        self.assertTrue(any("预算不足" in log for log in logs))

    def test_estimate_does_not_block_flow_items(self):
        """流程类能中途被打断，所以不该因为预估超时就跳过。"""
        clock = FakeClock()
        queue = make_queue(QueueItem(kind="flow", ref="长流程"))
        runner, calls, _ = self._runner(
            queue,
            {"长流程": lambda budget: True},
            clock,
            total_budget_seconds=180,
            estimate=lambda ref: 600.0,
        )
        result = runner.run()
        self.assertEqual([call[0] for call in calls], ["长流程"])
        self.assertFalse(result.results[0].skipped)

    def test_estimate_allows_task_when_enough_time(self):
        clock = FakeClock()
        queue = make_queue(QueueItem(kind="task", ref="快任务"))
        runner, calls, _ = self._runner(
            queue,
            {"快任务": lambda budget: True},
            clock,
            total_budget_seconds=1800,
            estimate=lambda ref: 60.0,
        )
        runner.run()
        self.assertEqual([call[0] for call in calls], ["快任务"])

    def test_estimator_error_is_ignored(self):
        clock = FakeClock()
        queue = make_queue(QueueItem(kind="task", ref="A"))

        def broken(_ref):
            raise RuntimeError("估不出来")

        runner, calls, _ = self._runner(
            queue, {"A": lambda budget: True}, clock, total_budget_seconds=60, estimate=broken
        )
        runner.run()
        self.assertEqual([call[0] for call in calls], ["A"])


class TestQueueRunnerDetails(unittest.TestCase):
    """原有的一些细节断言（预算标记、用时统计、解析异常）。"""

    def _runner(self, queue, behavior, clock=None):
        calls = []

        def resolver(item):
            action = behavior.get(item.ref)
            if action is None:
                return None

            def run(budget):
                calls.append((item.ref, budget))
                return action()

            return run

        return QueueRunner(queue, resolver, clock=clock), calls

    def test_budget_exceeded_is_flagged(self):
        clock = FakeClock()

        def slow():
            clock.now += 200
            return True

        queue = make_queue(QueueItem(kind="flow", ref="a", budget_minutes=1))
        runner, _calls = self._runner(queue, {"a": slow}, clock=clock)
        result = runner.run()
        self.assertTrue(result.ok)
        self.assertTrue(result.results[0].budget_exceeded)
        self.assertIn("超出预算", result.results[0].summary())

    def test_elapsed_measured(self):
        clock = FakeClock()

        def slow():
            clock.now += 3
            return True

        queue = make_queue(QueueItem(kind="flow", ref="a"))
        runner, _calls = self._runner(queue, {"a": slow}, clock=clock)
        result = runner.run()
        self.assertAlmostEqual(result.elapsed, 3.0)

    def test_resolver_exception_is_captured(self):
        def bad_resolver(item):
            raise ValueError("解析失败")

        queue = make_queue(QueueItem(kind="flow", ref="a"))
        result = QueueRunner(queue, bad_resolver).run()
        self.assertFalse(result.ok)
        self.assertIn("解析失败", result.results[0].error)


if __name__ == "__main__":
    unittest.main()
