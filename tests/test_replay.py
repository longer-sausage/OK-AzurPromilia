"""轮椅方案测试：分段转换、拼接规则、循环包装、校验、存取、可执行性。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.flow import FlowContext, FlowExecutor
from src.core.replay import (
    DEFAULT_BETWEEN_WAIT,
    SCHEME_FOLDER,
    Scheme,
    SchemeFormatError,
    list_schemes,
    load_scheme,
    save_scheme,
    scheme_path,
    scheme_to_flow_file,
)


def click_event(x=0.5, y=0.5, start=0.0, duration=0.1, button="left"):
    return [
        {"type": "mouse_down", "x": x, "y": y, "button": button, "time": start},
        {"type": "mouse_up", "x": x, "y": y, "button": button, "time": start + duration},
    ]


def key_event(key="f", start=0.0, duration=0.3):
    return [
        {"type": "key_down", "key": key, "time": start},
        {"type": "key_up", "key": key, "time": start + duration},
    ]


def two_segment_scheme(**overrides):
    data = {
        "name": "测试轮椅",
        "loop": 1,
        "between_wait": 0.5,
        "segments": [
            {"label": "进场", "events": click_event(0.1, 0.1)},
            {"label": "输出", "events": key_event("e")},
        ],
    }
    data.update(overrides)
    return data


class TestSchemeModel(unittest.TestCase):
    def test_parse(self):
        scheme = Scheme.from_dict(two_segment_scheme())
        self.assertEqual(scheme.name, "测试轮椅")
        self.assertEqual(len(scheme.segments), 2)
        self.assertEqual(scheme.segments[0].label, "进场")

    def test_missing_segments(self):
        with self.assertRaises(SchemeFormatError):
            Scheme.from_dict({"name": "x"})

    def test_empty_segments(self):
        with self.assertRaises(SchemeFormatError):
            Scheme.from_dict({"segments": []})

    def test_loop_bounds(self):
        for loop in (0, 1001):
            with self.subTest(loop=loop), self.assertRaises(SchemeFormatError):
                Scheme.from_dict(two_segment_scheme(loop=loop))

    def test_bad_between_wait(self):
        with self.assertRaises(SchemeFormatError):
            Scheme.from_dict(two_segment_scheme(between_wait="慢一点"))

    def test_negative_between_wait_rejected(self):
        """负间隔会被静默当成 0，不如直接拒绝（用户填错了应该知道）。"""
        with self.assertRaises(SchemeFormatError) as ctx:
            Scheme.from_dict(two_segment_scheme(between_wait=-1.5))
        self.assertIn("不能是负数", str(ctx.exception))

    def test_roundtrip(self):
        scheme = Scheme.from_dict(two_segment_scheme())
        again = Scheme.from_dict(json.loads(scheme.to_json()))
        self.assertEqual(again.to_dict(), scheme.to_dict())


class TestSchemeBuild(unittest.TestCase):
    def test_segments_are_concatenated_with_wait(self):
        scheme = Scheme.from_dict(two_segment_scheme())
        steps, report = scheme.build_steps()
        self.assertEqual([step["type"] for step in steps], ["click", "wait", "key"])
        self.assertAlmostEqual(steps[1]["seconds"], 0.5)
        self.assertEqual(report.segments, 2)
        self.assertEqual(report.converted, 2)

    def test_zero_between_wait_keeps_steps_adjacent(self):
        scheme = Scheme.from_dict(two_segment_scheme(between_wait=0))
        steps, _report = scheme.build_steps()
        self.assertEqual([step["type"] for step in steps], ["click", "key"])

    def test_loop_wraps_steps(self):
        scheme = Scheme.from_dict(two_segment_scheme(loop=3))
        steps, _report = scheme.build_steps()
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["type"], "loop")
        self.assertEqual(steps[0]["times"], 3)

    def test_skipped_inputs_are_counted(self):
        right_click = click_event(button="right")
        scheme = Scheme.from_dict(
            {"segments": [{"label": "右键", "events": right_click}, {"events": click_event()}]}
        )
        _steps, report = scheme.build_steps()
        self.assertEqual(report.skipped, 1)
        self.assertIn("跳过 1", report.summary())

    def test_steps_segment_is_used_directly(self):
        scheme = Scheme.from_dict(
            {"segments": [{"steps": [{"type": "wait", "seconds": 0.1}]}, {"steps": [{"type": "click", "x": 0.2, "y": 0.3}]}]}
        )
        steps, _report = scheme.build_steps()
        self.assertEqual([step["type"] for step in steps], ["wait", "wait", "click"])

    def test_empty_segment_is_noted(self):
        scheme = Scheme.from_dict({"segments": [{"label": "空", "events": []}, {"events": click_event()}]})
        steps, report = scheme.build_steps()
        self.assertEqual([step["type"] for step in steps], ["click"])
        self.assertTrue(any("没有可用步骤" in note for note in report.notes))

    def test_to_flow_validates(self):
        scheme = Scheme.from_dict(two_segment_scheme())
        flow, report = scheme.to_flow()
        self.assertEqual(flow.name, "测试轮椅")
        self.assertEqual(flow.step_count(), 3)
        self.assertIn("2 段", report.summary())

    def test_too_many_steps_raises(self):
        steps = [{"type": "wait", "seconds": 0} for _ in range(600)]
        scheme = Scheme.from_dict({"segments": [{"steps": steps}]})
        with self.assertRaises(SchemeFormatError):
            scheme.to_flow()

    def test_default_between_wait(self):
        self.assertAlmostEqual(DEFAULT_BETWEEN_WAIT, 0.5)


class TestSchemeStorage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "schemes"

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_load_list(self):
        scheme = Scheme.from_dict(two_segment_scheme())
        path = save_scheme(scheme, self.folder)
        self.assertTrue(path.exists())
        loaded = load_scheme("测试轮椅", self.folder)
        self.assertEqual(loaded.to_dict(), scheme.to_dict())
        self.assertEqual(list_schemes(self.folder), ["测试轮椅"])

    def test_load_missing(self):
        with self.assertRaises(SchemeFormatError):
            load_scheme("nope", self.folder)

    def test_unsafe_name(self):
        self.assertEqual(scheme_path("../evil", self.folder).parent, self.folder)

    def test_bad_json(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "bad.json").write_text("{ oops", encoding="utf-8")
        with self.assertRaises(SchemeFormatError):
            load_scheme("bad", self.folder)

    def test_default_folder(self):
        self.assertEqual(SCHEME_FOLDER.as_posix(), "configs/schemes")

    def test_export_to_flow_file(self):
        scheme = Scheme.from_dict(two_segment_scheme())
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("src.core.flow.model.FLOW_FOLDER", Path(tmp)):
                path = scheme_to_flow_file(scheme, flow_name="轮椅导出")
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["name"], "轮椅导出")
            self.assertEqual(len(data["steps"]), 3)


class FakeReplayTask:
    """回放用的假任务：记录点击/按键，自己推进时钟。"""

    width = 1920
    height = 1080

    def __init__(self):
        self.now = 0.0
        self.calls: list[tuple] = []

    def active_time(self):
        return self.now

    def sleep(self, seconds):
        self.calls.append(("sleep", round(float(seconds), 3)))
        self.now += float(seconds)

    def click_relative(self, x, y):
        self.calls.append(("click", round(float(x), 3), round(float(y), 3)))

    def press_key(self, key, down_time=0.02, **kwargs):
        self.calls.append(("key", key, round(float(down_time), 3)))

    def find_one(self, feature=None, **kwargs):
        return None


class TestSchemeExecution(unittest.TestCase):
    def test_scheme_runs_through_flow_executor(self):
        scheme = Scheme.from_dict(two_segment_scheme(loop=2, between_wait=0))
        flow, _report = scheme.to_flow()
        task = FakeReplayTask()
        executor = FlowExecutor(flow, FlowContext(task, default_timeout=1.0, poll_interval=0.1))
        result = executor.run()

        self.assertTrue(result.ok, result.summary())
        self.assertEqual(task.calls.count(("click", 0.1, 0.1)), 2)
        self.assertEqual(len([call for call in task.calls if call[0] == "key"]), 2)


class TestQueueSchemeIntegration(unittest.TestCase):
    """轮椅方案作为队列项执行（FlowTask.run_scheme）。"""

    def _task(self):
        from src.tasks.onetime.flow_task import FlowTask

        task = FlowTask.__new__(FlowTask)
        task.config = {}
        task.logs: list[str] = []
        task.log_info = lambda message, notify=False, **kwargs: task.logs.append(str(message))
        return task

    def test_scheme_is_resolved(self):
        from src.core.queue import QueueItem
        from src.tasks.onetime.flow_task import FlowTask

        task = self._task()
        task.run_scheme = lambda budget, name=None: True
        resolver = FlowTask._queue_resolver(task)
        self.assertTrue(callable(resolver(QueueItem(kind="scheme", ref="轮椅A"))))

    def test_missing_name(self):
        from src.tasks.onetime.flow_task import FlowTask

        task = self._task()
        self.assertFalse(FlowTask.run_scheme(task))
        self.assertTrue(any("没有填写方案文件" in log for log in task.logs))

    def test_load_failure(self):
        from src.tasks.onetime.flow_task import FlowTask

        task = self._task()
        with mock.patch("src.tasks.onetime.flow_task.load_scheme", side_effect=SchemeFormatError("坏了")):
            self.assertFalse(FlowTask.run_scheme(task, name="坏方案"))
        self.assertTrue(any("方案读取失败" in log for log in task.logs))

    def test_success_path(self):
        from src.tasks.onetime.flow_task import FlowTask

        task = self._task()
        executed = {}

        def fake_execute(flow_arg, budget, resume=False):
            executed["budget"] = budget
            executed["steps"] = flow_arg.step_count()

            class Result:
                ok = True

                def summary(self):
                    return "流程完成"

            return Result()

        task._execute_flow = fake_execute
        with mock.patch(
            "src.tasks.onetime.flow_task.load_scheme",
            return_value=Scheme.from_dict(two_segment_scheme()),
        ):
            self.assertTrue(FlowTask.run_scheme(task, budget_seconds=60, name="轮椅A"))
        self.assertEqual(executed["budget"], 60)
        self.assertGreater(executed["steps"], 0)

    def test_execute_returns_none(self):
        from src.tasks.onetime.flow_task import FlowTask

        task = self._task()
        task._execute_flow = lambda *args, **kwargs: None
        with mock.patch(
            "src.tasks.onetime.flow_task.load_scheme",
            return_value=Scheme.from_dict(two_segment_scheme()),
        ):
            self.assertFalse(FlowTask.run_scheme(task, name="轮椅A"))


if __name__ == "__main__":
    unittest.main()
