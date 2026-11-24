"""入口预热（DPI 策略）的测试与接线检查。

真实观察：headless 启动 `main_debug.py` 时 Qt 打了一行
「setHighDpiScaleFactorRoundingPolicy must be called before creating the QGuiApplication instance」——
框架在 `QApplication()` 之后才设，被 Qt 直接忽略，于是 150% 缩放下文字按默认取整，偏糊。
入口文件在最前面调用一次即可，这里锁住「入口真的调了」和「helper 本身安全」。
"""

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]


class TestEntryPointsApplyDpiPolicy(unittest.TestCase):
    def test_main_files_call_policy_before_ok(self):
        for name in ("main.py", "main_debug.py"):
            with self.subTest(file=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("apply_high_dpi_policy()", source)
                # 必须在 ok.OK(...)/ok.start() 之前调用，否则 QApplication 已创建
                self.assertLess(
                    source.index("apply_high_dpi_policy()"),
                    source.index("ok.start()"),
                    f"{name}: 调用位置太晚",
                )

    def test_policy_helper_is_safe_when_app_exists(self):
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        from src.patches.dpi_policy import apply_high_dpi_policy

        # 已经有 QApplication 时应返回 False（改不了），但绝不能抛
        self.assertFalse(apply_high_dpi_policy())

    def test_policy_helper_ok_before_app(self):
        """新进程里（还没有 QApplication）应该设置成功，并且 Qt 真的采纳（PassThrough）。"""
        import subprocess
        import sys

        script = (
            "import os; os.environ.setdefault('QT_QPA_PLATFORM','offscreen');"
            "from src.patches.dpi_policy import apply_high_dpi_policy;"
            "print(apply_high_dpi_policy());"
            "from PySide6.QtWidgets import QApplication;"
            "app = QApplication([]);"
            "from PySide6.QtGui import QGuiApplication;"
            "print(QGuiApplication.highDpiScaleFactorRoundingPolicy())"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[-2], "True")
        self.assertIn("PassThrough", lines[-1])


if __name__ == "__main__":
    unittest.main()
