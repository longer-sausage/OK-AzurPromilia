import unittest
from unittest.mock import MagicMock

from ok import WaitFailedException

from src.core.base_game_task import BaseGameTask
from src.core.base_mixin.runtime_mixin import RuntimeMixin


class DummyLoopTask:
    """用于测试 BaseGameTask.loop 生成器的轻量任务 stub。"""

    loop = BaseGameTask.loop

    def __init__(self):
        self.now = 0.0
        self.frames = []
        self.frame_index = 0

    def next_frame(self):
        if not self.frames:
            return None
        frame = self.frames[self.frame_index % len(self.frames)]
        self.frame_index += 1
        return frame

    def active_time(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


class TestLoop(unittest.TestCase):
    """测试 self.loop 的超时、帧产出与异常控制。"""

    def setUp(self):
        self.task = DummyLoopTask()
        self.task.frames = ["frame_0", "frame_1", "frame_2"]

    def test_loop_yields_frames(self):
        yielded = []
        for frame in self.task.loop(time_out=5.0):
            yielded.append(frame)
            self.task.advance(2.0)
            if len(yielded) == 3:
                break
        self.assertEqual(yielded, ["frame_0", "frame_1", "frame_2"])

    def test_loop_yield_frame_false(self):
        yielded = []
        for item in self.task.loop(time_out=5.0, yield_frame=False):
            yielded.append(item)
            self.task.advance(2.0)
            if len(yielded) == 2:
                break
        self.assertEqual(yielded, [None, None])

    def test_loop_early_break(self):
        count = 0
        for frame in self.task.loop(time_out=10.0):
            count += 1
            if count == 2:
                break
        self.assertEqual(count, 2)

    def test_loop_timeout_default_raises_timeout_error(self):
        iterations = 0
        with self.assertRaises(TimeoutError) as ctx:
            for _ in self.task.loop(time_out=3.0):
                iterations += 1
                self.task.advance(1.0)
        self.assertEqual(iterations, 3)
        self.assertIn("Loop time out.", str(ctx.exception))

    def test_loop_raise_if_time_out_false(self):
        iterations = 0
        for _ in self.task.loop(time_out=3.0, raise_if_time_out=False):
            iterations += 1
            self.task.advance(1.0)
        self.assertEqual(iterations, 3)

    def test_loop_raise_custom_exception_instance(self):
        with self.assertRaises(WaitFailedException) as ctx:
            for _ in self.task.loop(
                time_out=2.0,
                raise_if_time_out=WaitFailedException("Custom timeout"),
            ):
                self.task.advance(1.0)
        self.assertEqual(str(ctx.exception), "Custom timeout")

    def test_loop_raise_custom_exception_class(self):
        with self.assertRaises(RuntimeError) as ctx:
            for _ in self.task.loop(time_out=2.0, raise_if_time_out=RuntimeError):
                self.task.advance(1.0)
        self.assertIn("Loop time out.", str(ctx.exception))

    def test_loop_pause_awareness(self):
        """模拟任务暂停期间 active_time 不增长，循环总帧数增加。"""
        iterations = 0
        for _ in self.task.loop(time_out=2.0, raise_if_time_out=False):
            iterations += 1
            # 前两次迭代期间任务处于暂停状态，active_time 不增加
            if iterations > 2:
                self.task.advance(1.0)
        # 0s 跑两次（暂停），随后 1s 两次（达到 2s 超时），共 4 次
        self.assertEqual(iterations, 4)


if __name__ == "__main__":
    unittest.main()
