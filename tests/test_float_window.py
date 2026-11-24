"""悬浮窗的测试：设置夹取、位置、ETA 文案、队列行、运行时快照、窗口两态切换。

需求（用户原话）：
1. 能看任务列表；2. 能设颜色与不透明度（最低 10%）;3. 能调大小与位置；
4. 显示队列里每项大概要多久；5. 左侧有「悬浮窗」设置页；6. 圆角矩形 + 悬浮图标、可拖动。
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from src.core import float_window as fw
from src.core import run_state

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestSettings(unittest.TestCase):
    def test_defaults(self):
        settings = fw.FloatSettings()
        self.assertTrue(settings.enabled)
        self.assertGreaterEqual(settings.opacity, fw.MIN_OPACITY)
        self.assertTrue(settings.collapsed, "默认先是图标态")

    def test_opacity_floor_is_ten_percent(self):
        settings = fw.FloatSettings.from_dict({"opacity": 0.01})
        self.assertAlmostEqual(settings.opacity, 0.10)
        settings = fw.FloatSettings.from_dict({"opacity": 1.5})
        self.assertAlmostEqual(settings.opacity, 1.00)
        settings = fw.FloatSettings.from_dict({"opacity": "坏值"})
        self.assertAlmostEqual(settings.opacity, fw.DEFAULT_OPACITY)

    def test_size_and_icon_clamped(self):
        settings = fw.FloatSettings.from_dict(
            {"icon_size": 9999, "panel_width": 10, "panel_height": 9999, "corner_radius": -5}
        )
        self.assertEqual(settings.icon_size, fw.MAX_ICON_SIZE)
        self.assertEqual(settings.panel_width, fw.MIN_PANEL_WIDTH)
        self.assertEqual(settings.panel_height, fw.MAX_PANEL_HEIGHT)
        self.assertEqual(settings.corner_radius, 0)

    def test_color_normalized(self):
        self.assertEqual(fw.FloatSettings.from_dict({"bg_color": "#ABC"}).bg_color, "#aabbcc")
        self.assertEqual(fw.FloatSettings.from_dict({"bg_color": "ff8800"}).bg_color, "#ff8800")
        self.assertEqual(fw.FloatSettings.from_dict({"bg_color": "red"}).bg_color, fw.DEFAULT_BG_COLOR)

    def test_roundtrip_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fw.json"
            original = fw.FloatSettings(icon_size=80, panel_width=400, opacity=0.5, bg_color="#102030")
            fw.save_settings(original, path)
            loaded = fw.load_settings(path)
            self.assertEqual(loaded.icon_size, 80)
            self.assertEqual(loaded.panel_width, 400)
            self.assertAlmostEqual(loaded.opacity, 0.5)
            self.assertEqual(loaded.bg_color, "#102030")

    def test_broken_file_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fw.json"
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(fw.load_settings(path).icon_size, fw.DEFAULT_ICON_SIZE)

    def test_with_changes_clamps(self):
        settings = fw.FloatSettings().with_changes(icon_size=500, opacity=0.0)
        self.assertEqual(settings.icon_size, fw.MAX_ICON_SIZE)
        self.assertAlmostEqual(settings.opacity, fw.MIN_OPACITY)


class TestPosition(unittest.TestCase):
    def test_inside_screen_untouched(self):
        self.assertEqual(fw.clamp_position(100, 200, 300, 200, 1920, 1080), (100, 200))

    def test_off_screen_pulled_back(self):
        x, y = fw.clamp_position(5000, -50, 300, 200, 1920, 1080)
        self.assertLessEqual(x + 300, 1920)
        self.assertGreaterEqual(y, 0)

    def test_none_position_uses_margin(self):
        self.assertEqual(fw.clamp_position(None, None, 64, 64, 1920, 1080, margin=8), (8, 8))

    def test_smaller_screen_than_window(self):
        x, y = fw.clamp_position(10, 10, 800, 600, 640, 480)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_default_position_is_bottom_right(self):
        x, y = fw.default_position(64, 64, 1920, 1040)
        self.assertEqual(x, 1920 - 64 - fw.DEFAULT_MARGIN)
        self.assertEqual(y, 1040 - 64 - fw.DEFAULT_MARGIN - fw.NOTIFICATION_CLEARANCE)

    def test_default_position_leaves_room_for_notifications(self):
        """离底边要留出通知区高度，否则 Windows 通知弹窗会盖住悬浮图标。"""
        _x, y = fw.default_position(64, 64, 1920, 1040)
        self.assertLessEqual(y + 64, 1040 - fw.NOTIFICATION_CLEARANCE)

    def test_default_position_respects_taskbar_offset(self):
        """任务栏在左边时可用区域原点不是 (0,0)，右下角要跟着平移。"""
        x, y = fw.default_position(64, 64, 1840, 1040, offset_x=80)
        self.assertEqual(x, 80 + 1840 - 64 - fw.DEFAULT_MARGIN)
        self.assertGreaterEqual(y, 0)

    def test_default_position_on_tiny_screen(self):
        x, y = fw.default_position(340, 320, 400, 300)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + 340, 400 + 1)


class TestTexts(unittest.TestCase):
    def test_eta_format(self):
        self.assertEqual(fw.format_eta(None), fw.UNKNOWN_ETA)
        self.assertEqual(fw.format_eta(-5), fw.UNKNOWN_ETA)
        self.assertEqual(fw.format_eta(45), "约 45 秒")
        self.assertEqual(fw.format_eta(180), "约 3 分钟")
        self.assertEqual(fw.format_eta(3900), "约 1 小时 5 分")

    def test_queue_rows_limit(self):
        snapshot = {
            "items": [
                {"name": "a", "state": "完成", "eta_seconds": 60},
                {"name": "b", "state": "运行中", "eta_seconds": 120},
                {"name": "c", "state": "等待", "eta_seconds": None},
            ]
        }
        rows = fw.queue_rows(snapshot, max_tasks=2)
        self.assertEqual([row["name"] for row in rows], ["a", "b"])
        self.assertEqual(rows[1]["eta_text"], "约 2 分钟")

    def test_queue_rows_empty(self):
        self.assertEqual(fw.queue_rows(None), [])

    def test_title_text(self):
        self.assertEqual(fw.title_text({"task": "日常任务"}), "日常任务")
        self.assertIn("已暂停", fw.title_text({"task": "日常任务", "paused": True}))
        self.assertIn("空闲", fw.title_text({}))


class TestRunState(unittest.TestCase):
    def setUp(self):
        run_state.clear()

    def tearDown(self):
        run_state.clear()

    class FakeLog:
        def __init__(self, table):
            self.table = table

        def estimate_seconds(self, name):
            return self.table.get(name)

    def test_queue_snapshot_has_eta_per_item(self):
        log = self.FakeLog({"家园日常": 300, "每日委托": 600})
        run_state.set_queue("一条龙", ["家园日常", "每日委托"], run_log=log)
        snapshot = run_state.snapshot(run_log=log)
        self.assertEqual(snapshot["queue"], "一条龙")
        self.assertEqual(len(snapshot["items"]), 2)
        self.assertAlmostEqual(snapshot["remaining_seconds"], 900)
        rows = fw.queue_rows(snapshot)
        self.assertEqual(rows[0]["eta_text"], "约 5 分钟")

    def test_item_state_transitions(self):
        log = self.FakeLog({"A": 60, "B": 60})
        run_state.set_queue("q", ["A", "B"], run_log=log)
        run_state.start_item(0)
        run_state.finish_item(0, "完成")
        snapshot = run_state.snapshot(run_log=log)
        self.assertEqual(snapshot["items"][0]["state"], "完成")
        self.assertEqual(snapshot["items"][1]["state"], "等待")
        # 完成项不再计入剩余
        self.assertLessEqual(snapshot["remaining_seconds"], 60.5)

    def test_running_item_remaining_decreases(self):
        log = self.FakeLog({"A": 600})
        run_state.set_queue("q", ["A"], run_log=log)
        run_state.start_item(0)
        first = run_state.snapshot(run_log=log)["remaining_seconds"]
        later = run_state.snapshot(run_log=log, now=time.monotonic() + 120)["remaining_seconds"]
        self.assertLess(later, first)

    def test_clear_empties_snapshot(self):
        run_state.set_queue("q", ["A"], run_log=None)
        run_state.set_task("日常任务")
        run_state.clear()
        snapshot = run_state.snapshot()
        self.assertEqual(snapshot["items"], [])
        self.assertEqual(snapshot["task"], "")

    def test_unknown_estimate_is_none_not_zero(self):
        run_state.set_queue("q", ["没有历史"], run_log=self.FakeLog({}))
        snapshot = run_state.snapshot(run_log=self.FakeLog({}))
        self.assertIsNone(snapshot["items"][0]["eta_seconds"])
        self.assertIsNone(snapshot["remaining_seconds"])


class TestFloatWindowWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication

            cls.qapp = QApplication.instance() or QApplication([])
        except Exception:
            raise unittest.SkipTest("Qt 离屏不可用") from None

    def tearDown(self):
        from src.gui.float_window import set_current_window

        set_current_window(None)
        run_state.clear()

    def _window(self, **kwargs):
        from src.gui.float_window import FloatWindow

        return FloatWindow(fw.FloatSettings(**kwargs))

    def test_icon_and_panel_sizes(self):
        window = self._window(collapsed=True, icon_size=72, panel_width=360, panel_height=300)
        self.assertEqual((window.width(), window.height()), (72, 72))
        window.expand()
        self.assertFalse(window.collapsed)
        self.assertEqual((window.width(), window.height()), (360, 300))
        window.collapse()
        self.assertEqual((window.width(), window.height()), (72, 72))
        window.close()

    def test_apply_settings_updates_opacity_and_size(self):
        window = self._window()
        window.apply_settings(
            window.settings.with_changes(icon_size=96, opacity=0.3, bg_color="#112233"),
            persist=False,
        )
        self.assertEqual((window.width(), window.height()), (96, 96))
        self.assertAlmostEqual(window.windowOpacity(), 0.3, places=2)
        self.assertEqual(window.settings.bg_color, "#112233")
        window.close()

    def test_settings_persisted_on_change(self):
        window = self._window()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fw.json"
            original = fw.SETTINGS_FILE
            fw.SETTINGS_FILE = path
            try:
                window.apply_settings(window.settings.with_changes(icon_size=48))
                self.assertTrue(path.is_file())
                self.assertEqual(fw.load_settings(path).icon_size, 48)
            finally:
                fw.SETTINGS_FILE = original
        window.close()

    def test_remember_geometry_saves_position(self):
        window = self._window(collapsed=True)
        window.move(123, 234)
        window.remember_geometry()
        self.assertEqual(window.settings.icon_x, 123)
        self.assertEqual(window.settings.icon_y, 234)
        window.close()

    def test_refresh_uses_run_state(self):
        log = TestRunState.FakeLog({"A": 120})
        run_state.set_queue("q", ["A"], run_log=log)
        run_state.set_task("日常任务")
        window = self._window(collapsed=False)
        window.refresh()
        window.close()

    def test_registry(self):
        from src.gui import float_window as gui

        window = self._window()
        gui.set_current_window(window)
        self.assertIs(gui.current_window(), window)
        window.close()


if __name__ == "__main__":
    unittest.main()
