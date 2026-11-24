"""崩溃诊断接线的测试：补丁装上钩子后真的会落盘，并且能关掉。

``core/crash_guard`` 本身的行为在 ``test_privacy_and_diagnostics.py`` 里测过；
这里测的是**启动路径到底有没有把它接上**（之前就是因为没接，真崩了什么都没有）。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.patches import crash_guard_patch


class TestCrashGuardPatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        guard = crash_guard_patch.installed_guard()
        if guard is not None:
            guard.close()
        crash_guard_patch._guard = None
        self._tmp.cleanup()

    def test_install_and_record(self):
        self.assertTrue(crash_guard_patch.install_crash_guard_patch(self.root))
        guard = crash_guard_patch.installed_guard()
        self.assertIsNotNone(guard)
        self.assertTrue(guard.installed)

        guard.record(RuntimeError, RuntimeError("接线测试"), None)
        files = list((self.root / "crashes").glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertIn("接线测试", files[0].read_text(encoding="utf-8"))

    def test_idempotent(self):
        self.assertTrue(crash_guard_patch.install_crash_guard_patch(self.root))
        first = crash_guard_patch.installed_guard()
        self.assertTrue(crash_guard_patch.install_crash_guard_patch(self.root))
        self.assertIs(crash_guard_patch.installed_guard(), first)

    def test_disabled_by_env(self):
        with mock.patch.dict(os.environ, {crash_guard_patch.DISABLE_ENV: "1"}):
            self.assertFalse(crash_guard_patch.install_crash_guard_patch(self.root))
        self.assertIsNone(crash_guard_patch.installed_guard())

    def test_install_failure_is_swallowed(self):
        with mock.patch("src.core.crash_guard.CrashGuard.install", side_effect=OSError("盘满了")):
            self.assertFalse(crash_guard_patch.install_crash_guard_patch(self.root))
        self.assertIsNone(crash_guard_patch.installed_guard())

    def test_startup_patches_install_guard_first(self):
        """补丁顺序：崩溃诊断必须在最前面（别的补丁炸了也要留下记录）。"""
        import inspect

        from src.patches import startup_patches

        source = inspect.getsource(startup_patches.install_startup_patches)
        calls = [line.strip() for line in source.splitlines() if line.strip().startswith("install_")]
        self.assertTrue(calls[0].startswith("install_crash_guard_patch"), calls[:3])


if __name__ == "__main__":
    unittest.main()
