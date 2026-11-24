"""结算记账任务与运行报表接入的测试。

框架调用（OCR / 日志 / 计时）全部用 stub 替换，只验证：
结算文字 → 收支条目 → ledger.jsonl；任务项执行 → runs.jsonl。
"""

import tempfile
import unittest
from pathlib import Path

from src.core.ledger import Ledger, RunLog
from src.tasks.daily.daily_task_runner import DailyTaskRunner
from src.tasks.onetime.settlement_task import SettlementTask


class SettlementHarness:
    """把 SettlementTask 从框架里剥出来：假 OCR + 假日志 + 临时记账本。"""

    def __init__(self, root: Path, text: str = "", config: dict | None = None):
        task = SettlementTask.__new__(SettlementTask)
        task.config = {"记录来源": "结算界面", "最低置信度": 0.0}
        task.config.update(config or {})
        task.logs: list[str] = []
        task.log_info = lambda message, notify=False, **kwargs: task.logs.append(str(message))
        task._ledger = Ledger(root / "ledger.jsonl")
        task._text = text
        task.ocr_lines = lambda **kwargs: text.splitlines()
        self.task = task
        self.root = root


class TestSettlementTask(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_text_happy_path(self):
        harness = SettlementHarness(self.root)
        ok = harness.task.record_text("获得的奖励：尤尼 ×1,500\n12 × 波力果", source="结算界面")

        self.assertTrue(ok)
        entries = harness.task.ledger().entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].source, "结算界面")
        self.assertTrue(any("已记录 2 条" in log for log in harness.task.logs))
        self.assertTrue(any("今日" in log for log in harness.task.logs))

    def test_record_text_without_match(self):
        harness = SettlementHarness(self.root)
        self.assertFalse(harness.task.record_text("今天天气不错"))
        self.assertEqual(harness.task.ledger().entries(), [])
        self.assertTrue(any("没有识别到" in log for log in harness.task.logs))

    def test_expense_line(self):
        harness = SettlementHarness(self.root)
        harness.task.record_text("消耗：星脉结晶 ×3")
        totals = harness.task.ledger().totals()
        self.assertAlmostEqual(totals["expense"], 3)
        self.assertAlmostEqual(totals["income"], 0)

    def test_run_uses_ocr_lines(self):
        harness = SettlementHarness(self.root, text="尤尼 ×100\n消耗：星脉结晶 ×2")
        self.assertTrue(harness.task.run())
        self.assertEqual(len(harness.task.ledger().entries()), 2)

    def test_run_without_text(self):
        harness = SettlementHarness(self.root, text="")
        self.assertFalse(harness.task.run())
        self.assertTrue(any("没有识别到文字" in log for log in harness.task.logs))

    def test_source_comes_from_config(self):
        harness = SettlementHarness(self.root, text="尤尼 ×5", config={"记录来源": "商店"})
        harness.task.run()
        self.assertEqual(harness.task.ledger().entries()[0].source, "商店")

    def test_long_preview_is_truncated(self):
        harness = SettlementHarness(self.root)
        text = "\n".join(f"材料{i} ×{i + 1}" for i in range(8))
        harness.task.record_text(text)
        message = next(log for log in harness.task.logs if "已记录" in log)
        self.assertIn("等 8 条", message)


class RunnerTaskStub:
    """DailyTaskRunner 需要的最小 task 表面。"""

    def __init__(self, clock):
        self.config = {}
        self.current_user = ""
        self.current_account_id = ""
        self.logs: list[str] = []
        self.ensure_main_calls = 0
        self.screenshots: list[str] = []
        self._clock = clock

    def log_info(self, message, notify=False, **kwargs):
        self.logs.append(str(message))

    def tr(self, message):
        return message

    def ensure_main(self, **kwargs):
        self.ensure_main_calls += 1

    def screenshot(self, name=None, **kwargs):
        self.screenshots.append(str(name))

    def active_time(self):
        return self._clock[0]


class Clock:
    def __init__(self, step=10.0):
        self.value = 0.0
        self.step = step

    def advance(self, amount=None):
        self.value += self.step if amount is None else amount
        return self.value

    def as_list(self):
        return self._values


class TestRunnerReporting(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _runner(self, items, clock_holder):
        task = RunnerTaskStub(clock_holder)
        run_log = RunLog(self.root / "runs.jsonl")
        runner = DailyTaskRunner(task, items, ensure_main_kwargs={"time_out": 1}, run_log=run_log)
        # 关掉开关判定，直接跑清单里的每一项
        task.config = {name: True for name, *_ in items}
        return runner, run_log

    def test_success_and_failure_are_recorded(self):
        clock = [0.0]

        def slow_success():
            clock[0] += 42.0
            return True

        def failure():
            clock[0] += 8.0
            return False

        items = [("taskA", slow_success), ("taskB", failure)]
        runner, run_log = self._runner(items, clock)
        for key, func in items:
            runner.execute_task(key, func)

        rows = run_log.rows()
        self.assertEqual([row["task"] for row in rows], ["taskA", "taskB"])
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(rows[1]["ok"])
        self.assertAlmostEqual(rows[0]["seconds"], 42.0)
        self.assertAlmostEqual(rows[1]["seconds"], 8.0)

    def test_disabled_item_is_not_recorded(self):
        clock = [0.0]
        runner, run_log = self._runner([("taskA", lambda: True)], clock)
        runner.task.config = {"taskA": False}
        runner.execute_task("taskA", lambda: True)
        self.assertEqual(run_log.rows(), [])

    def test_stats_feed_estimate(self):
        clock = [0.0]

        def ok():
            clock[0] += 30.0
            return True

        run_log = RunLog(self.root / "runs.jsonl")
        for _ in range(2):
            task = RunnerTaskStub(clock)
            task.config = {"taskA": True}
            runner = DailyTaskRunner(task, [("taskA", ok)], run_log=run_log)
            runner.execute_task("taskA", ok)

        self.assertAlmostEqual(run_log.estimate_seconds("taskA"), 30.0)
        self.assertAlmostEqual(run_log.stats("taskA")["success_rate"], 1.0)

    def test_record_failure_does_not_break_runner(self):
        class BrokenLog:
            def record(self, *args, **kwargs):
                raise RuntimeError("disk full")

        clock = [0.0]
        task = RunnerTaskStub(clock)
        task.config = {"taskA": True}
        runner = DailyTaskRunner(task, [("taskA", lambda: True)], run_log=BrokenLog())
        self.assertTrue(runner.execute_task("taskA", lambda: True))


if __name__ == "__main__":
    unittest.main()
