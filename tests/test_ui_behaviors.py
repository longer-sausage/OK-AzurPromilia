"""界面行为测试：关闭询问、提醒调度、快捷键解析与动作、任务栏进度、HUD。"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from src.core.close_behavior import (
    CHOICE_CANCEL,
    CHOICE_CLOSE,
    CHOICE_TRAY,
    CloseBehavior,
    CloseBehaviorError,
    decide,
    load_settings,
    resolve,
    save_settings,
    should_exit,
    should_tray,
)
from src.core.hotkeys import (
    DEFAULT_HOTKEYS,
    FRAMEWORK_HOTKEYS,
    HOTKEY_ID_BASE,
    Hotkey,
    HotkeyError,
    build_hotkey,
    build_plan,
    find_action,
    identify,
    load_hotkeys,
    parse_hotkey,
    save_hotkeys,
    validate,
)
from src.core.hud import (
    UNKNOWN_REMAINING,
    HudSettings,
    build_hud_text,
    format_remaining,
    hud_position,
    remaining_seconds,
)
from src.core.hud import (
    load_settings as load_hud_settings,
)
from src.core.hud import (
    save_settings as save_hud_settings,
)
from src.core.ledger import RunLog
from src.core.privacy import default_config
from src.core.reminders import (
    REMINDER_DAILY,
    REMINDER_STAMINA,
    ReminderScheduler,
    ReminderSettings,
    current_reset_date,
    next_daily_reset,
)
from src.core.taskbar import (
    NullTaskbar,
    TaskbarProgress,
    WindowsTaskbar,
    compute_ratio,
    create_backend,
)
from src.patches.ui_extras_patch import perform_action


class TestCloseBehavior(unittest.TestCase):
    def test_default_asks(self):
        behavior = CloseBehavior()
        self.assertTrue(behavior.ask)
        self.assertIsNone(decide(behavior))

    def test_ask_disabled_closes(self):
        self.assertEqual(decide(CloseBehavior(ask=False)), CHOICE_CLOSE)

    def test_remembered_choice_wins(self):
        self.assertEqual(decide(CloseBehavior(remember=CHOICE_TRAY)), CHOICE_TRAY)
        self.assertEqual(decide(CloseBehavior(remember=CHOICE_CLOSE)), CHOICE_CLOSE)

    def test_invalid_remember(self):
        with self.assertRaises(CloseBehaviorError):
            CloseBehavior(remember=CHOICE_CANCEL)

    def test_resolve_remembers_only_tray_and_close(self):
        action, behavior = resolve(CloseBehavior(), CHOICE_TRAY, remember=True)
        self.assertEqual(action, CHOICE_TRAY)
        self.assertEqual(behavior.remember, CHOICE_TRAY)

        action, behavior = resolve(CloseBehavior(), CHOICE_CANCEL, remember=True)
        self.assertEqual(action, CHOICE_CANCEL)
        self.assertEqual(behavior.remember, "")

    def test_resolve_without_remember(self):
        _action, behavior = resolve(CloseBehavior(remember=CHOICE_TRAY), CHOICE_CLOSE, remember=False)
        self.assertEqual(behavior.remember, CHOICE_TRAY)

    def test_resolve_unknown_answer(self):
        with self.assertRaises(CloseBehaviorError):
            resolve(CloseBehavior(), "explode", remember=False)

    def test_helpers(self):
        self.assertTrue(should_tray(CHOICE_TRAY))
        self.assertFalse(should_tray(CHOICE_CLOSE))
        self.assertTrue(should_exit(CHOICE_CLOSE))
        self.assertFalse(should_exit(CHOICE_CANCEL))

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "close.json"
            save_settings(CloseBehavior(ask=True, remember=CHOICE_TRAY), path)
            self.assertEqual(load_settings(path).remember, CHOICE_TRAY)

    def test_load_missing_and_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "close.json"
            self.assertIsNone(decide(load_settings(path)))
            path.write_text("{ oops", encoding="utf-8")
            self.assertIsNone(decide(load_settings(path)))

    def test_cleared(self):
        self.assertEqual(CloseBehavior(remember=CHOICE_TRAY).cleared().remember, "")


class TestReminderSettings(unittest.TestCase):
    def test_defaults(self):
        settings = ReminderSettings()
        self.assertTrue(settings.enabled)
        self.assertTrue(settings.daily_enabled_effective)
        self.assertFalse(settings.stamina_enabled_effective)

    def test_bad_hour(self):
        for hour in (-1, 24):
            with self.subTest(hour=hour), self.assertRaises(ValueError):
                ReminderSettings(daily_reset_hour=hour)

    def test_negative_interval(self):
        with self.assertRaises(ValueError):
            ReminderSettings(stamina_interval_minutes=-5)

    def test_stamina_requires_interval(self):
        settings = ReminderSettings(stamina_enabled=True, stamina_interval_minutes=0)
        self.assertFalse(settings.stamina_enabled_effective)

    def test_master_switch(self):
        settings = ReminderSettings(
            enabled=False, stamina_enabled=True, stamina_interval_minutes=30
        )
        self.assertFalse(settings.daily_enabled_effective)
        self.assertFalse(settings.stamina_enabled_effective)

    def test_from_dict(self):
        settings = ReminderSettings.from_dict(
            {"enabled": True, "daily_reset_hour": "6", "stamina_enabled": True, "stamina_interval_minutes": "15"}
        )
        self.assertEqual(settings.daily_reset_hour, 6)
        self.assertEqual(settings.stamina_interval_minutes, 15)

    def test_from_dict_bad(self):
        with self.assertRaises(ValueError):
            ReminderSettings.from_dict({"daily_reset_hour": 99})


class TestReminderScheduler(unittest.TestCase):
    def _at(self, year, month, day, hour, minute=0):
        import datetime

        return datetime.datetime(year, month, day, hour, minute).timestamp()

    def test_no_daily_before_crossing(self):
        now = self._at(2026, 11, 24, 3, 0)
        scheduler = ReminderScheduler(ReminderSettings(), clock=lambda: now)
        self.assertEqual(scheduler.poll(now), [])

    def test_daily_fires_after_reset(self):
        start = self._at(2026, 11, 24, 3, 0)
        scheduler = ReminderScheduler(ReminderSettings(), clock=lambda: start)
        later = self._at(2026, 11, 24, 4, 30)
        reminders = scheduler.poll(later)
        self.assertEqual([reminder.kind for reminder in reminders], [REMINDER_DAILY])
        self.assertEqual(reminders[0].to_notification()[1], "每日重置")
        # 同一天再轮询不重复
        self.assertEqual(scheduler.poll(later + 60), [])

    def test_daily_fires_next_day(self):
        start = self._at(2026, 11, 24, 5, 0)
        scheduler = ReminderScheduler(ReminderSettings(), clock=lambda: start)
        tomorrow = self._at(2026, 11, 25, 5, 0)
        self.assertEqual(len(scheduler.poll(tomorrow)), 1)

    def test_daily_disabled(self):
        start = self._at(2026, 11, 24, 3, 0)
        scheduler = ReminderScheduler(ReminderSettings(daily_reset_enabled=False), clock=lambda: start)
        self.assertEqual(scheduler.poll(self._at(2026, 11, 24, 6, 0)), [])

    def test_stamina_interval(self):
        start = self._at(2026, 11, 24, 10, 0)
        settings = ReminderSettings(daily_reset_enabled=False, stamina_enabled=True, stamina_interval_minutes=30)
        scheduler = ReminderScheduler(settings, clock=lambda: start)
        self.assertEqual(scheduler.poll(start + 60), [])
        fired = scheduler.poll(start + 30 * 60)
        self.assertEqual([reminder.kind for reminder in fired], [REMINDER_STAMINA])
        self.assertEqual(scheduler.poll(start + 31 * 60), [])
        self.assertEqual(len(scheduler.poll(start + 61 * 60)), 1)

    def test_seconds_to_daily_reset(self):
        now = self._at(2026, 11, 24, 3, 0)
        scheduler = ReminderScheduler(ReminderSettings(), clock=lambda: now)
        self.assertAlmostEqual(scheduler.seconds_to_daily_reset(now), 3600, delta=1)

    def test_next_daily_reset_and_current_date(self):
        now = self._at(2026, 11, 24, 5, 0)
        self.assertGreater(next_daily_reset(now, 4), now)
        self.assertEqual(current_reset_date(now, 4).day, 24)
        self.assertEqual(current_reset_date(self._at(2026, 11, 24, 3, 0), 4).day, 23)

    def test_reset_clears_stamina_timer(self):
        start = self._at(2026, 11, 24, 10, 0)
        clock = {"now": start}
        settings = ReminderSettings(daily_reset_enabled=False, stamina_enabled=True, stamina_interval_minutes=30)
        scheduler = ReminderScheduler(settings, clock=lambda: clock["now"])
        scheduler.poll(start + 30 * 60)
        clock["now"] = start + 31 * 60
        scheduler.reset()  # 计时从「现在」重新开始
        self.assertEqual(scheduler.poll(start + 40 * 60), [])
        self.assertEqual(len(scheduler.poll(start + 62 * 60)), 1)


class TestHotkeys(unittest.TestCase):
    def test_parse_simple(self):
        modifiers, vk = parse_hotkey("ctrl+alt+u")
        self.assertEqual(modifiers, 0x0002 | 0x0001)
        self.assertEqual(vk, ord("U"))

    def test_parse_function_key(self):
        modifiers, vk = parse_hotkey("F9")
        self.assertEqual(modifiers, 0)
        self.assertEqual(vk, 0x78)

    def test_parse_aliases(self):
        self.assertEqual(parse_hotkey("control + shift + space"), (0x0002 | 0x0004, 0x20))

    def test_plain_letter_rejected(self):
        with self.assertRaises(HotkeyError):
            parse_hotkey("u")

    def test_unknown_key(self):
        with self.assertRaises(HotkeyError):
            parse_hotkey("ctrl+alt+超能力")

    def test_two_main_keys(self):
        with self.assertRaises(HotkeyError):
            parse_hotkey("ctrl+a+b")

    def test_missing_main_key(self):
        with self.assertRaises(HotkeyError):
            parse_hotkey("ctrl+alt")

    def test_build_hotkey(self):
        hotkey = build_hotkey("pause", "Ctrl+Alt+P")
        self.assertEqual(hotkey.action, "pause")
        self.assertIn("pause", hotkey.label())

    def test_identify_normalizes_order(self):
        self.assertEqual(identify("alt+ctrl+u"), identify("ctrl+alt+u"))

    def test_validate_ok(self):
        self.assertEqual(validate({"pause": "ctrl+alt+p", "stop": "ctrl+alt+s"}), [])

    def test_validate_reserved(self):
        issues = validate({"stop": "f9"})
        self.assertTrue(any("框架" in issue for issue in issues))
        self.assertIn("f9", FRAMEWORK_HOTKEYS)

    def test_validate_duplicate(self):
        issues = validate({"pause": "ctrl+alt+p", "stop": "alt+ctrl+p"})
        self.assertTrue(any("重复" in issue for issue in issues))

    def test_validate_bad_spec(self):
        issues = validate({"pause": "u"})
        self.assertTrue(issues)

    def test_build_plan(self):
        plan = build_plan({"pause": "ctrl+alt+p", "stop": "ctrl+alt+s"})
        self.assertEqual([entry["id"] for entry in plan], [HOTKEY_ID_BASE, HOTKEY_ID_BASE + 1])
        self.assertEqual(plan[0]["vk"], ord("P"))

    def test_build_plan_raises_on_conflict(self):
        with self.assertRaises(HotkeyError):
            build_plan({"stop": "f9"})

    def test_build_plan_accepts_hotkeys(self):
        plan = build_plan([Hotkey("pause", "ctrl+alt+p", 0x0003, ord("P"))])
        self.assertEqual(plan[0]["action"], "pause")

    def test_defaults_are_valid(self):
        self.assertEqual(validate(DEFAULT_HOTKEYS), [])
        self.assertIn("toggle_uid_mask", DEFAULT_HOTKEYS)

    def test_find_action(self):
        plan = build_plan(DEFAULT_HOTKEYS)
        self.assertEqual(find_action(plan, HOTKEY_ID_BASE), "pause")
        self.assertIsNone(find_action(plan, 424242))

    def test_hotkeys_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hotkeys.json"
            self.assertEqual(load_hotkeys(path), dict(DEFAULT_HOTKEYS))
            save_hotkeys({"pause": "ctrl+shift+p"}, path)
            loaded = load_hotkeys(path)
            self.assertEqual(loaded["pause"], "ctrl+shift+p")
            self.assertEqual(loaded["stop"], DEFAULT_HOTKEYS["stop"])

    def test_hotkeys_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hotkeys.json"
            path.write_text("{ oops", encoding="utf-8")
            self.assertEqual(load_hotkeys(path), dict(DEFAULT_HOTKEYS))


class FakeExecutor:
    def __init__(self):
        self.paused = False
        self.calls: list[str] = []

    def pause(self, task=None):
        self.paused = True
        self.calls.append("pause")

    def start(self):
        self.paused = False
        self.calls.append("start")

    def stop_current_task(self):
        self.calls.append("stop_current_task")


class TestHotkeyActions(unittest.TestCase):
    def setUp(self):
        from ok import og

        self.executor = FakeExecutor()
        self._patch = mock.patch.object(og, "executor", self.executor, create=True)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_pause_toggles(self):
        self.assertTrue(perform_action("pause"))
        self.assertTrue(self.executor.paused)
        self.assertTrue(perform_action("pause"))
        self.assertFalse(self.executor.paused)
        self.assertEqual(self.executor.calls, ["pause", "start"])

    def test_stop_current_task(self):
        self.assertTrue(perform_action("stop"))
        self.assertEqual(self.executor.calls, ["stop_current_task"])

    def test_toggle_uid_mask(self):
        config = default_config()
        with (
            mock.patch("src.core.privacy.load_config", return_value=config),
            mock.patch("src.core.privacy.save_config") as save,
        ):
            self.assertTrue(perform_action("toggle_uid_mask"))
        saved = save.call_args[0][0]
        self.assertFalse(saved.enabled)
        self.assertEqual(saved.count, config.count)

    def test_unknown_action(self):
        self.assertFalse(perform_action("teleport"))

    def test_missing_executor(self):
        from ok import og

        with mock.patch.object(og, "executor", None, create=True):
            self.assertFalse(perform_action("pause"))


class TestHudLogic(unittest.TestCase):
    def test_format_remaining(self):
        self.assertEqual(format_remaining(None), UNKNOWN_REMAINING)
        self.assertEqual(format_remaining(float("nan")), UNKNOWN_REMAINING)
        self.assertEqual(format_remaining(-5), UNKNOWN_REMAINING)
        self.assertEqual(format_remaining(0), "0 秒")
        self.assertEqual(format_remaining(58), "58 秒")
        self.assertEqual(format_remaining(60), "1 分钟")
        self.assertEqual(format_remaining(750), "12 分 30 秒")
        self.assertEqual(format_remaining(3600), "1 小时")
        self.assertEqual(format_remaining(3660), "1 小时 1 分")

    def test_build_text_without_task_is_empty(self):
        self.assertEqual(build_hud_text(""), "")
        self.assertEqual(build_hud_text("   "), "")

    def test_build_text_running(self):
        text = build_hud_text("一键日常", False, 0.42, 750)
        self.assertIn("运行中", text)
        self.assertIn("一键日常", text)
        self.assertIn("42%", text)
        self.assertIn("12 分 30 秒", text)

    def test_build_text_paused(self):
        self.assertIn("已暂停", build_hud_text("A", True, None, None))

    def test_build_text_caps_progress(self):
        self.assertIn("97%", build_hud_text("A", False, 5.0, None))

    def test_build_text_hides_remaining(self):
        self.assertNotIn("预计", build_hud_text("A", False, 0.1, 100, show_remaining=False))

    def test_remaining_seconds(self):
        self.assertEqual(remaining_seconds(1000, 300), 700.0)
        self.assertEqual(remaining_seconds(100, 500), 0.0)
        self.assertIsNone(remaining_seconds(None, 10))
        self.assertIsNone(remaining_seconds(10, None))

    def test_hud_position_bottom_center(self):
        x, y = hud_position(0, 0, 1920, 1080, 300, 36, 24)
        self.assertEqual(x, 810)
        self.assertEqual(y, 1020)

    def test_hud_position_respects_screen_offset(self):
        x, _y = hud_position(1920, 0, 1920, 1080, 300, 36, 24)
        self.assertEqual(x, 1920 + 810)

    def test_hud_position_clamps_oversize_window(self):
        x, y = hud_position(0, 0, 800, 600, 1200, 700, 24)
        self.assertEqual((x, y), (0, 0))

    def test_settings_roundtrip_and_preserves_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ui_extras.json"
            path.write_text('{"taskbar_progress": false, "hud_enabled": true}', encoding="utf-8")
            settings = load_hud_settings(path)
            self.assertTrue(settings.enabled)
            save_hud_settings(HudSettings(enabled=False, margin=40, show_remaining=False), path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(saved["hud_enabled"])
            self.assertFalse(saved["taskbar_progress"])  # 别的键不能被吃掉
            self.assertEqual(saved["hud_margin"], 40)

    def test_settings_missing_and_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            self.assertTrue(load_hud_settings(path).enabled)
            path.write_text("{ oops", encoding="utf-8")
            self.assertTrue(load_hud_settings(path).enabled)


class TestTaskbarProgress(unittest.TestCase):
    def test_update_pushes_backend(self):
        backend = NullTaskbar()
        progress = TaskbarProgress(backend)
        self.assertTrue(progress.update(0.5))
        self.assertEqual(backend.calls, [0.5])

    def test_throttle_small_changes(self):
        backend = NullTaskbar()
        progress = TaskbarProgress(backend)
        progress.update(0.5)
        self.assertFalse(progress.update(0.505))
        self.assertTrue(progress.update(0.6))

    def test_clamped(self):
        backend = NullTaskbar()
        progress = TaskbarProgress(backend)
        progress.update(5.0)
        self.assertAlmostEqual(progress.last, 0.97)
        progress.update(-1.0)
        self.assertAlmostEqual(progress.last, 0.0)

    def test_clear(self):
        backend = NullTaskbar()
        progress = TaskbarProgress(backend)
        progress.update(0.4)
        self.assertTrue(progress.clear())
        self.assertIsNone(progress.last)
        self.assertEqual(backend.calls[-1], None)

    def test_update_none_clears(self):
        progress = TaskbarProgress(NullTaskbar())
        progress.update(0.4)
        self.assertTrue(progress.update(None))
        self.assertIsNone(progress.last)

    def test_fail_sets_error_state(self):
        backend = NullTaskbar()
        TaskbarProgress(backend).fail()
        self.assertIn("error", backend.calls)

    def test_compute_ratio_without_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = RunLog(Path(tmp) / "runs.jsonl")
            self.assertIsNone(compute_ratio(log, "A", 10.0))

    def test_compute_ratio_with_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = RunLog(Path(tmp) / "runs.jsonl")
            log.record("A", True, 100.0)
            self.assertAlmostEqual(compute_ratio(log, "A", 50.0), 0.5)

    def test_create_backend_without_hwnd(self):
        self.assertIsInstance(create_backend(0), NullTaskbar)

    def test_windows_backend_without_hwnd_is_unavailable(self):
        backend = WindowsTaskbar(0)
        self.assertFalse(backend.available)
        self.assertFalse(backend.set_progress(0.5))
        self.assertFalse(backend.set_state("normal"))


class TestTaskbarThreadPlacement(unittest.TestCase):
    """COM 对象必须在使用它的线程里创建（``ITaskbarList3`` 是 STA 对象）。"""

    def setUp(self):
        from src.patches import ui_extras_patch

        self.patch_module = ui_extras_patch
        self.patch_module.stop_all()

    def tearDown(self):
        self.patch_module.stop_all()

    def test_backend_is_created_inside_worker_thread(self):
        created: dict[str, int] = {}
        real = self.patch_module.create_backend

        def spy(hwnd):
            created["thread"] = threading.get_ident()
            return real(hwnd)

        self.patch_module.create_backend = spy
        try:
            stop = threading.Event()
            thread = self.patch_module.start_taskbar_thread(0, stop_event=stop)
            self.assertIsNotNone(thread)
            deadline = time.monotonic() + 2.0
            while "thread" not in created and time.monotonic() < deadline:
                time.sleep(0.02)
            stop.set()
            thread.join(timeout=2.0)
        finally:
            self.patch_module.create_backend = real

        self.assertIn("thread", created)
        self.assertNotEqual(created["thread"], threading.main_thread().ident)

    def test_thread_is_idempotent(self):
        stop = threading.Event()
        first = self.patch_module.start_taskbar_thread(0, stop_event=stop)
        second = self.patch_module.start_taskbar_thread(0, stop_event=stop)
        self.assertIs(first, second)
        stop.set()
        first.join(timeout=2.0)

    def test_backend_cleanup_is_safe(self):
        from src.core.taskbar import NullTaskbar, WindowsTaskbar, uninitialize_backend

        self.assertIsNone(uninitialize_backend(NullTaskbar()))
        self.assertTrue(WindowsTaskbar(0).close())  # 没有 COM 也不能抛


class _StubRunLog:
    """只要 estimate_seconds / progress_ratio 的替身（任务栏 tick 只用这两个）。"""

    def estimate_seconds(self, task: str, fallback: float = 0.0):
        return 10.0

    def progress_ratio(self, task: str, elapsed: float, fallback_seconds: float | None = None):
        return min(0.97, max(0.0, elapsed / 10.0))


class TestBackgroundTickResilience(unittest.TestCase):
    """后台线程的一轮绝不能抛异常：崩一次，提醒/进度就再也不工作了。"""
    def test_reminder_tick_survives_settings_failure(self):
        from src.core.reminders import ReminderScheduler, ReminderSettings
        from src.patches.ui_extras_patch import reminder_tick

        logs = []

        def broken_loader():
            raise OSError("配置读不了")

        settings = ReminderSettings()
        scheduler = ReminderScheduler(settings)
        state = {}
        result = reminder_tick(
            scheduler, settings, broken_loader, lambda *a: None, state, logs.append
        )
        self.assertEqual(result, settings)  # 设置保持不变
        self.assertEqual(len(logs), 1)
        self.assertIn("配置读不了", logs[0])

        # 同一个错误连发多次只记一条（不刷日志）
        for _ in range(5):
            reminder_tick(scheduler, settings, broken_loader, lambda *a: None, state, logs.append)
        self.assertEqual(len(logs), 1)

    def test_reminder_tick_switches_settings(self):
        from src.core.reminders import ReminderScheduler, ReminderSettings
        from src.patches.ui_extras_patch import reminder_tick

        scheduler = ReminderScheduler(ReminderSettings(daily_reset_enabled=False))
        updated = ReminderSettings(daily_reset_enabled=True)
        reminder_tick(
            scheduler,
            ReminderSettings(daily_reset_enabled=False),
            lambda: updated,
            lambda message, title: None,
            {},
        )
        self.assertTrue(scheduler.settings.daily_reset_enabled)

    def test_taskbar_tick_survives_backend_failure(self):
        from src.core.taskbar import NullTaskbar, TaskbarProgress
        from src.patches.ui_extras_patch import taskbar_tick

        class BoomProgress(TaskbarProgress):
            def update(self, ratio):
                raise OSError("任务栏没了")

        class FakeExecutor:
            current_task = type("Task", (), {"name": "日常任务"})()

        run_log = _StubRunLog()
        logs = []
        state = {}
        with mock.patch("ok.og.executor", FakeExecutor(), create=True):
            taskbar_tick(
                BoomProgress(NullTaskbar()),
                run_log,
                state,
                enabled_loader=lambda: True,
                log=logs.append,
                now=0.0,
            )
        self.assertEqual(len(logs), 1)
        self.assertIn("任务栏没了", logs[0])

    def test_taskbar_tick_respects_disabled(self):
        from src.core.taskbar import NullTaskbar, TaskbarProgress
        from src.patches.ui_extras_patch import taskbar_tick

        progress = TaskbarProgress(NullTaskbar())
        progress.update(0.5)
        state = {"current": object()}
        taskbar_tick(progress, None, state, enabled_loader=lambda: False)
        self.assertIsNone(progress.last)   # 关掉后进度条被撤掉
        self.assertIsNone(state["current"])


if __name__ == "__main__":
    unittest.main()
