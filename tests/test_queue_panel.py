"""队列悬浮窗的窗口行为测试：前台隐藏、后台可选显示、拖动保存、设置读写。

用 Qt 的 offscreen 平台跑：不需要显示器，也不需要真的开游戏。
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


class TestPanelSettings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "queue_panel.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults(self):
        from src.gui import queue_panel as qp

        settings = qp.PanelSettings()
        self.assertTrue(settings.enabled)
        self.assertTrue(settings.show_in_background)
        self.assertEqual(settings.icon_path, qp.DEFAULT_ICON_PATH)

    def test_roundtrip(self):
        from src.gui import queue_panel as qp

        settings = qp.PanelSettings(
            enabled=False, show_in_background=False, opacity=0.5, width=400, height=260,
            x=30, y=40, icon_path="icons/other.png", corner_radius=6, max_rows=3,
        )
        qp.save_settings(settings, self.path)
        self.assertEqual(qp.load_settings(self.path), settings)

    def test_bad_values_clamped(self):
        from src.gui import queue_panel as qp

        self.path.write_text('{"opacity": 5, "width": 1, "height": 9999, "max_rows": 0}', encoding="utf-8")
        settings = qp.load_settings(self.path)
        self.assertAlmostEqual(settings.opacity, qp.MAX_OPACITY)
        self.assertEqual(settings.width, qp.MIN_WIDTH)
        self.assertEqual(settings.height, qp.MAX_HEIGHT)
        self.assertEqual(settings.max_rows, 1)

    def test_corrupt_file_falls_back(self):
        from src.gui import queue_panel as qp

        self.path.write_text("{不是 json", encoding="utf-8")
        self.assertEqual(qp.load_settings(self.path), qp.PanelSettings())

    def test_position_is_clamped_on_screen(self):
        from src.gui import queue_panel as qp

        x, y = qp.clamp_position(9999, 9999, 300, 200, 1920, 1080)
        self.assertLessEqual(x + 300, 1920)
        self.assertLessEqual(y + 200, 1080)

    def test_default_position_is_on_screen(self):
        from src.gui import queue_panel as qp

        x, y = qp.default_position(320, 200, 1920, 1080)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)


class TestQueueSnapshot(unittest.TestCase):
    def test_reads_executor_queue(self):
        from src.gui import queue_panel as qp

        class Task:
            def __init__(self, name, running=False):
                self.name = name
                self.running = running

        class Executor:
            lock = None

            def __init__(self):
                self.current_task = Task("日常", running=True)
                self.onetime_task_queue = [Task("收菜"), Task("委托")]

        with mock.patch("ok.og.executor", Executor(), create=True):
            current, queued = qp.queue_snapshot()
        self.assertEqual(current, "日常")
        self.assertEqual(queued, ["收菜", "委托"])

    def test_no_executor(self):
        from src.gui import queue_panel as qp

        with mock.patch("ok.og.executor", None, create=True):
            self.assertEqual(qp.queue_snapshot(), (None, []))


class TestPanelForms(unittest.TestCase):
    """两种形态：单个图标 ↔ 队列面板，以及「图标不能被任务栏盖住」。"""

    @classmethod
    def setUpClass(cls):
        if _app() is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def _panel(self, **kwargs):
        from src.gui import queue_panel as qp

        settings = qp.PanelSettings(**{"icon_size": 64, "width": 320, "height": 200, **kwargs})
        panel = qp.QueuePanel(settings, hwnd_provider=lambda: 4242)
        panel._timer.stop()
        return panel

    def test_default_form_is_icon(self):
        from src.gui import queue_panel as qp

        self.assertTrue(qp.PanelSettings().collapsed)

    def test_icon_form_is_square_icon_size(self):
        panel = self._panel(collapsed=True, icon_size=72)
        try:
            self.assertEqual((panel.width(), panel.height()), (72, 72))
        finally:
            panel.close()

    def test_panel_form_uses_panel_size(self):
        panel = self._panel(collapsed=False, width=340, height=220)
        try:
            self.assertEqual((panel.width(), panel.height()), (340, 220))
        finally:
            panel.close()

    def test_click_expands_icon_form(self):
        panel = self._panel(collapsed=True)
        try:
            panel._on_click()
            self.assertFalse(panel.collapsed)
            self.assertEqual((panel.width(), panel.height()), (320, 200))
        finally:
            panel.close()

    def test_click_in_panel_form_does_nothing(self):
        panel = self._panel(collapsed=False)
        try:
            panel._on_click()
            self.assertFalse(panel.collapsed)
        finally:
            panel.close()

    def test_double_click_collapses_panel_form(self):
        panel = self._panel(collapsed=False)
        try:
            panel._on_double_click()
            self.assertTrue(panel.collapsed)
            self.assertEqual(panel.width(), 64)
        finally:
            panel.close()

    def _qtest(self):
        """``PySide6.QtTest`` 只在装了 pyside6-addons 的环境里有；没有就跳过这个用例。"""
        try:
            from PySide6.QtTest import QTest
        except Exception:
            self.skipTest("PySide6.QtTest 不可用")

        return QTest

    def test_real_double_click_sequence_ends_in_icon_form(self):
        """用 Qt 真实事件序列（press/release/dblclick/release）走一遍，防止只测到辅助函数。"""
        from PySide6.QtCore import QPoint, Qt

        QTest = self._qtest()

        panel = self._panel(collapsed=False)
        try:
            panel._expanded_at = 0.0
            QTest.mouseDClick(panel, Qt.LeftButton, pos=QPoint(panel.width() // 2, 20))
            self.assertTrue(panel.collapsed, "真实双击之后应该停在图标态")
            panel._handle_release(False, False)  # 迟到的松手也不该弹回
            self.assertTrue(panel.collapsed)
        finally:
            panel.close()

    def test_real_single_click_expands_icon(self):
        from PySide6.QtCore import QPoint, Qt

        QTest = self._qtest()

        panel = self._panel(collapsed=True)
        try:
            QTest.mouseClick(panel, Qt.LeftButton, pos=QPoint(panel.width() // 2, panel.height() // 2))
            self.assertFalse(panel.collapsed, "单击图标应该展开面板")
        finally:
            panel.close()

    def test_release_after_double_click_does_not_expand_again(self):
        """真实 bug：Qt 双击序列里 dblclick 后面还有一次 release，不能当成单击。"""
        panel = self._panel(collapsed=False)
        try:
            panel._expanded_at = 0.0
            panel._on_double_click()             # 双击 → 收回图标
            self.assertTrue(panel.collapsed)
            panel._handle_release(False, False)  # 双击后紧跟的那次松手
            self.assertTrue(panel.collapsed, "双击后又被那次 release 展开回去了")
        finally:
            panel.close()

    def test_stray_click_right_after_collapse_is_ignored(self):
        """即使真飘来一次单击，刚收回的 300ms 内也不许立刻展开。"""
        panel = self._panel(collapsed=False)
        try:
            panel._expanded_at = 0.0
            panel._on_double_click()
            self.assertTrue(panel.collapsed)
            panel._suppress_next_release = False  # 模拟「吞标记丢了」的最坏情况
            panel._on_click()
            self.assertTrue(panel.collapsed)
            panel._collapsed_at = 0.0             # 过了防抖窗口就能正常单击展开
            panel._on_click()
            self.assertFalse(panel.collapsed)
        finally:
            panel.close()

    def test_double_click_right_after_expand_is_ignored(self):
        """手快：单击展开后紧跟的双击（< 300ms）不该把面板又收回去。"""
        panel = self._panel(collapsed=True)
        try:
            panel._on_click()
            self.assertFalse(panel.collapsed)
            panel._on_double_click()
            self.assertFalse(panel.collapsed, "刚展开就被双击收回去了")
        finally:
            panel.close()

    def test_double_click_later_does_collapse(self):

        panel = self._panel(collapsed=False)
        try:
            panel._expanded_at = 0.0  # 早就展开了
            panel._on_double_click()
            self.assertTrue(panel.collapsed)
        finally:
            panel.close()

    def test_double_click_in_icon_form_does_nothing(self):
        panel = self._panel(collapsed=True)
        try:
            panel._on_double_click()
            self.assertTrue(panel.collapsed)
        finally:
            panel.close()

    def test_icon_drag_does_not_clobber_panel_size(self):
        """图标态拖动只该改图标位置，不能把面板宽高写成图标尺寸。"""
        from src.gui import queue_panel as qp

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue_panel.json"
            original = qp.SETTINGS_FILE
            qp.SETTINGS_FILE = path
            try:
                panel = self._panel(collapsed=True, width=320, height=200, icon_size=64)
                try:
                    panel.move(90, 80)
                    panel.remember_geometry()
                    saved = qp.load_settings(path)
                    self.assertEqual((saved.icon_x, saved.icon_y), (90, 80))
                    self.assertEqual((saved.width, saved.height), (320, 200))
                finally:
                    panel.close()
            finally:
                qp.SETTINGS_FILE = original

    def test_default_position_is_bottom_right(self):
        from src.gui import queue_panel as qp

        x, y = qp.default_position(320, 200, 1920, 1040, margin=16)
        self.assertEqual(x, 1920 - 320 - 16)
        self.assertEqual(y, 1040 - 200 - 16)
        # 右下角仍然整个在工作区里（不会被任务栏压住）
        self.assertLessEqual(y + 200, 1040)

    def test_panel_starts_bottom_right_when_no_saved_position(self):
        from src.gui import queue_panel as qp

        panel = self._panel(collapsed=False, x=None, y=None)
        try:
            panel._available_rect = lambda: (0, 0, 1920, 1040)
            panel._apply_geometry()
            self.assertEqual(panel.pos().x(), 1920 - panel.width() - qp.DEFAULT_MARGIN)
            self.assertEqual(panel.pos().y(), 1040 - panel.height() - qp.DEFAULT_MARGIN)
        finally:
            panel.close()

    def test_expand_centers_panel_on_icon(self):
        """单击图标：面板以图标中心生成。"""
        panel = self._panel(collapsed=True, icon_size=64, width=320, height=200)
        try:
            panel._available_rect = lambda: (0, 0, 1920, 1040)
            panel.move(900, 500)  # 图标中心 = (932, 532)
            panel._on_click()
            self.assertFalse(panel.collapsed)
            self.assertEqual(panel.pos().x(), 932 - 320 // 2)
            self.assertEqual(panel.pos().y(), 532 - 200 // 2)
        finally:
            panel.close()

    def test_expand_near_edge_stays_inside_screen(self):
        """靠近右下角时，面板「尽量居中」但必须留在工作区内。"""
        panel = self._panel(collapsed=True, icon_size=64, width=320, height=200)
        try:
            panel._available_rect = lambda: (0, 0, 1920, 1040)
            panel.move(1920 - 64, 1040 - 64)  # 图标贴在右下角
            panel._on_click()
            self.assertEqual(panel.pos().x() + panel.width(), 1920)
            self.assertEqual(panel.pos().y() + panel.height(), 1040)
        finally:
            panel.close()

    def test_expand_near_left_top_edge_stays_inside_screen(self):
        panel = self._panel(collapsed=True, icon_size=64, width=320, height=200)
        try:
            panel._available_rect = lambda: (0, 0, 1920, 1040)
            panel.move(0, 0)
            panel._on_click()
            self.assertEqual((panel.pos().x(), panel.pos().y()), (0, 0))
        finally:
            panel.close()

    def test_centered_on_is_pure_screen_math(self):
        from src.gui import queue_panel as qp

        # 锚点 (100,100,60,60) 中心 (130,130) → 320x200 面板想放 (-30,30)，左侧夹回 0
        x, y = qp.centered_on((100, 100, 60, 60), 320, 200, 1920, 1040)
        self.assertEqual((x, y), (0, 30))
        # 锚点居中时正好居中
        x, y = qp.centered_on((930, 500, 60, 60), 320, 200, 1920, 1040)
        self.assertEqual((x, y), (800, 430))
        # 贴右下角时夹回工作区
        x, y = qp.centered_on((1800, 1000, 60, 60), 320, 200, 1920, 1040)
        self.assertEqual((x, y), (1920 - 320, 1040 - 200))

    def test_drag_is_clamped_above_taskbar(self):
        """工作区已排除任务栏：拖到底部也得留在任务栏上方。"""
        from PySide6.QtCore import QPoint

        panel = self._panel(collapsed=True, icon_size=64)
        try:
            panel._available_rect = lambda: (0, 0, 1920, 1040)  # 底部 40px 是任务栏
            clamped = panel._clamped_position(QPoint(5000, 5000))
            self.assertLessEqual(clamped.y() + panel.height(), 1040)
            self.assertLessEqual(clamped.x() + panel.width(), 1920)
        finally:
            panel.close()

    def test_ensure_on_screen_pushes_icon_back(self):
        from src.gui import queue_panel as qp

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue_panel.json"
            original = qp.SETTINGS_FILE
            qp.SETTINGS_FILE = path
            try:
                panel = self._panel(collapsed=True, icon_size=64)
                try:
                    panel._available_rect = lambda: (0, 0, 1920, 1040)
                    panel.move(10, 2000)  # 跑到屏幕下面（会被任务栏遮住）
                    panel._ensure_on_screen()
                    self.assertLessEqual(panel.pos().y() + panel.height(), 1040)
                    saved = qp.load_settings(path)
                    self.assertEqual(saved.icon_y, panel.pos().y())
                finally:
                    panel.close()
            finally:
                qp.SETTINGS_FILE = original


class TestPanelGate(unittest.TestCase):
    """需求：游戏在前台永远不显示；游戏在后台时按开关决定。"""

    @classmethod
    def setUpClass(cls):
        if _app() is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def _panel(self, **kwargs):
        from src.gui import queue_panel as qp

        settings = qp.PanelSettings(**{"enabled": True, "show_in_background": True, **kwargs})
        panel = qp.QueuePanel(settings, hwnd_provider=lambda: 4242)
        panel._timer.stop()  # 手动驱动 refresh
        return panel

    def test_hidden_while_game_in_foreground(self):
        from src.gui import queue_panel as qp

        panel = self._panel()
        try:
            with mock.patch.object(qp, "game_in_foreground", return_value=True):
                panel.refresh()
            self.assertFalse(panel.isVisible())
            self.assertTrue(panel._hidden_by_game)
        finally:
            panel.close()

    def test_shown_in_background_when_enabled(self):
        from src.gui import queue_panel as qp

        panel = self._panel()
        try:
            with mock.patch.object(qp, "game_in_foreground", return_value=False):
                panel.refresh()
            self.assertTrue(panel.isVisible())
            self.assertFalse(panel._hidden_by_game)
        finally:
            panel.close()

    def test_background_switch_off_keeps_it_hidden(self):
        from src.gui import queue_panel as qp

        panel = self._panel(show_in_background=False)
        try:
            with mock.patch.object(qp, "game_in_foreground", return_value=False):
                panel.refresh()
            self.assertFalse(panel.isVisible())
        finally:
            panel.close()

    def test_disabled_never_shows(self):
        from src.gui import queue_panel as qp

        panel = self._panel(enabled=False)
        try:
            with mock.patch.object(qp, "game_in_foreground", return_value=False):
                panel.refresh()
            self.assertFalse(panel.isVisible())
        finally:
            panel.close()

    def test_geometry_and_opacity_applied(self):

        panel = self._panel(opacity=0.5, width=360, height=240, collapsed=False)
        try:
            self.assertEqual(panel.width(), 360)
            self.assertEqual(panel.height(), 240)
            self.assertAlmostEqual(panel.windowOpacity(), 0.5, places=2)
        finally:
            panel.close()

    def test_drag_saves_geometry(self):
        from src.gui import queue_panel as qp

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue_panel.json"
            original = qp.SETTINGS_FILE
            qp.SETTINGS_FILE = path
            try:
                panel = self._panel(collapsed=False)
                try:
                    panel.move(120, 90)
                    panel.remember_geometry()
                    saved = qp.load_settings(path)
                    self.assertEqual((saved.x, saved.y), (120, 90))
                finally:
                    panel.close()
            finally:
                qp.SETTINGS_FILE = original


if __name__ == "__main__":
    unittest.main()
