"""自动冲刺触发式任务测试。

全部 Win32 按键读取、键盘钩子、前台判定、pynput 与交互层均使用 stub，
不连接游戏窗口，不发送真实按键。

覆盖重点：
1. 「切换角色 + Shift」的快速跑动状态机（照抄 ok-nte 的节奏）。
2. 接管 Shift 所有权时只屏蔽物理 Shift、松开即归还。
3. 角色数量 / 间隔等界面配置的取值与边界。
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
from src.tasks.trigger import auto_sprint_task
from src.tasks.trigger.auto_sprint_task import (
    CHAR_COUNT_CONFIG_KEY,
    DEFAULT_SPRINT_INTERVAL_MS,
    INTERVAL_CONFIG_KEY,
    QUICK_RUN_HOLD_SECONDS,
    SHIFT_MODE_CONFIG_KEY,
    SWITCH_ENABLED_CONFIG_KEY,
    USE_THREAD_CONFIG_KEY,
    AutoSprintTask,
)


class FakePynput:
    """记录 pynput 按键序列。"""

    def __init__(self):
        self.events: list[tuple] = []

    def press(self, key):
        self.events.append(("press", key))

    def release(self, key):
        self.events.append(("release", key))

    @property
    def tapped(self) -> list:
        return [k for action, k in self.events if action == "press"]


class SprintHarness:
    """把 AutoSprintTask 从框架运行期剥离：假时钟 + 假键盘钩子 + 假 pynput。"""

    def __init__(
        self,
        *,
        physical_keys: set[int] | None = None,
        wasd_vks: set[int] | None = None,
        foreground: bool = True,
        enabled: bool = True,
        paused: bool = False,
        executor_paused: bool = False,
        **config_overrides,
    ):
        self.foreground = foreground
        self.wasd_vks: set[int] = set(wasd_vks or ())
        self.interaction = MagicMock()

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
        task.load_config()
        task.config.update(config_overrides)
        # 用例默认关线程（假时钟无法驱动真实线程）；显式传入时尊重用例设置。
        if USE_THREAD_CONFIG_KEY not in config_overrides:
            task.config[USE_THREAD_CONFIG_KEY] = False
        task._enabled = enabled
        task._paused = paused
        task._physical_keys = set(physical_keys or ())
        self.pynput = FakePynput()
        task._pynput = self.pynput
        task.start_listener = lambda: None  # 不启动真实钩子
        task.stop_listener = lambda: None
        task._ensure_worker = lambda: None
        self.task = task

    # ── 按键状态控制 ────────────────────────────────────────

    def hold_shift(self):
        self.task._physical_keys |= {0xA0}

    def release_shift_key(self):
        self.task._physical_keys -= {0xA0, 0xA1, 0x10}

    def key_state(self, vk: int) -> int:
        return -32768 if vk in self.wasd_vks else 0

    def run(self, times: int = 1):
        with patch.object(auto_sprint_task.win32api, "GetAsyncKeyState", side_effect=self.key_state):
            for _ in range(times):
                self.task._sprint_tick()

    def advance_to_quick_run(self):
        """按住 Shift 满进入阈值。"""
        self.hold_shift()
        self.task._shift_down_at = time.time() - QUICK_RUN_HOLD_SECONDS - 0.01
        self.task._shift_pressed = True


class TestAutoSprintTask(unittest.TestCase):
    # ── 1. 元数据与配置 ─────────────────────────────────────

    def test_metadata_and_defaults(self):
        harness = SprintHarness()
        task = harness.task
        self.assertEqual(task.name, "自动冲刺")
        self.assertEqual(task.icon, Icons.Trigger)
        self.assertIs(task.needs_frame, False)
        self.assertEqual(task.trigger_interval, 0)
        self.assertEqual(task.default_config[INTERVAL_CONFIG_KEY], DEFAULT_SPRINT_INTERVAL_MS)
        self.assertAlmostEqual(DEFAULT_SPRINT_INTERVAL_MS, 200)
        self.assertEqual(task.default_config[CHAR_COUNT_CONFIG_KEY], 2)
        self.assertTrue(task.default_config[SHIFT_MODE_CONFIG_KEY])
        self.assertTrue(task.default_config[SWITCH_ENABLED_CONFIG_KEY])
        self.assertFalse(task.default_config["_enabled"])

    def test_char_count_is_clamped(self):
        for raw, expected in ((0, 1), (1, 1), (2, 2), (4, 4), (9, 4), ("3", 3), ("bad", 2)):
            with self.subTest(raw=raw):
                task = SprintHarness(**{CHAR_COUNT_CONFIG_KEY: raw}).task
                self.assertEqual(task._char_count(), expected)

    def test_interval_config_and_clamp(self):
        task = SprintHarness(**{INTERVAL_CONFIG_KEY: 200}).task
        self.assertAlmostEqual(task._sprint_interval_seconds(), 0.2)
        task.config[INTERVAL_CONFIG_KEY] = 0
        self.assertAlmostEqual(
            task._sprint_interval_seconds(), DEFAULT_SPRINT_INTERVAL_MS / 1000.0
        )
        task.config[INTERVAL_CONFIG_KEY] = "bad"
        self.assertAlmostEqual(
            task._sprint_interval_seconds(), DEFAULT_SPRINT_INTERVAL_MS / 1000.0
        )

    # ── 2. Shift 所有权接管 ─────────────────────────────────

    def test_hold_shift_enters_quick_run_and_releases_physical_shift(self):
        harness = SprintHarness()
        harness.advance_to_quick_run()
        harness.run()

        self.assertTrue(harness.task._quick_running)
        # 接管时应先释放左右 Shift
        self.assertIn(("release", auto_sprint_task.Key.shift_l), harness.pynput.events)
        self.assertIn(("release", auto_sprint_task.Key.shift_r), harness.pynput.events)

    def test_does_not_start_before_hold_threshold(self):
        harness = SprintHarness(physical_keys={0xA0})
        harness.task._shift_down_at = time.time()
        harness.task._shift_pressed = True
        harness.run()
        self.assertFalse(harness.task._quick_running)
        self.assertEqual(harness.pynput.events, [])

    def test_releasing_shift_stops_and_resets(self):
        harness = SprintHarness()
        harness.advance_to_quick_run()
        harness.run()
        self.assertTrue(harness.task._quick_running)

        harness.release_shift_key()
        harness.run()
        self.assertFalse(harness.task._quick_running)
        self.assertFalse(harness.task._shift_pressed)

    def test_suppress_only_shift_while_quick_running(self):
        harness = SprintHarness()
        task = harness.task
        task._listener = MagicMock()
        task._listener.suppress_event = MagicMock()

        # 未进入快速跑动：不屏蔽
        self.assertFalse(task._should_suppress(auto_sprint_task.KEY_DOWN_MESSAGES[0], 0xA0))

        task._quick_running = True
        self.assertTrue(task._should_suppress(auto_sprint_task.KEY_DOWN_MESSAGES[0], 0xA0))
        # 非 Shift 键不屏蔽
        self.assertFalse(task._should_suppress(auto_sprint_task.KEY_DOWN_MESSAGES[0], 0x57))

    def test_injected_events_are_ignored_by_filter(self):
        harness = SprintHarness()
        task = harness.task
        data = SimpleNamespace(vkCode=0xA0, flags=0x10)
        self.assertTrue(task._win32_filter(auto_sprint_task.KEY_DOWN_MESSAGES[0], data))
        self.assertEqual(task._physical_keys, set())

    # ── 3. 切人 + Shift 节奏 ────────────────────────────────

    def test_switch_mode_taps_character_slot_then_shift(self):
        harness = SprintHarness(**{SWITCH_ENABLED_CONFIG_KEY: True, CHAR_COUNT_CONFIG_KEY: 2})
        harness.advance_to_quick_run()
        harness.run()

        tapped = harness.pynput.tapped
        self.assertIn("1", tapped, f"应切换第 1 个角色，实际: {tapped}")

    def test_character_slot_cycles_between_configured_count(self):
        harness = SprintHarness(**{CHAR_COUNT_CONFIG_KEY: 2, SWITCH_ENABLED_CONFIG_KEY: True})
        harness.advance_to_quick_run()
        harness.run()
        harness.task._quick_step = 0
        harness.run()

        slots = [k for k in harness.pynput.tapped if k in {"1", "2", "3", "4"}]
        self.assertEqual(slots, ["1", "2"])

    def test_char_count_one_means_shift_only(self):
        """角色数量为 1 时切人无意义，应退化为纯 Shift，不发送任何角色键。"""
        harness = SprintHarness(**{CHAR_COUNT_CONFIG_KEY: 1, SWITCH_ENABLED_CONFIG_KEY: True})
        harness.advance_to_quick_run()
        harness.run()
        harness.task._quick_step = 0
        harness.run()

        slots = [k for k in harness.pynput.tapped if k in {"1", "2", "3", "4"}]
        self.assertEqual(slots, [], f"角色数量为 1 时不应发送角色键，实际: {slots}")
        self.assertTrue(harness.pynput.tapped)

    def test_switch_disabled_only_taps_shift(self):
        harness = SprintHarness(**{SWITCH_ENABLED_CONFIG_KEY: False})
        harness.advance_to_quick_run()
        harness.run(times=3)

        tapped = harness.pynput.tapped
        self.assertTrue(tapped)
        self.assertFalse([k for k in tapped if k in {"1", "2", "3", "4"}])
        # "lshift" 会经由 PYNPUT_KEY_MAP 映射为 Key.shift_l
        self.assertEqual(tapped[0], auto_sprint_task.Key.shift_l)

    def test_shift_only_mode_respects_interval(self):
        harness = SprintHarness(
            **{SWITCH_ENABLED_CONFIG_KEY: False, INTERVAL_CONFIG_KEY: 200}
        )
        harness.advance_to_quick_run()

        harness.run()
        self.assertEqual(len(harness.pynput.tapped), 1)

        harness.run()  # 立刻再跑一次，应被间隔挡住
        self.assertEqual(len(harness.pynput.tapped), 1)

    # ── 4. 保护条件 ─────────────────────────────────────────

    def test_no_action_when_game_not_foreground(self):
        harness = SprintHarness(foreground=False)
        harness.advance_to_quick_run()
        harness.run()
        self.assertEqual(harness.pynput.events, [])

    def test_no_action_when_disabled_or_paused(self):
        for overrides in (
            {"enabled": False},
            {"paused": True},
            {"executor_paused": True},
        ):
            with self.subTest(**overrides):
                harness = SprintHarness(**overrides)
                harness.advance_to_quick_run()
                harness.run()
                self.assertEqual(harness.pynput.events, [])

    def test_no_action_when_hwnd_missing(self):
        harness = SprintHarness()
        harness.executor.device_manager.hwnd_window = None
        harness.advance_to_quick_run()
        harness.run()
        self.assertEqual(harness.pynput.events, [])

    def test_foreground_fallback_uses_win32gui(self):
        harness = SprintHarness()
        harness.executor.device_manager.hwnd_window = SimpleNamespace(hwnd=4242)
        harness.advance_to_quick_run()
        with patch.object(auto_sprint_task.win32gui, "GetForegroundWindow", return_value=9999):
            harness.run()
        self.assertEqual(harness.pynput.events, [])
        with patch.object(auto_sprint_task.win32gui, "GetForegroundWindow", return_value=4242):
            harness.run()
        self.assertTrue(harness.pynput.events)

    # ── 5. 生命周期 ─────────────────────────────────────────

    def test_disable_resets_quick_run_and_stops_worker(self):
        harness = SprintHarness()
        harness.advance_to_quick_run()
        harness.run()
        self.assertTrue(harness.task._quick_running)

        with patch("ok.task.task.communicate"):
            harness.task.disable()

        self.assertFalse(harness.task._quick_running)
        self.assertFalse(harness.task._shift_pressed)

    def test_on_destroy_releases_and_blocks_input(self):
        harness = SprintHarness()
        harness.advance_to_quick_run()
        harness.run()
        with patch("ok.task.task.communicate"):
            harness.task.on_destroy()
        self.assertTrue(harness.task._destroyed)
        self.assertFalse(harness.task._can_input())

    # ── 6. 界面配置 ─────────────────────────────────────────

    def test_release_shift_bypasses_backend_pairing_guard(self):
        """真实后端 send_key_up 有配对保护，玩家手按的 Shift 未登记会被丢弃。

        回归用例：接管所有权时必须同时走 pynput 直接释放，否则游戏侧仍认为
        Shift 按着，后续无法产生新的按下边沿（表现为按住却不冲刺）。
        """
        harness = SprintHarness()

        call_order: list[tuple] = []
        harness.task._pynput.press = lambda k: call_order.append(("pynput_press", k))
        harness.task._pynput.release = lambda k: call_order.append(("pynput_release", k))

        backend = MagicMock()
        # 模拟真实后端的配对保护：未登记的键释放被静默忽略
        backend.send_key_up = MagicMock(
            side_effect=lambda key: call_order.append(("backend_up", key))
        )
        harness.executor.interaction = backend

        harness.task._release_shift_keys()

        actions = [a for a, _ in call_order]
        self.assertIn("pynput_release", actions, "必须直接走 pynput 释放，绕开配对保护")
        self.assertIn("backend_up", actions, "同时通知后端释放，保持两边状态一致")

    def test_thread_mode_run_does_not_drive_state_machine(self):
        """启用独立线程时 run() 不得再驱动状态机，否则会重复发键。"""
        harness = SprintHarness(**{USE_THREAD_CONFIG_KEY: True})
        harness.advance_to_quick_run()
        task = harness.task
        task._ensure_worker = lambda: None
        # 仅允许 _can_input 通过，隔离出「run() 是否驱动状态机」这一行为
        with patch.object(AutoSprintTask, "_can_input", return_value=True):
            driven = []
            with patch.object(AutoSprintTask, "_handle_quick_run", side_effect=lambda now: driven.append(now)):
                task.run()
        self.assertEqual(driven, [], "线程模式下 run() 不应再驱动状态机")

    def test_wasd_mode_keeps_620_default_interval(self):
        """WASD 模式未显式配置间隔时沿用上游 620ms，不被快速跑动的 200ms 影响。"""
        task = SprintHarness().task
        task.config.pop(INTERVAL_CONFIG_KEY, None)
        self.assertAlmostEqual(task._wasd_interval_seconds(), 0.62)
        self.assertAlmostEqual(task._sprint_interval_seconds(), 0.2)

    def test_worker_stop_uses_dedicated_event(self):
        """关闭线程配置时用专属事件停 worker，不能污染 _stop_event。"""
        harness = SprintHarness(**{USE_THREAD_CONFIG_KEY: False})
        task = harness.task
        task._ensure_worker()

        self.assertFalse(task._stop_event.is_set(), "_stop_event 不应被配置关闭影响")
        self.assertIsNone(task._worker)

    def test_exclusive_owner_blocks_input(self):
        """执行器被其他任务独占时不应继续发键。"""
        harness = SprintHarness()
        harness.executor.exclusive_owner = object()
        harness.advance_to_quick_run()
        harness.run()
        self.assertEqual(harness.pynput.events, [])
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
            keys = list(form.config_widget_by_key.keys())
            self.assertEqual(
                keys,
                [
                    INTERVAL_CONFIG_KEY,
                    USE_THREAD_CONFIG_KEY,
                    SHIFT_MODE_CONFIG_KEY,
                    SWITCH_ENABLED_CONFIG_KEY,
                    CHAR_COUNT_CONFIG_KEY,
                ],
            )
            editor = form.config_widget_by_key[INTERVAL_CONFIG_KEY]
            self.assertIsInstance(editor, NumberEditor)
            self.assertEqual(editor.spin_box.value(), DEFAULT_SPRINT_INTERVAL_MS)
            editor.spin_box.setValue(200)
            self.assertEqual(values[INTERVAL_CONFIG_KEY], 200)
            form.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
