"""流程模型与执行器测试。

执行器只依赖 ``FlowContext`` 的少量原语，所以这里用一个假 Task（记录调用、
自己推进虚拟时钟）就能覆盖全部步骤类型、断点续跑与失败策略，不需要真机。
"""

import json
import tempfile
import unittest
from pathlib import Path

from src.core.flow import (
    MAX_DEPTH,
    MAX_LOOP_TIMES,
    MAX_STEPS,
    CheckpointStore,
    Flow,
    FlowContext,
    FlowExecutor,
    FlowFormatError,
    parse_flow,
    validate_flow,
)
from src.core.flow.executor import FlowError


class FakeBox:
    def __init__(self, x=960, y=540):
        self._x = x
        self._y = y

    def center(self):
        return self._x, self._y


class FakeTask:
    """记录调用并自己推进时钟的任务替身。"""

    width = 1920
    height = 1080

    def __init__(self, visible=(), on_click=None, on_find=None):
        self.now = 0.0
        self.visible = set(visible)
        self.calls: list[tuple] = []
        self._on_click = on_click
        self._on_find = on_find

    # 时钟
    def active_time(self):
        return self.now

    def sleep(self, seconds):
        self.calls.append(("sleep", round(float(seconds), 3)))
        self.now += float(seconds)

    # 输入
    def click_relative(self, x, y):
        self.calls.append(("click", round(float(x), 3), round(float(y), 3)))
        if self._on_click:
            self._on_click(self, x, y)

    def mouse_down(self, x=-1, y=-1, name=None, key="left"):
        self.calls.append(("mouse_down", int(x), int(y)))

    def mouse_up(self, name=None, key="left"):
        self.calls.append(("mouse_up",))

    def press_key(self, key, down_time=0.02, **kwargs):
        self.calls.append(("key", key, round(float(down_time), 3)))

    def move_keys(self, keys, duration=0.0):
        self.calls.append(("move", keys, round(float(duration), 3)))

    def active_and_send_mouse_delta(self, dx, dy, **kwargs):
        self.calls.append(("mouse_delta", dx, dy))

    # 识别
    def find_one(self, feature=None, **kwargs):
        name = getattr(feature, "value", feature)
        self.calls.append(("find", name))
        if self._on_find:
            self._on_find(self, name)
        return FakeBox() if name in self.visible else None

    # 动作白名单
    def back(self, **kwargs):
        self.calls.append(("action", "back"))

    def ensure_main(self, **kwargs):
        self.calls.append(("action", "ensure_main"))

    # 工具
    def kinds(self):
        return [call[0] for call in self.calls]

    def named(self, kind):
        return [call for call in self.calls if call[0] == kind]

def make_flow(steps, name="t"):
    return parse_flow({"name": name, "steps": steps})


def make_executor(flow, task=None, **kwargs):
    task = task or FakeTask()
    context = FlowContext(task, default_timeout=1.0, poll_interval=0.1)
    return FlowExecutor(flow, context, **kwargs), task


class TestFlowParsing(unittest.TestCase):
    def test_minimal_flow(self):
        flow = make_flow([{"type": "wait", "seconds": 0.5}])
        self.assertEqual(flow.name, "t")
        self.assertEqual(flow.step_count(), 1)
        self.assertEqual(flow.depth(), 1)
        self.assertEqual(validate_flow(flow), [])

    def test_click_hold_is_parsed_and_round_trips(self):
        flow = make_flow([{"type": "click", "x": 0.5, "y": 0.5, "hold": 1.5}])
        self.assertEqual(validate_flow(flow), [])
        self.assertAlmostEqual(flow.steps[0].hold, 1.5)
        import json

        again = make_flow(json.loads(flow.to_json())["steps"])
        self.assertAlmostEqual(again.steps[0].hold, 1.5)

    def test_click_hold_negative_is_rejected(self):
        flow = make_flow([{"type": "click", "x": 0.5, "y": 0.5, "hold": -1}])
        errors = [issue for issue in validate_flow(flow) if issue.level == "error"]
        self.assertTrue(any("hold" in str(issue) for issue in errors))

    def test_click_hold_unknown_field_still_flagged(self):
        flow = make_flow([{"type": "click", "x": 0.5, "y": 0.5, "durationn": 1}])
        errors = [issue for issue in validate_flow(flow) if issue.level == "error"]
        self.assertTrue(any("durationn" in str(issue) for issue in errors))

    def test_missing_steps_raises(self):
        with self.assertRaises(FlowFormatError):
            parse_flow({"name": "x"})

    def test_steps_must_be_list(self):
        with self.assertRaises(FlowFormatError):
            parse_flow({"steps": {"type": "wait"}})

    def test_top_level_must_be_object(self):
        with self.assertRaises(FlowFormatError):
            parse_flow([{"type": "wait"}])

    def test_unknown_type_raises(self):
        with self.assertRaises(FlowFormatError):
            make_flow([{"type": "teleport"}])

    def test_invalid_json_text(self):
        with self.assertRaises(FlowFormatError):
            Flow.from_json("{ not json")

    def test_unknown_field_is_error(self):
        flow = make_flow([{"type": "wait", "seconds": 1, "secs": 2}])
        issues = validate_flow(flow)
        self.assertTrue(any("不支持字段 secs" in issue.message for issue in issues))
        self.assertTrue(all(issue.level == "error" for issue in issues))

    def test_round_trip(self):
        source = {
            "name": "往返",
            "description": "说明",
            "steps": [
                {"type": "click", "x": 0.5, "y": 0.25, "comment": "点一下"},
                {"type": "wait_template", "template": "main_ui", "expect": "disappear", "settle": 0.5},
                {"type": "loop", "times": 2, "steps": [{"type": "key", "key": "f", "duration": 0.1}]},
                {"type": "if", "template": "confirm_button", "steps": [{"type": "action", "action": "back"}],
                 "else": [{"type": "wait", "seconds": 0}]},
            ],
        }
        flow = parse_flow(source)
        again = Flow.from_dict(json.loads(flow.to_json()))
        self.assertEqual(again.to_dict(), flow.to_dict())

    def test_flow_from_file_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.json"
            path.write_text(json.dumps({"name": "文件流程", "steps": [{"type": "wait", "seconds": 0}]}),
                            encoding="utf-8")
            flow = Flow.from_file(path)
        self.assertEqual(flow.name, "文件流程")
        self.assertEqual(flow.key, "文件流程")
        self.assertEqual(flow.source, "demo.json")


class TestFlowLimits(unittest.TestCase):
    def test_step_limit(self):
        steps = [{"type": "wait", "seconds": 0} for _ in range(MAX_STEPS + 1)]
        issues = validate_flow(make_flow(steps))
        self.assertTrue(any("超过上限" in issue.message for issue in issues))

    def test_step_limit_counts_nested(self):
        inner = [{"type": "wait", "seconds": 0} for _ in range(MAX_STEPS)]
        issues = validate_flow(make_flow([{"type": "loop", "times": 1, "steps": inner}]))
        self.assertTrue(any("超过上限" in issue.message for issue in issues))

    def test_depth_limit(self):
        step = {"type": "wait", "seconds": 0}
        for _ in range(MAX_DEPTH + 1):
            step = {"type": "loop", "times": 1, "steps": [step]}
        issues = validate_flow(make_flow([step]))
        self.assertTrue(any("嵌套深度" in issue.message for issue in issues))

    def test_loop_times_bounds(self):
        for times in (0, MAX_LOOP_TIMES + 1):
            with self.subTest(times=times):
                issues = validate_flow(
                    make_flow([{"type": "loop", "times": times, "steps": [{"type": "wait", "seconds": 0}]}])
                )
                self.assertTrue(any("循环次数" in issue.message for issue in issues))

    def test_loop_needs_exactly_one_mode(self):
        body = [{"type": "wait", "seconds": 0}]
        cases = [
            {"type": "loop", "steps": body},
            {"type": "loop", "times": 2, "until_template": "x", "steps": body},
        ]
        for case in cases:
            with self.subTest(case=case):
                issues = validate_flow(make_flow([case]))
                self.assertTrue(any("只能" in issue.message or "选且只选" in issue.message for issue in issues))

    def test_loop_body_required(self):
        issues = validate_flow(make_flow([{"type": "loop", "times": 2, "steps": []}]))
        self.assertTrue(any("循环体" in issue.message for issue in issues))

    def test_empty_flow_is_error(self):
        issues = validate_flow(make_flow([]))
        self.assertTrue(any("没有任何步骤" in issue.message for issue in issues))


class TestStepValidation(unittest.TestCase):
    def _issues(self, step):
        return [issue.message for issue in validate_flow(make_flow([step]))]

    def test_click_requires_target(self):
        self.assertTrue(any("需要 x/y 或 template" in msg for msg in self._issues({"type": "click"})))

    def test_click_rejects_both_targets(self):
        msgs = self._issues({"type": "click", "x": 0.5, "y": 0.5, "template": "ui"})
        self.assertTrue(any("二选一" in msg for msg in msgs))

    def test_click_ratio_range(self):
        self.assertTrue(any("0..1" in msg for msg in self._issues({"type": "click", "x": 1.5, "y": 0.5})))

    def test_move_requires_mode(self):
        self.assertTrue(any("需要 direction 或 dx/dy" in msg for msg in self._issues({"type": "move"})))

    def test_move_rejects_both_modes(self):
        msgs = self._issues({"type": "move", "direction": "w", "dx": 1})
        self.assertTrue(any("二选一" in msg for msg in msgs))

    def test_move_direction_must_be_known(self):
        self.assertTrue(any("不支持的方向" in msg for msg in self._issues({"type": "move", "direction": "q"})))

    def test_key_and_action_required(self):
        self.assertTrue(any("key 不能为空" in msg for msg in self._issues({"type": "key"})))
        self.assertTrue(any("action 不能为空" in msg for msg in self._issues({"type": "action"})))

    def test_wait_rejects_negative(self):
        self.assertTrue(any("负数" in msg for msg in self._issues({"type": "wait", "seconds": -1})))

    def test_wait_template_requires_template_and_valid_expect(self):
        self.assertTrue(any("需要 template" in msg for msg in self._issues({"type": "wait_template"})))
        msgs = self._issues({"type": "wait_template", "template": "ui", "expect": "blink"})
        self.assertTrue(any("expect" in msg for msg in msgs))

    def test_if_requires_branches(self):
        msgs = self._issues({"type": "if", "template": "ui", "steps": [], "else": []})
        self.assertTrue(any("没有意义" in msg for msg in msgs))


class TestExecutorBasics(unittest.TestCase):
    def test_click_hold_uses_mouse_down_up(self):
        executor, task = make_executor(
            make_flow([{"type": "click", "x": 0.5, "y": 0.5, "hold": 1.2}])
        )
        result = executor.run()
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(task.named("mouse_down"), [("mouse_down", 960, 540)])
        self.assertEqual(task.named("mouse_up"), [("mouse_up",)])
        self.assertIn(("sleep", 1.2), task.calls)
        self.assertEqual(task.named("click"), [], "有 hold 时不该再普通点击")

    def test_click_hold_falls_back_without_mouse_api(self):
        class NoMouseTask(FakeTask):
            width = 1920
            height = 1080

            # 删掉 mouse_down / mouse_up
            mouse_down = None
            mouse_up = None

        task = NoMouseTask()
        executor, _ = make_executor(
            make_flow([{"type": "click", "x": 0.4, "y": 0.6, "hold": 0.8}]), task
        )
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("click"), [("click", 0.4, 0.6)])
        self.assertIn(("sleep", 0.8), task.calls)

    def test_click_point(self):
        executor, task = make_executor(make_flow([{"type": "click", "x": 0.25, "y": 0.75}]))
        result = executor.run()
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(task.named("click"), [("click", 0.25, 0.75)])
        self.assertEqual(result.completed, 1)

    def test_click_template_uses_box_center(self):
        task = FakeTask(visible={"confirm_button"})
        executor, _ = make_executor(
            make_flow([{"type": "click", "template": "confirm_button"}]), task
        )
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("click"), [("click", 0.5, 0.5)])

    def test_click_template_missing_fails(self):
        executor, _ = make_executor(make_flow([{"type": "click", "template": "ghost"}]))
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertEqual(result.failed, ["steps[0]"])
        self.assertIn("ghost", result.error)

    def test_key_with_duration_and_times(self):
        executor, task = make_executor(
            make_flow([{"type": "key", "key": "f", "duration": 0.2, "times": 3}])
        )
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("key"), [("key", "f", 0.2)] * 3)

    def test_move_direction(self):
        executor, task = make_executor(make_flow([{"type": "move", "direction": "WD", "duration": 0.8}]))
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("move"), [("move", "wd", 0.8)])

    def test_move_delta(self):
        executor, task = make_executor(make_flow([{"type": "move", "dx": 10, "dy": -5}]))
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("mouse_delta"), [("mouse_delta", 10, -5)])

    def test_wait(self):
        executor, task = make_executor(make_flow([{"type": "wait", "seconds": 1.5}]))
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("sleep"), [("sleep", 1.5)])

    def test_wait_template_appear(self):
        task = FakeTask(visible={"main_ui"})
        executor, _ = make_executor(make_flow([{"type": "wait_template", "template": "main_ui"}]), task)
        self.assertTrue(executor.run().ok)

    def test_wait_template_appear_timeout(self):
        executor, _ = make_executor(
            make_flow([{"type": "wait_template", "template": "ghost", "timeout": 0.3}])
        )
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertIn("出现超时", result.error)

    def test_wait_template_disappear(self):
        task = FakeTask(visible=set())
        executor, _ = make_executor(
            make_flow([{"type": "wait_template", "template": "loading", "expect": "disappear"}]), task
        )
        self.assertTrue(executor.run().ok)

    def test_wait_template_disappear_timeout(self):
        task = FakeTask(visible={"loading"})
        executor, _ = make_executor(
            make_flow([{"type": "wait_template", "template": "loading", "expect": "disappear", "timeout": 0.3}]),
            task,
        )
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertIn("消失超时", result.error)

    def test_action_allowlisted(self):
        executor, task = make_executor(make_flow([{"type": "action", "action": "back"}]))
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("action"), [("action", "back")])

    def test_action_outside_allowlist_fails(self):
        executor, _ = make_executor(make_flow([{"type": "action", "action": "os_system"}]))
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertIn("白名单", result.error)


class TestControlFlow(unittest.TestCase):
    def test_loop_times(self):
        executor, task = make_executor(
            make_flow([{"type": "loop", "times": 3, "steps": [{"type": "key", "key": "e"}]}])
        )
        self.assertTrue(executor.run().ok)
        self.assertEqual(len(task.named("key")), 3)

    def test_loop_until_template(self):
        """点两次之后 target 出现 → 循环退出。"""

        def on_click(task, x, y):
            task.visible.add("target")

        task = FakeTask(on_click=on_click)
        executor, _ = make_executor(
            make_flow(
                [
                    {
                        "type": "loop",
                        "until_template": "target",
                        "steps": [{"type": "click", "x": 0.5, "y": 0.5}],
                    }
                ]
            ),
            task,
        )
        self.assertTrue(executor.run().ok)
        self.assertEqual(len(task.named("click")), 1)

    def test_loop_until_not_template(self):
        def on_find(task, name):
            if name == "loading" and len(task.named("find")) > 2:
                task.visible.discard("loading")

        task = FakeTask(visible={"loading"}, on_find=on_find)
        executor, _ = make_executor(
            make_flow(
                [
                    {
                        "type": "loop",
                        "until_not_template": "loading",
                        "max": 5,
                        "steps": [{"type": "wait", "seconds": 0.1}],
                    }
                ]
            ),
            task,
        )
        result = executor.run()
        self.assertTrue(result.ok, result.summary())

    def test_loop_cap_reached(self):
        task = FakeTask(visible={"ghost"})
        executor, _ = make_executor(
            make_flow(
                [
                    {
                        "type": "loop",
                        "until_not_template": "ghost",
                        "max": 3,
                        "steps": [{"type": "wait", "seconds": 0}],
                    }
                ]
            ),
            task,
        )
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertIn("上限", result.error)

    def test_if_true_branch(self):
        task = FakeTask(visible={"confirm_button"})
        executor, _ = make_executor(
            make_flow(
                [
                    {
                        "type": "if",
                        "template": "confirm_button",
                        "steps": [{"type": "click", "x": 0.1, "y": 0.1}],
                        "else": [{"type": "click", "x": 0.9, "y": 0.9}],
                    }
                ]
            ),
            task,
        )
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("click"), [("click", 0.1, 0.1)])

    def test_if_false_branch(self):
        task = FakeTask(visible=set())
        executor, _ = make_executor(
            make_flow(
                [
                    {
                        "type": "if",
                        "template": "confirm_button",
                        "steps": [{"type": "click", "x": 0.1, "y": 0.1}],
                        "else": [{"type": "click", "x": 0.9, "y": 0.9}],
                    }
                ]
            ),
            task,
        )
        self.assertTrue(executor.run().ok)
        self.assertEqual(task.named("click"), [("click", 0.9, 0.9)])


class TestFailurePolicy(unittest.TestCase):
    def test_stops_on_first_failure(self):
        executor, task = make_executor(
            make_flow(
                [
                    {"type": "click", "x": 0.1, "y": 0.1},
                    {"type": "click", "template": "ghost", "timeout": 0.1},
                    {"type": "click", "x": 0.9, "y": 0.9},
                ]
            )
        )
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertEqual(result.completed, 1)
        self.assertEqual(result.failed, ["steps[1]"])
        self.assertEqual(task.named("click"), [("click", 0.1, 0.1)])

    def test_continue_on_error(self):
        executor, task = make_executor(
            make_flow(
                [
                    {"type": "click", "template": "ghost", "timeout": 0.1},
                    {"type": "click", "x": 0.9, "y": 0.9},
                ]
            ),
            continue_on_error=True,
        )
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertEqual(result.completed, 2)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(task.named("click"), [("click", 0.9, 0.9)])

    def test_invalid_flow_raises_before_running(self):
        flow = make_flow([{"type": "click"}])
        executor, task = make_executor(flow)
        with self.assertRaises(FlowError):
            executor.run()
        self.assertEqual(task.calls, [])

    def test_budget_exceeded(self):
        executor, _ = make_executor(
            make_flow([{"type": "wait", "seconds": 5}, {"type": "wait", "seconds": 5}]),
            budget_seconds=1.0,
        )
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertIn("预算", result.error)
        self.assertTrue(result.budget_exceeded)
        self.assertIn("超出时间预算", result.summary())

    def test_budget_aborts_even_when_continue_on_error(self):
        """预算不是「某一步失败」：continue_on_error 也不该继续刷剩下的步骤。"""
        executor, task = make_executor(
            make_flow(
                [
                    {"type": "wait", "seconds": 5},
                    {"type": "click", "x": 0.1, "y": 0.1},
                    {"type": "click", "x": 0.2, "y": 0.2},
                    {"type": "click", "x": 0.3, "y": 0.3},
                ]
            ),
            budget_seconds=1.0,
            continue_on_error=True,
        )
        result = executor.run()
        self.assertFalse(result.ok)
        self.assertTrue(result.budget_exceeded)
        self.assertEqual(result.failed, [], "预算不该被记成步骤失败")
        # 第一步的 wait 自己就被预算打断了，所以它不算完成
        self.assertEqual(result.completed, 0)
        self.assertEqual(task.named("click"), [], "预算用完后不该再执行任何步骤")

    def test_budget_stops_at_successful_step_for_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(Path(tmp))
            flow = make_flow(
                [{"type": "wait", "seconds": 1}, {"type": "wait", "seconds": 5}, {"type": "wait", "seconds": 1}]
            )
            executor, _ = make_executor(
                flow, budget_seconds=2.0, checkpoint=store.for_flow(flow)
            )
            result = executor.run()
            self.assertTrue(result.budget_exceeded)
            self.assertEqual(result.completed, 1)
            # 断点停在最后一个成功步骤，续跑会重跑被打断的那一步
            self.assertEqual(store.for_flow(flow).load(), 1)

    def test_elapsed_is_reported(self):
        executor, _ = make_executor(make_flow([{"type": "wait", "seconds": 2}]))
        result = executor.run()
        self.assertAlmostEqual(result.elapsed, 2.0, places=3)
        self.assertIn("流程完成", result.summary())


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = CheckpointStore(Path(self._tmp.name))
        self.flow = make_flow(
            [
                {"type": "click", "x": 0.1, "y": 0.1},
                {"type": "click", "template": "ghost", "timeout": 0.1},
                {"type": "click", "x": 0.9, "y": 0.9},
            ],
            name="断点流程",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_progress_saved_after_each_top_level_step(self):
        checkpoint = self.store.for_flow(self.flow)
        executor, _ = make_executor(self.flow, checkpoint=checkpoint)
        executor.run()
        self.assertEqual(checkpoint.load(), 1)

    def test_resume_skips_completed_steps(self):
        checkpoint = self.store.for_flow(self.flow)
        first, _ = make_executor(self.flow, checkpoint=checkpoint)
        first.run()

        # 断点之后「问题已修好」：ghost 现在能被识别到
        task = FakeTask(visible={"ghost"})
        second, _ = make_executor(self.flow, task=task, checkpoint=checkpoint)
        result = second.run(resume=True)

        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.resumed_from, 1)
        # 只重跑第 2、3 步：第 1 步的固定坐标点击不该再出现
        self.assertEqual(task.named("click"), [("click", 0.5, 0.5), ("click", 0.9, 0.9)])

    def test_success_clears_checkpoint(self):
        checkpoint = self.store.for_flow(self.flow)
        flow = make_flow([{"type": "wait", "seconds": 0}], name="简单流程")
        executor, _ = make_executor(flow, checkpoint=checkpoint)
        self.assertTrue(executor.run().ok)
        self.assertEqual(checkpoint.load(), 0)

    def test_edited_flow_ignores_stale_checkpoint(self):
        """改了流程结构，旧断点就不该再跳过步骤（否则会跳过刚插入的新步骤）。"""
        checkpoint = self.store.for_flow(self.flow)
        executor, _ = make_executor(self.flow, checkpoint=checkpoint)
        executor.run()
        self.assertEqual(checkpoint.load(), 1)

        edited = make_flow(
            [
                {"type": "wait", "seconds": 0.1},  # 新插入的第 1 步
                {"type": "click", "x": 0.1, "y": 0.1},
                {"type": "click", "template": "ghost", "timeout": 0.1},
            ],
            name="断点流程",
        )
        self.assertEqual(self.store.for_flow(edited).load(), 0, "结构变了应忽略旧断点")

    def test_old_checkpoint_without_fingerprint_still_works(self):
        key = self.flow.key
        self.store.save_record(key, {"flow": key, "completed": 2, "total": 3})
        self.assertEqual(self.store.for_flow(self.flow).load(), 2)

    def test_description_change_keeps_checkpoint(self):
        checkpoint = self.store.for_flow(self.flow)
        executor, _ = make_executor(self.flow, checkpoint=checkpoint)
        executor.run()
        same_steps = parse_flow(
            {"name": "断点流程", "description": "改了说明", "steps": self.flow.to_dict()["steps"]}
        )
        self.assertEqual(self.store.for_flow(same_steps).load(), 1)

    def test_store_tolerates_corrupt_file(self):
        checkpoint = self.store.for_flow(self.flow)
        path = self.store._path(self.flow.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(checkpoint.load(), 0)

    def test_store_roundtrip_record(self):
        checkpoint = self.store.for_flow(self.flow)
        self.assertTrue(checkpoint.save(2, 3))
        record = self.store.load_record(self.flow.key)
        self.assertEqual(record["completed"], 2)
        self.assertEqual(record["total"], 3)
        checkpoint.clear()
        self.assertIsNone(self.store.load_record(self.flow.key))

    def test_unsafe_flow_name_is_sanitized(self):
        flow = make_flow([{"type": "wait", "seconds": 0}], name="../../evil name")
        checkpoint = self.store.for_flow(flow)
        path = self.store._path(flow.key)
        self.assertEqual(path.parent, Path(self._tmp.name))
        self.assertTrue(checkpoint.save(1, 1))


if __name__ == "__main__":
    unittest.main()
