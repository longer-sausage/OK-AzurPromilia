"""「悬浮窗」设置页的交互测试。

真实崩溃：点「选择颜色」→ ``AttributeError: type object 'ColorDialog' has no attribute 'getColor'``
（qfluentwidgets 的 ColorDialog 没有静态 getColor）。页面构建能过、静态检查也发现不了，
只有**真的点一次**才炸——所以这里把每个按钮都点一遍。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() or QApplication([])
    except Exception:
        return None


class TestFloatWindowTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _app() is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original = None
        from src.core import float_window as fw

        self.fw = fw
        self._original = fw.SETTINGS_FILE
        fw.SETTINGS_FILE = Path(self._tmp.name) / "float_window.json"

    def tearDown(self):
        if self._original is not None:
            self.fw.SETTINGS_FILE = self._original
        self._tmp.cleanup()

    def _tab(self):
        from src.gui.float_window_tab import FloatWindowTab

        tab = FloatWindowTab()
        tab._build()
        tab.reload()
        return tab

    def test_pick_color_applies(self):
        from PySide6.QtGui import QColor

        tab = self._tab()
        with mock.patch(
            "src.gui.float_window_tab.QColorDialog.getColor", return_value=QColor("#102030")
        ):
            tab._pick_color()
        self.assertEqual(tab.color_label.text(), "#102030")

    def test_pick_color_cancelled_keeps_old(self):
        from PySide6.QtGui import QColor

        tab = self._tab()
        before = tab.color_label.text()
        with mock.patch(
            "src.gui.float_window_tab.QColorDialog.getColor", return_value=QColor()
        ):
            tab._pick_color()
        self.assertEqual(tab.color_label.text(), before)

    def test_save_writes_settings_and_applies_live(self):
        tab = self._tab()
        tab.icon_size_box.setValue(96)
        tab.opacity_slider.setValue(40)
        with mock.patch(
            "src.gui.float_window_tab.apply_float_settings", return_value=True
        ) as applied:
            tab._save()
        saved = self.fw.load_settings(self.fw.SETTINGS_FILE)
        self.assertEqual(saved.icon_size, 96)
        self.assertAlmostEqual(saved.opacity, 0.40)
        applied.assert_called_once()
        self.assertIn("已保存", tab.status.text())

    def test_opacity_slider_never_goes_below_ten_percent(self):
        tab = self._tab()
        tab.opacity_slider.setValue(0)
        self.assertGreaterEqual(tab.opacity_slider.value(), 10)
        tab.opacity_slider.setValue(0)
        tab._save()
        saved = self.fw.load_settings(self.fw.SETTINGS_FILE)
        self.assertGreaterEqual(saved.opacity, 0.10)

    def test_reset_position_clears_coordinates(self):
        tab = self._tab()
        self.fw.save_settings(
            self.fw.FloatSettings(icon_x=500, icon_y=400, panel_x=100, panel_y=100)
        )
        tab.reload()
        with mock.patch("src.gui.float_window_tab.apply_float_settings", return_value=True):
            tab._reset_position()
        saved = self.fw.load_settings(self.fw.SETTINGS_FILE)
        self.assertIsNone(saved.icon_x)
        self.assertIsNone(saved.icon_y)
        self.assertIsNone(saved.panel_x)
        self.assertTrue(saved.collapsed)

    def test_reload_reflects_saved_settings(self):
        tab = self._tab()
        self.fw.save_settings(
            self.fw.FloatSettings(icon_size=120, opacity=0.5, bg_color="#abcdef", show_queue=False)
        )
        tab.reload()
        self.assertEqual(tab.icon_size_box.value(), 120)
        self.assertEqual(tab.opacity_slider.value(), 50)
        self.assertEqual(tab.color_label.text(), "#abcdef")
        self.assertFalse(tab.show_queue_box.isChecked())


class TestApplyFloatSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _app() is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def test_applies_to_current_window(self):
        from src.core import float_window as fw
        from src.gui import float_window as gui
        from src.gui.float_window_tab import apply_float_settings

        window = gui.FloatWindow(fw.FloatSettings(collapsed=True, icon_size=64))
        gui.set_current_window(window)
        try:
            self.assertTrue(apply_float_settings(window.settings.with_changes(icon_size=80)))
            self.assertEqual(window.width(), 80)
        finally:
            gui.set_current_window(None)
            window.close()

    def test_no_window_is_graceful(self):
        from src.gui import float_window as gui
        from src.gui.float_window_tab import apply_float_settings

        gui.set_current_window(None)
        self.assertFalse(apply_float_settings(gui.fw.FloatSettings()))


if __name__ == "__main__":
    unittest.main()
