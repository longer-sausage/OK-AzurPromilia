"""「流程 / 队列」任务的运行报表接线测试。

报表数据同时喂给任务栏进度与 HUD 的「预计剩余」，所以这里的要点是：
**成功、返回 False、抛异常三种结局都要留痕**，且写报表本身不能影响执行。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.ledger import RunLog
from src.tasks.onetime.flow_task import JOB_FLOW, JOB_QUEUE, FlowTask
from src.tasks.onetime.home_daily_task import HomeDailyTask


class Harness:
    """把任务从框架里剥出来：假时钟 + 假报表 + 假执行函数。"""

    def __init__(self, job=JOB_FLOW, name="我的流程"):
        self.root_tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.root_tmp.name)
        self.run_log = RunLog(self.root / "runs.jsonl")

        task = FlowTask.__new__(FlowTask)
        task.config = {"任务类型": job}
        task.config["队列文件" if job == JOB_QUEUE else "流程文件"] = name
        task._run_log_instance = self.run_log
        task.now = 100.0
        task.active_time = lambda: task.now
        task.logs: list[str] = []
        task.log_info = lambda message, notify=False, **kwargs: task.logs.append(str(message))
        self.task = task

    def close(self):
        self.root_tmp.cleanup()


def make_run(task, duration=0.0, result=True, error=None):
    """造一个假执行函数：推进假时钟，然后返回结果或抛异常。"""

    def fake_run(budget=None):
        task.now += duration
        if error is not None:
            raise error
        return result

    return fake_run


class TestFlowTaskRunLog(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.task = self.harness.task

    def tearDown(self):
        self.harness.close()

    def test_success_is_recorded(self):
        self.task.run_flow = make_run(self.task, duration=150.0)
        self.assertTrue(FlowTask.run(self.task))
        rows = self.harness.run_log.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task"], "我的流程")
        self.assertTrue(rows[0]["ok"])
        self.assertAlmostEqual(rows[0]["seconds"], 150.0)
        self.assertEqual(rows[0]["note"], "")

    def test_false_is_recorded_with_note(self):
        self.task.run_flow = make_run(self.task, result=False)
        self.assertFalse(FlowTask.run(self.task))
        row = self.harness.run_log.rows()[0]
        self.assertFalse(row["ok"])
        self.assertIn("返回 False", row["note"])

    def test_exception_is_recorded_and_reraised(self):
        self.task.run_flow = make_run(self.task, error=RuntimeError("炸了"))
        with self.assertRaises(RuntimeError):
            FlowTask.run(self.task)
        row = self.harness.run_log.rows()[0]
        self.assertFalse(row["ok"])
        self.assertIn("RuntimeError", row["note"])
        self.assertIn("炸了", row["note"])

    def test_queue_job_uses_queue_name(self):
        harness = Harness(job=JOB_QUEUE, name="一条龙")
        try:
            harness.task.run_queue = make_run(harness.task, duration=30.0)
            self.assertTrue(FlowTask.run(harness.task))
            row = harness.run_log.rows()[0]
            self.assertEqual(row["task"], "一条龙")
            self.assertAlmostEqual(row["seconds"], 30.0)
        finally:
            harness.close()

    def test_missing_name_falls_back_to_job(self):
        self.task.config["流程文件"] = "   "
        self.assertTrue(FlowTask._job_name(self.task))
        self.task.run_flow = make_run(self.task)
        FlowTask.run(self.task)
        self.assertEqual(self.harness.run_log.rows()[0]["task"], JOB_FLOW)

    def test_record_failure_does_not_break_run(self):
        self.task.run_flow = make_run(self.task)
        broken = mock.Mock()
        broken.record.side_effect = OSError("磁盘满了")
        self.task._run_log_instance = broken
        self.assertTrue(FlowTask.run(self.task))  # 写报表炸了也不能影响执行结果
        self.assertTrue(broken.record.called)

    def test_clock_falls_back_to_monotonic(self):
        def boom():
            raise RuntimeError("没有 active_time")

        self.task.active_time = boom
        self.assertIsInstance(FlowTask._clock(self.task), float)

    def test_estimate_available_after_run(self):
        """跑过一次之后，报表能给出该项的平均用时（任务栏/HUD 就用它）。"""
        self.task.run_flow = make_run(self.task, duration=120.0)
        FlowTask.run(self.task)
        self.assertGreater(self.harness.run_log.estimate_seconds("我的流程"), 0)


class TestQueueEstimate(unittest.TestCase):
    """队列预算判断用的预估：按运行报表的历史均值，类名与显示名都认。"""

    def _fake_task(self):
        """造一个类名就是 ``HomeDailyTask`` 的实例（不跑真实初始化）。"""

        def init(self):
            self.name = "家园每日"

        cls = type("HomeDailyTask", (HomeDailyTask,), {"__init__": init})
        return cls()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        fake = self._fake_task()

        class Executor:
            def __init__(self):
                self.onetime_tasks = [fake]
                self.trigger_tasks = []

        self.task = FlowTask.__new__(FlowTask)
        self.task.__dict__["_executor"] = Executor()
        self.task._run_log_instance = RunLog(self.root / "runs.jsonl")
        self.task._run_log_instance.record("家园每日", True, 300.0)

    def tearDown(self):
        self._tmp.cleanup()

    def test_by_class_name(self):
        self.assertAlmostEqual(FlowTask._estimate_task_seconds(self.task, "HomeDailyTask"), 300.0)

    def test_by_display_name(self):
        self.assertAlmostEqual(FlowTask._estimate_task_seconds(self.task, "家园每日"), 300.0)

    def test_unknown_returns_zero(self):
        self.assertEqual(FlowTask._estimate_task_seconds(self.task, "Nope"), 0.0)


if __name__ == "__main__":
    unittest.main()
