"""GUI 页冒烟测试：离屏把每个自定义 Tab 真正构建一遍。

静态检查只能确认「名字存在」，确认不了「这一段构造代码能不能跑通」——
`QTableWidget.SelectionBehavior.SelectRows` 这种写错了，要等用户点开那一页才炸。
这里用 Qt 的 offscreen 平台把 9 个页面都构造 + build 一次。

⚠️ 需要 ``QT_QPA_PLATFORM=offscreen``：不弹窗、不需要显示器，CI 也能跑。
"""

import importlib
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_app():
    """离屏 QApplication；Qt 不可用就返回 None（测试会 skip）。"""
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() or QApplication([])
    except Exception:
        return None


class _StubApp:
    """``og.app`` 的最小替身。

    真实运行时 ``og.app`` 一定存在（页面是框架在 ``OK`` 初始化后创建的），这里补一个
    只带 ``tr()`` 的替身，是为了让页面能在没有完整应用的情况下被构建一次。
    ``AccountConfigTab`` 直接用 ``og.app.tr(...)``，所以没有它会构造失败。
    """

    def tr(self, text: str) -> str:
        return text

    def get_config(self, *args, **kwargs):
        return {}


class TestGuiTabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = _make_app()
        if cls.qapp is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def test_every_tab_builds(self):
        from src.config import config

        pairs = config.get("custom_tabs", []) or []
        self.assertGreaterEqual(len(pairs), 9)

        failures = []
        with mock.patch("ok.og.app", _StubApp(), create=True):
            for module_name, class_name in pairs:
                try:
                    module = importlib.import_module(module_name)
                    tab = getattr(module, class_name)()
                    build = getattr(tab, "_build", None)
                    if callable(build):
                        build()
                except Exception as exc:
                    failures.append(f"{class_name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_tab_metadata_is_sane(self):
        """每个页的 name / icon / position 都要能取到（导航栏靠它们）。"""
        from src.config import config

        problems = []
        with mock.patch("ok.og.app", _StubApp(), create=True):
            for module_name, class_name in config.get("custom_tabs", []) or []:
                try:
                    module = importlib.import_module(module_name)
                    tab = getattr(module, class_name)()
                    name = tab.name
                    icon = tab.icon
                except Exception as exc:
                    problems.append(f"{class_name}: {type(exc).__name__}: {exc}")
                    continue
                if not isinstance(name, str) or not name.strip():
                    problems.append(f"{class_name}: name 为空")
                if icon is None:
                    problems.append(f"{class_name}: icon 为 None")
        self.assertEqual(problems, [], "\n".join(problems))


class TestGrowthImportRouting(unittest.TestCase):
    """「导入数据」按钮要按扩展名分流：CSV/TSV 走表格解析，JSON 走直投校验。

    这条容易漏：按钮改了过滤器但处理函数还只认 json，用户选了 .csv 会得到
    「不是合法 JSON」这种看不懂的报错。
    """

    @classmethod
    def setUpClass(cls):
        if _make_app() is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def _tab(self):
        from src.gui.offline_tools_tab import OfflineToolsTab

        with mock.patch("ok.og.app", _StubApp(), create=True):
            tab = OfflineToolsTab()
            build = getattr(tab, "_build", None)
            if callable(build):
                build()
        tab._notify = lambda *args, **kwargs: None
        tab.refresh_growth_entries = lambda: None
        return tab

    def test_csv_goes_to_table_import(self):
        tab = self._tab()
        with mock.patch(
            "src.gui.offline_tools_tab.QFileDialog.getOpenFileName",
            return_value=("养成表.csv", ""),
        ), mock.patch(
            "src.gui.offline_tools_tab.import_table",
            return_value=mock.Mock(ok=True, describe=lambda: "ok"),
        ) as table_mock, mock.patch(
            "src.gui.offline_tools_tab.import_dataset",
        ) as dataset_mock:
            tab._import_growth_data()
        table_mock.assert_called_once_with("养成表.csv")
        dataset_mock.assert_not_called()

    def test_json_goes_to_dataset_import(self):
        tab = self._tab()
        with mock.patch(
            "src.gui.offline_tools_tab.QFileDialog.getOpenFileName",
            return_value=("growth.json", ""),
        ), mock.patch(
            "src.gui.offline_tools_tab.import_table",
        ) as table_mock, mock.patch(
            "src.gui.offline_tools_tab.import_dataset",
            return_value=mock.Mock(ok=True, describe=lambda: "ok"),
        ) as dataset_mock:
            tab._import_growth_data()
        dataset_mock.assert_called_once_with("growth.json")
        table_mock.assert_not_called()

    def test_cancel_does_nothing(self):
        tab = self._tab()
        with mock.patch(
            "src.gui.offline_tools_tab.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ), mock.patch("src.gui.offline_tools_tab.import_table") as table_mock:
            tab._import_growth_data()
        table_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
