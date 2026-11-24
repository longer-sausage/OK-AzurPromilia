"""导航栏布局补丁的测试：本项目页面进滚动区、排在最前、底部不动。

真实诉求：左侧一整列只留一个滚动条，且「开发工具 / 运行代码 / 通知 / 设置 / 关于」
固定在下半部分不跟着滚。

第一版补丁把 topLayout 的控件搬到 scrollLayout，结果滚动区跑到标题栏下面跟标题重叠；
现在改成「只改注册位置（SCROLL）+ 在同一布局内排序」，这些测试锁住新做法。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() or QApplication([])
    except Exception:
        return None


class TestSelectors(unittest.TestCase):
    def test_our_pages_detected_by_module(self):
        from src.patches.nav_layout_patch import is_our_page

        class Ours:
            pass

        Ours.__module__ = "src.gui.flow_tab"
        self.assertTrue(is_our_page(Ours()))

        class Framework:
            pass

        Framework.__module__ = "ok.ui.qt.tasks.OneTimeTaskTab"
        self.assertFalse(is_our_page(Framework()))


class TestReorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _app() is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def test_ours_move_first_and_bottom_untouched(self):
        from PySide6.QtWidgets import QWidget
        from qfluentwidgets.components.navigation.navigation_panel import NavigationItemLayout

        from src.patches import nav_layout_patch

        layout = NavigationItemLayout()
        bottom = NavigationItemLayout()
        ours = []
        others = []
        # 模拟真实顺序：框架的 Capture 先加，然后本项目 9 页，再 Triggers/Tasks
        for name in ("Capture",):
            w = QWidget()
            w.setObjectName(name)
            layout.addWidget(w)
            others.append(w)
        for index in range(3):
            w = QWidget()
            w.setObjectName(f"ours{index}")
            layout.addWidget(w)
            ours.append(w)
            nav_layout_patch._OUR_PAGES.add(id(w))
        for name in ("Triggers", "Tasks"):
            w = QWidget()
            w.setObjectName(name)
            layout.addWidget(w)
            others.append(w)
        for name in ("Debug", "Run Code", "Settings"):
            w = QWidget()
            w.setObjectName(name)
            bottom.addWidget(w)

        class Panel:
            scrollLayout = layout
            bottomLayout = bottom

        moved = nav_layout_patch.reorder_ours_first(Panel())
        self.assertEqual(moved, 3)

        order = [layout.itemAt(i).widget().objectName() for i in range(layout.count())]
        self.assertEqual(
            order,
            ["ours0", "ours1", "ours2", "Capture", "Triggers", "Tasks"],
            "本项目页面在前，框架项保持相对顺序",
        )
        self.assertEqual(bottom.count(), 3, "底部区不该被改动")

    def test_missing_layout_is_safe(self):
        from src.patches.nav_layout_patch import reorder_ours_first

        class Broken:
            @property
            def scrollLayout(self):
                raise RuntimeError("没有布局")

        self.assertEqual(reorder_ours_first(Broken()), 0)


class TestInstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _app() is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def test_install_is_idempotent(self):
        from src.patches.nav_layout_patch import install_nav_layout_patch

        self.assertTrue(install_nav_layout_patch())
        self.assertTrue(install_nav_layout_patch())

    def test_our_pages_are_registered_as_scroll(self):
        """本项目页面必须被改成 SCROLL（进滚动区），框架页面保持原样。"""
        from qfluentwidgets import NavigationItemPosition

        from src.patches import nav_layout_patch

        class OurPage:
            pass

        OurPage.__module__ = "src.gui.offline_tools_tab"
        page = OurPage()
        position = nav_layout_patch.navigation_position_for(page, NavigationItemPosition.TOP)
        self.assertEqual(position, NavigationItemPosition.SCROLL)
        self.assertIn(id(page), nav_layout_patch._OUR_PAGES)

        class FrameworkTab:
            pass

        FrameworkTab.__module__ = "ok.ui.qt.tasks.OneTimeTaskTab"
        other = FrameworkTab()
        kept = nav_layout_patch.navigation_position_for(other, NavigationItemPosition.TOP)
        self.assertEqual(kept, NavigationItemPosition.TOP)
        self.assertNotIn(id(other), nav_layout_patch._OUR_PAGES)

    def test_bottom_position_is_respected(self):
        """底部项（设置/关于等）不能被误改成 SCROLL。"""
        from qfluentwidgets import NavigationItemPosition

        from src.patches.nav_layout_patch import navigation_position_for

        class SettingsTab:
            pass

        SettingsTab.__module__ = "ok.ui.qt.settings.SettingTab"
        self.assertEqual(
            navigation_position_for(SettingsTab(), NavigationItemPosition.BOTTOM),
            NavigationItemPosition.BOTTOM,
        )


if __name__ == "__main__":
    unittest.main()
