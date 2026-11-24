"""模板热更新的安全刷新测试。

重点不是「能不能调 process_data」，而是：
- 必须在 ``feature_set.lock`` 里调（否则会和 ``ensure_feature`` 抢同一个缓存）；
- 任务在跑时要明确告警；
- 任何异常都不能把安装流程带崩。
"""

import threading
import unittest

from src.core.feature_reload import BUSY_WARNING, is_busy, reload_features


class FakeFeatureSet:
    def __init__(self, ok=True, raise_error=None):
        self.lock = threading.Lock()
        self.ok = ok
        self.raise_error = raise_error
        self.calls = 0
        self.locked_during_call = None

    def process_data(self):
        self.calls += 1
        self.locked_during_call = self.lock.locked()
        if self.raise_error is not None:
            raise self.raise_error
        return self.ok


class FakeExecutor:
    def __init__(self, feature_set=None, task=None):
        self.feature_set = feature_set
        self.current_task = task


class FakeTask:
    def __init__(self, disabled=False):
        self.disabled = disabled


class TestReloadFeatures(unittest.TestCase):
    def test_reload_holds_the_lock(self):
        feature_set = FakeFeatureSet()
        self.assertTrue(reload_features(FakeExecutor(feature_set)))
        self.assertEqual(feature_set.calls, 1)
        self.assertTrue(feature_set.locked_during_call, "重载没有在 feature_set.lock 里执行")

    def test_returns_load_success_flag(self):
        self.assertFalse(reload_features(FakeExecutor(FakeFeatureSet(ok=False))))

    def test_no_executor_or_feature_set(self):
        self.assertFalse(reload_features(None))
        self.assertFalse(reload_features(FakeExecutor(None)))

    def test_exception_is_swallowed_and_logged(self):
        logs = []
        feature_set = FakeFeatureSet(raise_error=RuntimeError("坏了"))
        self.assertFalse(reload_features(FakeExecutor(feature_set), log=logs.append))
        self.assertTrue(any("坏了" in log for log in logs))

    def test_failed_load_is_logged(self):
        logs = []
        reload_features(FakeExecutor(FakeFeatureSet(ok=False)), log=logs.append)
        self.assertTrue(any("加载失败" in log for log in logs))

    def test_busy_warning_when_task_running(self):
        logs = []
        reload_features(FakeExecutor(FakeFeatureSet(), FakeTask()), log=logs.append)
        self.assertIn(BUSY_WARNING, logs)

    def test_no_warning_when_idle_or_disabled(self):
        for task in (None, FakeTask(disabled=True)):
            logs = []
            reload_features(FakeExecutor(FakeFeatureSet(), task), log=logs.append)
            self.assertEqual(logs, [])

    def test_warning_can_be_disabled(self):
        logs = []
        reload_features(FakeExecutor(FakeFeatureSet(), FakeTask()), log=logs.append, warn_when_busy=False)
        self.assertEqual(logs, [])

    def test_lock_is_actually_free_after_reload(self):
        feature_set = FakeFeatureSet()
        reload_features(FakeExecutor(feature_set))
        self.assertFalse(feature_set.lock.locked())

    def test_is_busy(self):
        self.assertFalse(is_busy(None))
        self.assertFalse(is_busy(FakeExecutor(None, None)))
        self.assertTrue(is_busy(FakeExecutor(None, FakeTask())))
        self.assertFalse(is_busy(FakeExecutor(None, FakeTask(disabled=True))))

    def test_feature_set_without_lock_still_works(self):
        class NoLock:
            def __init__(self):
                self.calls = 0

            def process_data(self):
                self.calls += 1
                return True

        feature_set = NoLock()
        self.assertTrue(reload_features(FakeExecutor(feature_set)))
        self.assertEqual(feature_set.calls, 1)


if __name__ == "__main__":
    unittest.main()
