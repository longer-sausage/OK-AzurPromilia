"""运行期策略配置测试：默认值、类型兜底、低配模式缩放。"""

import unittest

from src.core.global_config_store import (
    GLOBAL_CONFIG_OPTIONS,
    RUNTIME_CONFIG_NAME,
    get_all_visible_configs,
    get_runtime_settings,
)
from src.core.runtime_config import (
    AFTER_ACTION_WAIT_KEY,
    AUTO_RECOVER_KEY,
    FAILURE_FRAME_LIMIT_KEY,
    LOW_SPEC_KEY,
    LOW_SPEC_TIMEOUT_SCALE,
    MAX_RECOVER_KEY,
    RETRY_INTERVAL_KEY,
    STEP_TIMEOUT_KEY,
    STUCK_THRESHOLD_KEY,
    RuntimeSettings,
    build_default_config,
    load_runtime_settings,
    with_override,
)
from src.core.runtime_config import (
    RUNTIME_CONFIG_NAME as NAME_IN_MODULE,
)


class TestRuntimeDefaults(unittest.TestCase):
    def test_defaults_keep_previous_behaviour(self):
        """默认值必须与改动前一致：单步超时 10 秒、无额外等待。"""
        settings = load_runtime_settings(build_default_config())
        self.assertAlmostEqual(settings.step_timeout, 10.0)
        self.assertAlmostEqual(settings.retry_interval, 0.0)
        self.assertAlmostEqual(settings.after_action_wait, 0.0)
        self.assertAlmostEqual(settings.stuck_threshold, 30.0)
        self.assertFalse(settings.low_spec)
        self.assertTrue(settings.auto_recover)
        self.assertEqual(settings.max_recover_attempts, 3)
        self.assertEqual(settings.failure_frame_limit, 20)

    def test_none_returns_defaults(self):
        self.assertEqual(load_runtime_settings(None), RuntimeSettings())

    def test_config_option_registered_and_grouped(self):
        names = [option.name for option in GLOBAL_CONFIG_OPTIONS]
        self.assertIn(RUNTIME_CONFIG_NAME, names)
        self.assertEqual(RUNTIME_CONFIG_NAME, NAME_IN_MODULE)
        visible = {name for name, _config, _option in get_all_visible_configs()}
        self.assertIn(RUNTIME_CONFIG_NAME, visible)

    def test_live_snapshot_reads_global_config(self):
        settings = get_runtime_settings()
        self.assertGreater(settings.step_timeout, 0)
        self.assertGreater(settings.stuck_threshold, 0)


class TestRuntimeCoercion(unittest.TestCase):
    def test_non_numeric_falls_back(self):
        settings = load_runtime_settings({STEP_TIMEOUT_KEY: "很快", STUCK_THRESHOLD_KEY: None})
        self.assertAlmostEqual(settings.step_timeout, 10.0)
        self.assertAlmostEqual(settings.stuck_threshold, 30.0)

    def test_numeric_strings_are_accepted(self):
        settings = load_runtime_settings({STEP_TIMEOUT_KEY: "7.5", RETRY_INTERVAL_KEY: "0.25"})
        self.assertAlmostEqual(settings.step_timeout, 7.5)
        self.assertAlmostEqual(settings.retry_interval, 0.25)

    def test_zero_or_negative_step_timeout_falls_back(self):
        self.assertAlmostEqual(load_runtime_settings({STEP_TIMEOUT_KEY: 0}).step_timeout, 10.0)
        self.assertAlmostEqual(load_runtime_settings({STEP_TIMEOUT_KEY: -5}).step_timeout, 10.0)

    def test_negative_delays_clamped_to_zero(self):
        settings = load_runtime_settings({RETRY_INTERVAL_KEY: -1, AFTER_ACTION_WAIT_KEY: -2})
        self.assertAlmostEqual(settings.retry_interval, 0.0)
        self.assertAlmostEqual(settings.after_action_wait, 0.0)

    def test_bool_parsing(self):
        for raw, expected in (
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("true", True),
            ("off", False),
            ("是", True),
            ("关", False),
            ("看不懂", True),  # 非法字符串退回默认（默认 True）
        ):
            with self.subTest(raw=raw):
                self.assertEqual(load_runtime_settings({AUTO_RECOVER_KEY: raw}).auto_recover, expected)

    def test_low_spec_flag_parsing(self):
        self.assertTrue(load_runtime_settings({LOW_SPEC_KEY: "1"}).low_spec)
        self.assertFalse(load_runtime_settings({LOW_SPEC_KEY: "0"}).low_spec)

    def test_recover_attempts_and_frame_limit_are_bounded(self):
        settings = load_runtime_settings({MAX_RECOVER_KEY: -3, FAILURE_FRAME_LIMIT_KEY: 0})
        self.assertEqual(settings.max_recover_attempts, 0)
        self.assertEqual(settings.failure_frame_limit, 1)


class TestRuntimeScaling(unittest.TestCase):
    def test_normal_mode_keeps_timeout(self):
        settings = RuntimeSettings()
        self.assertAlmostEqual(settings.resolve_step_timeout(None), 10.0)
        self.assertAlmostEqual(settings.resolve_step_timeout(3.0), 3.0)

    def test_low_spec_scales_timeout(self):
        settings = RuntimeSettings(low_spec=True)
        self.assertAlmostEqual(settings.resolve_step_timeout(None), 10.0 * LOW_SPEC_TIMEOUT_SCALE)
        self.assertAlmostEqual(settings.resolve_step_timeout(2.0), 2.0 * LOW_SPEC_TIMEOUT_SCALE)

    def test_low_spec_scales_stuck_threshold(self):
        settings = RuntimeSettings(low_spec=True, stuck_threshold=40.0)
        self.assertAlmostEqual(settings.resolve_stuck_threshold(), 40.0 * LOW_SPEC_TIMEOUT_SCALE)

    def test_zero_timeout_means_unlimited_and_is_not_scaled(self):
        settings = RuntimeSettings(low_spec=True)
        self.assertEqual(settings.resolve_step_timeout(0), 0)

    def test_with_override_does_not_mutate_original(self):
        base = RuntimeSettings()
        patched = with_override(base, low_spec=True, step_timeout=1.0)
        self.assertFalse(base.low_spec)
        self.assertAlmostEqual(base.step_timeout, 10.0)
        self.assertTrue(patched.low_spec)


class TestRetryGap(unittest.TestCase):
    """``重试间隔(秒)`` 必须真的被 ``wait_until`` 用上（之前是死配置）。"""

    def test_default_is_zero(self):
        self.assertEqual(RuntimeSettings().resolve_retry_gap(), 0.0)

    def test_explicit_check_delay_wins_when_larger(self):
        self.assertAlmostEqual(RuntimeSettings().resolve_retry_gap(0.3), 0.3)

    def test_config_value_used_when_no_check_delay(self):
        settings = with_override(RuntimeSettings(), retry_interval=0.7)
        self.assertAlmostEqual(settings.resolve_retry_gap(), 0.7)

    def test_larger_of_the_two_wins(self):
        settings = with_override(RuntimeSettings(), retry_interval=0.7)
        self.assertAlmostEqual(settings.resolve_retry_gap(0.2), 0.7)
        self.assertAlmostEqual(settings.resolve_retry_gap(1.2), 1.2)

    def test_bad_values_fall_back_to_zero(self):
        settings = with_override(RuntimeSettings(), retry_interval=-3.0)
        self.assertEqual(settings.resolve_retry_gap(), 0.0)
        self.assertEqual(settings.resolve_retry_gap("oops"), 0.0)


class TestWaitUntilUsesRetryGap(unittest.TestCase):
    """接线测试：``wait_until`` 真的把重试间隔用上了（不是只有个 resolver）。"""

    class Harness:
        def __init__(self, settings):
            import threading
            import types

            self.runtime = settings
            self.slept: list[float] = []
            self.now = 0.0
            self.executor = types.SimpleNamespace(
                reset_scene=lambda: None,
                exit_event=threading.Event(),
                wait_scene_timeout=1.0,
                wait_until_settle_time=0,
            )

        def next_frame(self):
            return None

        def active_time(self):
            self.now += 0.05
            return self.now

        def sleep(self, seconds):
            self.slept.append(round(float(seconds), 3))
            self.now += float(seconds)

        def log_info(self, *args, **kwargs):
            return None

    def _run(self, settings, check_delay=None):
        from unittest import mock

        from src.core.base_game_task import BaseGameTask

        harness = self.Harness(settings)
        patch = {"wait_until_check_delay": check_delay} if check_delay is not None else {}
        with mock.patch.dict("src.core.base_game_task.app_config", patch, clear=False):
            BaseGameTask.wait_until(harness, lambda: False, time_out=0.3)
        return harness.slept

    def test_retry_interval_is_slept(self):
        slept = self._run(with_override(RuntimeSettings(), retry_interval=0.1))
        self.assertTrue(slept, "配置了重试间隔却完全没 sleep")
        self.assertAlmostEqual(slept[0], 0.1)

    def test_default_does_not_sleep_between_checks(self):
        self.assertEqual(self._run(RuntimeSettings()), [])

    def test_explicit_check_delay_still_wins(self):
        slept = self._run(with_override(RuntimeSettings(), retry_interval=0.1), check_delay=0.25)
        self.assertAlmostEqual(slept[0], 0.25)


class TestWaitUntilTimeoutWiring(unittest.TestCase):
    """``wait_until`` 的超时也要吃全局策略（之前只认框架写死的 wait_scene_timeout）。"""

    class Harness:
        def __init__(self, settings, scene_timeout=10.0):
            import threading
            import types

            self.runtime = settings
            self.now = 0.0
            self.frames = 0
            self.executor = types.SimpleNamespace(
                reset_scene=lambda: None,
                exit_event=threading.Event(),
                wait_scene_timeout=scene_timeout,
                wait_until_settle_time=0,
            )

        def next_frame(self):
            self.frames += 1
            self.now += 0.5
            return None

        def active_time(self):
            return self.now

        def sleep(self, seconds):
            self.now += float(seconds)

        def log_info(self, *args, **kwargs):
            return None

    def _frames(self, settings, time_out=0, scene_timeout=10.0):
        from unittest import mock

        from src.core.base_game_task import BaseGameTask

        harness = self.Harness(settings, scene_timeout)
        with mock.patch.dict("src.core.base_game_task.app_config", {}, clear=False):
            BaseGameTask.wait_until(harness, lambda: False, time_out=time_out)
        return harness.frames

    def test_default_uses_framework_timeout(self):
        """没改配置时保持框架默认行为（10 秒）。"""
        self.assertGreater(self._frames(RuntimeSettings()), 10)

    def test_custom_step_timeout_overrides_framework(self):
        short = self._frames(with_override(RuntimeSettings(), step_timeout=1.0))
        self.assertLessEqual(short, 6)

    def test_low_spec_doubles_the_effective_timeout(self):
        normal = self._frames(RuntimeSettings(), scene_timeout=2.0)
        slow = self._frames(with_override(RuntimeSettings(), low_spec=True), scene_timeout=2.0)
        self.assertGreater(slow, normal)

    def test_explicit_timeout_is_scaled_by_low_spec(self):
        normal = self._frames(RuntimeSettings(), time_out=2.0)
        slow = self._frames(with_override(RuntimeSettings(), low_spec=True), time_out=2.0)
        self.assertGreater(slow, normal)


if __name__ == "__main__":
    unittest.main()
