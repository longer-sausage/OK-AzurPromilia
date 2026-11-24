"""补丁接线体检：16 个启动补丁是否都能装上、装上两次是否安全。

为什么用**子进程**跑：这些补丁会改框架类的行为（`MainWindow.__init__`、`Task.click`、
OCR 流程…），在本进程里装一遍会影响其它测试的结果，还会让测试之间产生顺序依赖。
子进程里装完就退出，干净。

这条体检能抓到的问题：补丁文件名/函数名对不上（启动时静默跳过）、
新框架版本里某个属性没了导致 `install_*` 抛异常、安装不幂等（重复调用会套两层包装）。
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATCHES = [
    ("src.patches.crash_guard_patch", "install_crash_guard_patch"),
    ("src.patches.cascade_dropdown_patch", "install_cascade_dropdown_patch"),
    ("src.patches.capture_timestamp_patch", "install_capture_timestamp_patch"),
    ("src.patches.close_behavior_patch", "install_close_behavior_patch"),
    ("src.patches.i18n_collection_patch", "install_i18n_collection_patch"),
    ("src.patches.launch_args_patch", "install_launch_args_patch"),
    ("src.patches.qfluent_mute_promo_patch", "install_mute_promo_patch"),
    ("src.patches.nav_layout_patch", "install_nav_layout_patch"),
    ("src.patches.no_frame_task_patch", "install_no_frame_task_patch"),
    ("src.patches.ocr_text_fix_patch", "install_ocr_text_fix_patch"),
    ("src.patches.process_execute_patch", "install_process_execute_patch"),
    ("src.patches.qfluent_navigation_patch", "install_qfluent_navigation_patch"),
    ("src.patches.screenshot_sidecar_patch", "install_screenshot_sidecar_patch"),
    ("src.patches.startup_window_patch", "install_startup_window_patch"),
    ("src.patches.task_config_lock_patch", "install_task_config_lock_patch"),
    ("src.patches.ui_extras_patch", "install_ui_extras_patch"),
    ("src.patches.win32_gdi_point_patch", "install_win32_gdi_point_patch"),
]

SCRIPT = """
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib
from PySide6.QtWidgets import QApplication
QApplication.instance() or QApplication([])

problems = []
for module_name, func_name in {patches!r}:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        problems.append(f"{{func_name}}: 导入失败 {{type(exc).__name__}}: {{exc}}")
        continue
    func = getattr(module, func_name, None)
    if func is None:
        problems.append(f"{{func_name}}: 函数不存在")
        continue
    for attempt in (1, 2):
        try:
            func()
        except Exception as exc:
            problems.append(f"{{func_name}}: 第 {{attempt}} 次安装抛 {{type(exc).__name__}}: {{exc}}")
            break

print("PROBLEMS:" + repr(problems))
print("OK" if not problems else "FAILED")
"""


class TestPatchesInstall(unittest.TestCase):
    def _run(self, patches):
        script = SCRIPT.format(patches=patches)
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
        )

    def test_all_patches_install_twice_without_error(self):
        result = self._run(PATCHES)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip().splitlines()[-1], "OK", result.stdout[-2000:])

    def test_detects_a_broken_patch(self):
        """正控：故意塞一个不存在的补丁，确认体检真的会报错（不然测试永远绿）。"""
        result = self._run([("src.patches.no_such_patch", "install_nope")])
        self.assertEqual(result.stdout.strip().splitlines()[-1], "FAILED")
        self.assertIn("导入失败", result.stdout)

    def test_startup_order_lists_every_patch(self):
        """`install_startup_patches()` 必须把上面这些补丁都装上（别漏接）。"""
        source = (ROOT / "src" / "patches" / "startup_patches.py").read_text(encoding="utf-8")
        missing = [
            func_name
            for _module, func_name in PATCHES
            if f"{func_name}()" not in source
        ]
        self.assertEqual(missing, [], f"这些补丁没有在 install_startup_patches 里调用: {missing}")


if __name__ == "__main__":
    unittest.main()
