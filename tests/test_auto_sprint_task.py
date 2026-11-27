"""自动冲刺触发式任务测试。

全部 Win32 按键状态读取、前台窗口判定、交互输入与时钟均使用 stub / mock，
不连接游戏窗口，不发送真实按键。
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ok import og
from ok.ui.qt.tasks import config_form
from ok.ui.qt.tasks.config_editors import NumberEditor
from PySide6.QtWidgets import QApplication

from src.gui.account_config_tab import InMemoryConfig
from src.icons import Icons
from src.patches import no_frame_task_patch
from src.tasks.trigger import auto_sprint_task
from src.tasks.trigger.auto_sprint_task import (
    DEFAULT_SPRINT_INTERVAL_MS,
    INTERVAL_CONFIG_KEY,
    KEY_DOWN_TIME,
    MIN_SPRINT_INTERVAL_MS,
    SPRINT_KEY,
    START_DELAY_SECONDS,
    WASD_VK_CODES,
    AutoSprintTask,
)


class FakeInteraction:
    """记录按键按下/抬起序列的假交互层。"""

    def __init__(self, down_result=True):
        self.down_result = down_result
        self.events: list[tuple] = []
        self.on_down = None

    def send_key_down(self, key, activate=True):
        self.events.append(("down", key, activate))
        if callable(self.on_down):
            self.on_down(key)
        return self.down_result

    def send_key_up(self, key):
        self.events.append(("up", key))

    def on_run(self):
        pass


class SprintHarness:
    """将 AutoSprintTask 从框架运行期剥离：假时钟 + 假前景窗口 + 假键盘状态 + 假交互。"""

    def __init__(
        self,
        *,
        pressed_vks: set[int] | None = None,
        foreground: bool = True,
        enabled: bool = True,
        paused: bool = False,
        executor_paused: bool = False,
        down_result: bool = True,
        **config_overrides,
    ):
        self.now = 0.0
        self.pressed_vks: set[int] = set(pressed_vks or ())
        self.foreground = foreground
        self.wait_calls: list[float] = []
        self.interaction = FakeInteraction(down_result=down_result)

        self.hwnd_window = SimpleNamespace(
            hwnd=12345,
            is_foreground=lambda: self.foreground,
        )
        self.executor = SimpleNamespace(
            debug=False,
            scene=None,
            paused=executor_paused,
            pause_start=None,
            exit_event=threading.Event(),
            interaction=self.interaction,
            device_manager=SimpleNamespace(
                hwnd_window=self.hwnd_window,
                ensure_capture=lambda *a, **kw: None,
            ),
            enqueue_onetime_task=lambda task: False,
            remove_onetime_task=lambda task: False,
            _wake_executor=lambda: None,
        )
        self.app = SimpleNamespace(tr=str)

        task = AutoSprintTask(self.executor, self.app)
        task.config = dict(task.default_config)
        task.config.update(config_overrides)
        task._enabled = enabled
        task._paused = paused
        # 用例用假时钟驱动 run()；独立线程读真实时钟并真实等待，默认关闭，
        # 线程行为由专门的用例显式开启验证。
        task.config[auto_sprint_task.USE_THREAD_CONFIG_KEY] = False
        task.active_time = lambda: self.now

        # 避免单元测试真的 sleep，记录请求的按下时长并保留停止事件语义
        real_event = task._stop_event

        class _FastStopEvent:
            def is_set(self):
                return real_event.is_set()

            def set(self):
                real_event.set()

            def clear(self):
                real_event.clear()

            def wait(self_evt, timeout=None):
                if timeout is not None:
                    self.wait_calls.append(timeout)
                return real_event.is_set()

        task._stop_event = _FastStopEvent()
        self.task = task

    def key_state(self, vk: int) -> int:
        return -32768 if vk in self.pressed_vks else 0

    def advance(self, seconds: float):
        self.now += seconds

    def run(self, times: int = 1, gap: float = 0.0):
        with patch.object(auto_sprint_task.win32api, "GetAsyncKeyState", side_effect=self.key_state):
            for _ in range(times):
                self.task.run()
                if gap:
                    self.advance(gap)

    def hold_until_sprint(self):
        """模拟首次按下 WASD 并持续按住满 START_DELAY_SECONDS 后再触发一次 run()。"""
        self.run()
        self.advance(START_DELAY_SECONDS)
        self.run()

    @property
    def shift_press_count(self) -> int:
        return sum(1 for event in self.interaction.events if event[0] == "down" and event[1] == SPRINT_KEY)


class TestAutoSprintTask(unittest.TestCase):
    # ── 1. 基本属性与默认配置 ───────────────────────────────

    def test_metadata_and_default_config(self):
        harness = SprintHarness()
        task = harness.task
        self.assertEqual(task.name, "自动冲刺")
        self.assertEqual(task.icon, Icons.Trigger)
        self.assertIs(task.needs_frame, False)
        self.assertEqual(task.trigger_interval, 0)
        self.assertEqual(task.default_config[INTERVAL_CONFIG_KEY], DEFAULT_SPRINT_INTERVAL_MS)
        self.assertEqual(task.default_config["_enabled"], False)
        self.assertIn(INTERVAL_CONFIG_KEY, task.config_description)
        self.assertEqual(
            task.config_type[INTERVAL_CONFIG_KEY],
            {"min": MIN_SPRINT_INTERVAL_MS, "max": 10000, "step": 10},
        )

    # ── 2. WASD 检测与 0.2 秒起跑延迟 ───────────────────────

    def test_waits_start_delay_after_press_before_starting_sprint(self):
        self.assertAlmostEqual(START_DELAY_SECONDS, 0.2)
        harness = SprintHarness(pressed_vks={0x57})  # W
        harness.run()  # t=0.000 -> 开始计时，不冲刺
        self.assertEqual(harness.interaction.events, [])

        harness.now = 0.100
        harness.run()  # t=0.100 < 0.200 -> 不冲刺
        harness.now = 0.199
        harness.run()  # t=0.199 < 0.200 -> 不冲刺
        self.assertEqual(harness.interaction.events, [])

        harness.now = 0.200
        harness.run()  # t=0.200 >= 0.200 -> 开始首次冲刺
        self.assertEqual(
            harness.interaction.events,
            [("down", "shift", False), ("up", "shift")],
        )

    def test_brief_wasd_tap_under_start_delay_never_sprints(self):
        harness = SprintHarness(pressed_vks={0x57})
        harness.run()  # t=0.0
        harness.now = 0.15
        harness.run()  # t=0.15
        harness.pressed_vks.clear()
        harness.now = 0.18
        harness.run()  # t=0.18 松开
        harness.pressed_vks.add(0x57)
        harness.now = 0.30
        harness.run()  # t=0.30 重新按下，刚开始计时
        harness.now = 0.45
        harness.run()  # t=0.45 仅按住 0.15s
        self.assertEqual(harness.interaction.events, [])

    def test_each_wasd_key_triggers_shift_after_start_delay_hold(self):
        for vk in WASD_VK_CODES:
            with self.subTest(vk=hex(vk)):
                harness = SprintHarness(pressed_vks={vk})
                harness.hold_until_sprint()
                self.assertEqual(
                    harness.interaction.events,
                    [("down", "shift", False), ("up", "shift")],
                )
                self.assertEqual(harness.wait_calls, [KEY_DOWN_TIME])

    def test_switching_between_wasd_keys_without_release_keeps_hold_timer(self):
        """W -> W+D -> D 连续切换不松开全部方向键时，不重置 0.2 秒计时。"""
        harness = SprintHarness(pressed_vks={0x57})
        harness.run()  # t=0.0 按 W
        harness.now = 0.12
        harness.pressed_vks.add(0x44)  # W+D
        harness.run()  # t=0.12
        harness.now = 0.20
        harness.pressed_vks.discard(0x57)  # 仅 D
        harness.run()  # t=0.20 连续按住满 0.2s
        self.assertEqual(harness.shift_press_count, 1)

    def test_no_shift_when_no_wasd_pressed(self):
        harness = SprintHarness(pressed_vks=set())
        harness.run(times=5, gap=0.5)
        self.assertEqual(harness.interaction.events, [])

    def test_ignores_historical_key_bit_without_current_down_bit(self):
        """GetAsyncKeyState 返回 0x0001（仅历史触发位）不应视为当前按下。"""
        harness = SprintHarness()
        with patch.object(auto_sprint_task.win32api, "GetAsyncKeyState", return_value=0x0001):
            harness.task.run()
            harness.advance(START_DELAY_SECONDS)
            harness.task.run()
        self.assertEqual(harness.interaction.events, [])

    # ── 3. 冲刺间隔控制 ─────────────────────────────────────

    def test_default_interval_throttles_presses_after_start_delay(self):
        interval_s = DEFAULT_SPRINT_INTERVAL_MS / 1000.0
        harness = SprintHarness(pressed_vks={0x57})  # W
        harness.hold_until_sprint()  # t=0.200 -> 第 1 次
        self.assertEqual(harness.shift_press_count, 1)

        harness.now = START_DELAY_SECONDS + interval_s - 0.001
        harness.run()  # 差 1ms -> 冷却中
        self.assertEqual(harness.shift_press_count, 1)

        harness.now = START_DELAY_SECONDS + interval_s + 1e-6
        harness.run()  # 满 1 个间隔 -> 第 2 次
        self.assertEqual(harness.shift_press_count, 2)

        harness.now = START_DELAY_SECONDS + interval_s * 2 + 2e-6
        harness.run()  # 再满 1 个间隔 -> 第 3 次
        self.assertEqual(harness.shift_press_count, 3)

    def test_custom_interval_and_live_config_change(self):
        harness = SprintHarness(pressed_vks={0x44}, **{INTERVAL_CONFIG_KEY: 300})
        harness.hold_until_sprint()  # t=1.0 -> 第 1 次
        harness.advance(0.200)
        harness.run()  # t=1.200, 距上次 0.200 < 0.300
        self.assertEqual(harness.shift_press_count, 1)

        # 运行中调小间隔，立即生效
        harness.task.config[INTERVAL_CONFIG_KEY] = 100
        harness.run()  # t=1.200, 距上次 0.200 >= 0.100
        self.assertEqual(harness.shift_press_count, 2)

    def test_small_interval_clamps_key_down_time(self):
        harness = SprintHarness(pressed_vks={0x57}, **{INTERVAL_CONFIG_KEY: 10})
        harness.hold_until_sprint()
        self.assertEqual(harness.wait_calls, [0.005])

    def test_invalid_interval_falls_back_to_default(self):
        for bad_value in (0, -50, None, "abc", float("nan"), float("inf")):
            with self.subTest(bad_value=bad_value):
                harness = SprintHarness(pressed_vks={0x57}, **{INTERVAL_CONFIG_KEY: bad_value})
                self.assertAlmostEqual(
                    harness.task._sprint_interval_seconds(),
                    DEFAULT_SPRINT_INTERVAL_MS / 1000.0,
                )

    def test_releasing_wasd_resets_start_delay(self):
        harness = SprintHarness(pressed_vks={0x57})
        harness.hold_until_sprint()  # t=0.2 -> 第 1 次
        self.assertEqual(harness.shift_press_count, 1)

        # 松开全部 WASD，并等冷却完全过去
        harness.pressed_vks.clear()
        harness.now = 0.3
        harness.run()  # 重置按住起点

        # 重新按下后需再次按住满 0.2s 才触发
        harness.pressed_vks.add(0x41)
        harness.now = 1.0
        harness.run()  # t=1.0 -> 开始新一轮计时
        harness.now = 1.199
        harness.run()  # 仅按住 0.199s，不触发
        self.assertEqual(harness.shift_press_count, 1)

        harness.now = 1.0 + START_DELAY_SECONDS + 1e-6
        harness.run()  # 按住满 0.2s，触发第 2 次
        self.assertEqual(harness.shift_press_count, 2)

    # ── 4. 前台与运行状态保护 ───────────────────────────────

    def test_no_shift_when_game_not_foreground_and_resets_hold_timer(self):
        harness = SprintHarness(pressed_vks={0x57}, foreground=True)
        harness.run()  # t=0.0 开始计时
        harness.now = 0.15
        harness.foreground = False
        harness.run()  # t=0.15 失焦 -> 重置计时
        harness.foreground = True
        harness.now = 0.30
        harness.run()  # t=0.30 回到前台重新开始计时，不该立即触发
        self.assertEqual(harness.interaction.events, [])

        harness.now = 0.30 + START_DELAY_SECONDS
        harness.run()  # 满 0.2s 才触发
        self.assertEqual(harness.shift_press_count, 1)

    def test_no_shift_when_hwnd_window_missing(self):
        harness = SprintHarness(pressed_vks={0x57})
        harness.executor.device_manager.hwnd_window = None
        harness.hold_until_sprint()
        self.assertEqual(harness.interaction.events, [])

    def test_foreground_fallback_uses_win32gui_when_no_is_foreground_method(self):
        harness = SprintHarness(pressed_vks={0x57})
        harness.executor.device_manager.hwnd_window = SimpleNamespace(hwnd=4242)
        with patch.object(auto_sprint_task.win32gui, "GetForegroundWindow", return_value=9999):
            harness.hold_until_sprint()
        self.assertEqual(harness.interaction.events, [])
        with patch.object(auto_sprint_task.win32gui, "GetForegroundWindow", return_value=4242):
            harness.hold_until_sprint()
        self.assertEqual(harness.shift_press_count, 1)

    def test_no_shift_when_disabled_paused_or_exiting(self):
        for name, overrides in (
            ("disabled", {"enabled": False}),
            ("task_paused", {"paused": True}),
            ("executor_paused", {"executor_paused": True}),
        ):
            with self.subTest(case=name):
                harness = SprintHarness(pressed_vks={0x57}, **overrides)
                harness.hold_until_sprint()
                self.assertEqual(harness.interaction.events, [])

        harness = SprintHarness(pressed_vks={0x57})
        harness.executor.exit_event.set()
        harness.hold_until_sprint()
        self.assertEqual(harness.interaction.events, [])

    # ── 5. 释放保护与生命周期 ───────────────────────────────

    def test_disable_during_press_releases_shift_exactly_once_and_reenable_works(self):
        harness = SprintHarness(pressed_vks={0x57})
        harness.task._stop_event = threading.Event()
        entered_wait = threading.Event()
        original_wait = harness.task._stop_event.wait

        def _wait_and_disable(timeout=None):
            entered_wait.set()
            harness.task.disable()
            return original_wait(timeout)

        harness.task._stop_event.wait = _wait_and_disable

        with patch("ok.task.task.communicate"):
            harness.hold_until_sprint()

        self.assertTrue(entered_wait.is_set())
        self.assertEqual(
            harness.interaction.events,
            [("down", "shift", False), ("up", "shift")],
        )
        self.assertIsNone(harness.task._wasd_pressed_since)
        self.assertIsNone(harness.task._last_sprint_at)
        self.assertFalse(harness.task.enabled)

        # 重新启用后，需要重新按住满 1s 才触发
        harness.task._stop_event = threading.Event()
        with patch("ok.task.task.communicate"):
            harness.task.enable()
        harness.run()
        self.assertEqual(harness.shift_press_count, 1)
        harness.advance(START_DELAY_SECONDS)
        harness.run()
        self.assertEqual(harness.shift_press_count, 2)

    def test_on_destroy_releases_held_shift_and_blocks_future_input(self):
        harness = SprintHarness(pressed_vks={0x57})
        harness.task._held_interaction = harness.interaction
        harness.task.on_destroy()
        self.assertEqual(harness.interaction.events, [("up", "shift")])

        harness.hold_until_sprint()
        self.assertEqual(harness.interaction.events, [("up", "shift")])

    def test_failed_key_down_does_not_release_or_consume_cooldown(self):
        harness = SprintHarness(pressed_vks={0x57}, down_result=False)
        harness.hold_until_sprint()
        self.assertEqual(harness.interaction.events, [("down", "shift", False)])
        self.assertIsNone(harness.task._last_sprint_at)

    def test_exception_during_hold_still_releases_shift(self):
        harness = SprintHarness(pressed_vks={0x57})
        harness.task._stop_event.wait = MagicMock(side_effect=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            harness.hold_until_sprint()
        self.assertEqual(
            harness.interaction.events,
            [("down", "shift", False), ("up", "shift")],
        )

    # ── 6. 框架补丁与配置表单集成 ───────────────────────────

    def test_no_frame_patch_skips_capture_for_auto_sprint(self):
        harness = SprintHarness()
        executor = SimpleNamespace(current_task=harness.task)
        self.assertFalse(no_frame_task_patch._task_wants_frame(executor))

    def test_config_form_renders_interval_number_editor(self):
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        harness = SprintHarness()
        task = harness.task
        values = InMemoryConfig(task.default_config, task.default_config)
        with patch.object(og, "app", SimpleNamespace(tr=str)):
            form = config_form.ConfigForm(
                task,
                values,
                task.default_config,
                task.config_description,
                task.config_type,
            )
            self.assertEqual(
                list(form.config_widget_by_key.keys()),
                [
                    INTERVAL_CONFIG_KEY,
                    auto_sprint_task.USE_THREAD_CONFIG_KEY,
                    auto_sprint_task.SHIFT_MODE_CONFIG_KEY,
                ],
            )
            editor = form.config_widget_by_key[INTERVAL_CONFIG_KEY]
            self.assertIsInstance(editor, NumberEditor)
            self.assertEqual(editor.spin_box.value(), DEFAULT_SPRINT_INTERVAL_MS)
            self.assertEqual(editor.spin_box.spin_box.minimum(), MIN_SPRINT_INTERVAL_MS)
            self.assertEqual(editor.spin_box.spin_box.singleStep(), 10)
            editor.spin_box.setValue(220)
            self.assertEqual(values[INTERVAL_CONFIG_KEY], 220)
            form.deleteLater()
            app.processEvents()

    # ── 7. 独立线程 ─────────────────────────────────────────

    def test_worker_thread_sprints_without_framework_run(self):
        """独立线程在 run() 未被调用时也能按节奏触发冲刺。"""
        harness = SprintHarness(pressed_vks={0x57})
        task = harness.task
        task.config[auto_sprint_task.USE_THREAD_CONFIG_KEY] = True
        task.config[INTERVAL_CONFIG_KEY] = 10
        task._stop_event = threading.Event()

        with patch.object(auto_sprint_task.win32api, "GetAsyncKeyState", side_effect=harness.key_state):
            with patch("ok.task.task.communicate"):
                task._ensure_worker()
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and harness.shift_press_count < 1:
                    time.sleep(0.02)
            finally:
                task._stop_event.set()
                task._stop_worker()

        self.assertGreaterEqual(harness.shift_press_count, 1)
        self.assertTrue(task._stop_event.is_set())

    def test_worker_thread_does_not_start_when_disabled_by_config(self):
        harness = SprintHarness(pressed_vks={0x57})
        task = harness.task
        task._ensure_worker()
        self.assertIsNone(task._worker)

    # ── 8. Shift 冲刺模式 ───────────────────────────────────

    def test_shift_mode_off_by_default_and_keeps_wasd_behaviour(self):
        harness = SprintHarness(pressed_vks={0x57})
        self.assertFalse(harness.task.default_config[auto_sprint_task.SHIFT_MODE_CONFIG_KEY])
        self.assertFalse(harness.task._shift_mode())
        shift_only = SprintHarness(
            pressed_vks={0x10}, **{auto_sprint_task.SHIFT_MODE_CONFIG_KEY: False}
        )
        shift_only.run(times=3, gap=0.2)
        self.assertEqual(shift_only.interaction.events, [])

    def test_shift_mode_triggers_on_held_shift_with_new_edge(self):
        harness = SprintHarness(
            pressed_vks={0x10}, **{auto_sprint_task.SHIFT_MODE_CONFIG_KEY: True}
        )
        harness.hold_until_sprint()
        self.assertEqual(
            harness.interaction.events,
            [("up", "shift"), ("down", "shift", False), ("up", "shift")],
        )

    def test_shift_mode_ignores_wasd_and_requires_shift(self):
        harness = SprintHarness(
            pressed_vks={0x57}, **{auto_sprint_task.SHIFT_MODE_CONFIG_KEY: True}
        )
        harness.hold_until_sprint()
        self.assertEqual(harness.interaction.events, [])

    def test_shift_mode_respects_interval(self):
        interval_s = DEFAULT_SPRINT_INTERVAL_MS / 1000.0
        harness = SprintHarness(
            pressed_vks={0x10}, **{auto_sprint_task.SHIFT_MODE_CONFIG_KEY: True}
        )
        harness.hold_until_sprint()
        first = harness.shift_press_count
        self.assertEqual(first, 1)

        harness.advance(interval_s / 2)
        harness.run()
        self.assertEqual(harness.shift_press_count, first)

        harness.advance(interval_s / 2 + 1e-6)
        harness.run()
        self.assertEqual(harness.shift_press_count, first + 1)


if __name__ == "__main__":
    unittest.main()
