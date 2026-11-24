"""卡死检测与失败自恢复测试。

任务实例用 ``__new__`` 造出来（跳过框架初始化），只保留被测方法需要的属性，
时间基准由测试自己推进，因此不依赖真实等待。
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.core.base_game_task import BaseGameTask
from src.core.failure_frame_store import FailureFrameStore
from src.core.base_mixin.agent_mixin import AgentMixin
from src.core.runtime_config import STUCK_CHECK_INTERVAL, RuntimeSettings


def _frame(pattern: str = "solid") -> np.ndarray:
    frame = np.zeros((108, 192, 3), dtype=np.uint8)
    if pattern == "checker":
        frame[::4, ::4] = 255
    elif pattern == "gradient":
        frame[:, :] = np.arange(192, dtype=np.uint8)[None, :, None]
    return frame


class StubTask(BaseGameTask):
    """只提供被测逻辑需要的最小表面。"""

    _frame_value = None

    @property
    def runtime(self) -> RuntimeSettings:
        return self._runtime_settings

    @property
    def frame(self):
        """框架里 frame 是只读属性，测试里需要可写。"""
        return self._frame_value

    @frame.setter
    def frame(self, value):
        self._frame_value = value


def _make_task(settings: RuntimeSettings | None = None) -> StubTask:
    task = StubTask.__new__(StubTask)
    task._runtime_settings = settings or RuntimeSettings()
    task.frame = None
    task.now = 0.0
    task.logs: list[str] = []
    task.sleeps: list[float] = []
    task.back_calls: list[int] = []
    task.menu = False
    task.once_sleep_time = 0.5

    task._stuck_fingerprint = None
    task._stuck_since = 0.0
    task._stuck_warned = False
    task._last_stuck_check = -STUCK_CHECK_INTERVAL  # 让第一次检测就能通过节流门槛
    task._failure_store = None

    task.active_time = lambda: task.now
    task.log_info = lambda message, notify=False, **kwargs: task.logs.append(str(message))
    task.log_warning = lambda message, notify=False, **kwargs: task.logs.append(f"WARN {message}")
    task.sleep = lambda timeout: task.sleeps.append(timeout)
    task.back = lambda *args, **kwargs: task.back_calls.append(1)
    task.is_main_menu = lambda: task.menu
    return task


class TestStuckDetection(unittest.TestCase):
    def _advance(self, task, seconds, steps: int = 1):
        """推进时间并触发若干次检测（每次跨过最小检测间隔）。"""
        for _ in range(steps):
            task.now += seconds
            task._check_stuck()

    def test_unchanged_frame_warns_after_threshold(self):
        task = _make_task(RuntimeSettings(stuck_threshold=10.0))
        task.frame = _frame("solid")

        self._advance(task, STUCK_CHECK_INTERVAL)  # 建立基线
        self.assertEqual([log for log in task.logs if log.startswith("WARN")], [])

        self._advance(task, 5.0)
        self.assertEqual([log for log in task.logs if log.startswith("WARN")], [])

        self._advance(task, 10.0)
        warnings = [log for log in task.logs if log.startswith("WARN")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("没有变化", warnings[0])

    def test_warning_is_emitted_once_per_streak(self):
        task = _make_task(RuntimeSettings(stuck_threshold=5.0))
        task.frame = _frame("solid")
        self._advance(task, 1.0, steps=6)
        self._advance(task, 5.0, steps=6)
        warnings = [log for log in task.logs if log.startswith("WARN")]
        self.assertEqual(len(warnings), 1)

    def test_changed_frame_resets_baseline(self):
        task = _make_task(RuntimeSettings(stuck_threshold=5.0))
        patterns = ["solid", "checker", "gradient"]
        for index in range(9):
            task.frame = _frame(patterns[index % len(patterns)])
            task.now += 1.0
            task._check_stuck()
        self.assertEqual([log for log in task.logs if log.startswith("WARN")], [])

    def test_new_streak_warns_again(self):
        task = _make_task(RuntimeSettings(stuck_threshold=5.0))
        task.frame = _frame("solid")
        self._advance(task, 1.0, steps=6)
        self._advance(task, 5.0, steps=6)
        task.frame = _frame("gradient")
        self._advance(task, 1.0, steps=6)
        task.now += 6.0
        task._check_stuck()
        warnings = [log for log in task.logs if log.startswith("WARN")]
        self.assertEqual(len(warnings), 2)

    def test_low_spec_doubles_threshold(self):
        task = _make_task(RuntimeSettings(stuck_threshold=10.0, low_spec=True))
        task.frame = _frame("solid")
        self._advance(task, 1.0, steps=6)
        task.now += 9.0  # 未变时长 14s，小于 15s（10 x 1.5）
        task._check_stuck()
        self.assertEqual([log for log in task.logs if log.startswith("WARN")], [])

    def test_no_frame_means_no_check(self):
        task = _make_task(RuntimeSettings(stuck_threshold=1.0))
        task.frame = None
        self._advance(task, 10.0, steps=6)
        self.assertEqual(task.logs, [])

    def test_min_check_interval_is_respected(self):
        task = _make_task(RuntimeSettings(stuck_threshold=1.0))
        task.frame = _frame("solid")
        task._check_stuck()
        first = task._last_stuck_check
        task.now += STUCK_CHECK_INTERVAL / 2
        task._check_stuck()
        self.assertEqual(task._last_stuck_check, first)

    def test_reset_clears_baseline(self):
        task = _make_task(RuntimeSettings(stuck_threshold=5.0))
        task.frame = _frame("solid")
        self._advance(task, 1.0, steps=6)
        self._advance(task, 5.0, steps=6)
        task.reset_stuck_detection()
        self.assertIsNone(task._stuck_fingerprint)
        self.assertFalse(task._stuck_warned)

    def test_sleep_check_delegates_to_stuck_detection(self):
        task = _make_task(RuntimeSettings(stuck_threshold=1.0))
        task.frame = _frame("solid")
        task.sleep_check()
        self.assertIsNotNone(task._stuck_fingerprint)


class TestRecoverToMenu(unittest.TestCase):
    def test_already_in_menu_does_not_press_back(self):
        task = _make_task()
        task.menu = True
        self.assertTrue(task.recover_to_menu())
        self.assertEqual(task.back_calls, [])

    def test_presses_back_until_menu(self):
        task = _make_task()
        presses = {"left": 2}
        task.is_main_menu = lambda: presses["left"] <= 0
        task.back = lambda *a, **k: (task.back_calls.append(1), presses.__setitem__("left", presses["left"] - 1))

        self.assertTrue(task.recover_to_menu())
        self.assertEqual(len(task.back_calls), 2)

        task.back_calls.clear()
        self.assertTrue(task.recover_to_menu())
        self.assertEqual(task.back_calls, [])

    def test_gives_up_after_max_attempts(self):
        task = _make_task(RuntimeSettings(max_recover_attempts=3))
        self.assertFalse(task.recover_to_menu())
        self.assertEqual(len(task.back_calls), 3)

    def test_explicit_attempts_override_config(self):
        task = _make_task(RuntimeSettings(max_recover_attempts=5))
        self.assertFalse(task.recover_to_menu(attempts=1))
        self.assertEqual(len(task.back_calls), 1)

    def test_zero_attempts_only_checks_menu(self):
        task = _make_task(RuntimeSettings(max_recover_attempts=3))
        self.assertFalse(task.recover_to_menu(attempts=0))
        self.assertEqual(task.back_calls, [])

    def test_uses_once_sleep_time_as_default_wait(self):
        task = _make_task()
        task.recover_to_menu(attempts=2)
        self.assertEqual(task.sleeps, [task.once_sleep_time, task.once_sleep_time])

    def test_back_exception_does_not_propagate(self):
        task = _make_task()

        def boom(*args, **kwargs):
            raise RuntimeError("input broken")

        task.back = boom
        # 按返回键失败不能把异常抛到失败处理链上
        self.assertFalse(task.recover_to_menu())

    def test_recover_after_failure_respects_switch(self):
        task = _make_task(RuntimeSettings(auto_recover=False))
        self.assertFalse(task.recover_after_failure("boom"))
        self.assertEqual(task.back_calls, [])
        self.assertEqual(task.logs, [])

    def test_recover_after_failure_logs_outcome(self):
        task = _make_task(RuntimeSettings(auto_recover=True, max_recover_attempts=1))
        task.menu = True
        self.assertTrue(task.recover_after_failure("超时"))
        self.assertTrue(any("退回主菜单" in log for log in task.logs))

    def test_failed_recovery_logs_warning(self):
        task = _make_task(RuntimeSettings(auto_recover=True, max_recover_attempts=1))
        task.recover_after_failure("x")
        self.assertTrue(any(log.startswith("WARN") for log in task.logs))


class TestFailureFrameCapture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "failure_frames"

    def tearDown(self):
        self._tmp.cleanup()

    def _task_with_store(self, limit: int = 5) -> StubTask:
        task = _make_task(RuntimeSettings(failure_frame_limit=limit))
        task._failure_store = FailureFrameStore(root=self.root, limit=limit)
        return task

    def test_capture_saves_current_frame(self):
        task = self._task_with_store()
        task.frame = _frame("solid")

        self.assertTrue(task.capture_failure_frame("没找到按钮", task_name="DailyTask"))

        records = task._failure_store.list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].task, "DailyTask")
        self.assertEqual(records[0].message, "没找到按钮")

    def test_capture_without_frame_is_noop(self):
        task = self._task_with_store()
        task.frame = None
        self.assertFalse(task.capture_failure_frame("x"))
        self.assertEqual(task._failure_store.list(), [])

    def test_store_rebuilt_when_limit_changes(self):
        task = self._task_with_store(limit=5)
        old = task._failure_store
        task._runtime_settings = RuntimeSettings(failure_frame_limit=9)
        new = task.failure_store()
        self.assertIsNot(new, old)
        self.assertEqual(new.limit, 9)
        self.assertEqual(new.root, old.root)  # 目录必须沿用，不能悄悄换个地方写

    def test_store_reused_when_limit_same(self):
        task = self._task_with_store(limit=5)
        self.assertIs(task.failure_store(), task._failure_store)


class TestMarkFailureAndException(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "failure_frames"

    def tearDown(self):
        self._tmp.cleanup()

    def _task(self, settings: RuntimeSettings | None = None) -> StubTask:
        task = _make_task(settings or RuntimeSettings(failure_frame_limit=5))
        task._failure_store = FailureFrameStore(root=self.root, limit=5)
        task.frame = _frame("solid")
        task.screenshot_calls: list[str] = []
        task.screenshot = lambda name=None, **kwargs: task.screenshot_calls.append(str(name))
        task.config = {"发生异常时终止游戏": False}
        return task

    def test_mark_task_failure_saves_frame_and_logs(self):
        task = self._task()
        task.mark_task_failure("出错了", task_name="CommissionTask")

        self.assertEqual(task.screenshot_calls, ["fail_CommissionTask"])
        self.assertTrue(any("出错了" in log for log in task.logs))
        records = task._failure_store.list()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].task.startswith("CommissionTask"))

    def test_handle_task_exception_captures_and_recovers(self):
        task = self._task(RuntimeSettings(auto_recover=True, max_recover_attempts=2))
        task.menu = True

        with self.assertRaises(ValueError):
            task.handle_task_exception(ValueError("boom"), "some_prefix")

        self.assertEqual(task.screenshot_calls, ["some_prefix"])
        records = task._failure_store.list()
        self.assertEqual(len(records), 1)
        self.assertIn("boom", records[0].message)
        self.assertTrue(any("退回主菜单" in log for log in task.logs))

    def test_handle_task_exception_without_auto_recover_skips_back(self):
        task = self._task(RuntimeSettings(auto_recover=False))

        with self.assertRaises(ValueError):
            task.handle_task_exception(ValueError("boom"), "p")

        self.assertEqual(task.back_calls, [])
        self.assertEqual(len(task._failure_store.list()), 1)

    def test_unique_ids_for_frames_in_same_second(self):
        task = self._task()
        for _ in range(3):
            task.capture_failure_frame("x")
        ids = {record.id for record in task._failure_store.list()}
        self.assertEqual(len(ids), 3)
        self.assertTrue(all(isinstance(uid, str) for uid in ids))


if __name__ == "__main__":
    unittest.main()
