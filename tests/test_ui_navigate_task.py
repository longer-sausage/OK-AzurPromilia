import unittest
from unittest import mock

from ok import WaitFailedException

from src.data.page import Page
from src.tasks.test.test_ui_navigate_task import TestUINavigateTask


def build_task(debug=True):
    executor = mock.MagicMock()
    executor.debug = debug
    app = mock.MagicMock()
    return TestUINavigateTask(executor, app)


class TestUINavigateTaskCase(unittest.TestCase):

    def test_init_properties(self):
        task = build_task(debug=False)
        self.assertEqual(task.name, "UI导航测试")
        self.assertFalse(task.visible)

        task_debug = build_task(debug=True)
        self.assertTrue(task_debug.visible)

    def test_default_config_and_types(self):
        task = build_task()
        self.assertIn("目标界面", task.default_config)
        self.assertIn("超时时间(秒)", task.default_config)
        self.assertIn("防抖等待(秒)", task.default_config)

        self.assertEqual(task.default_config["超时时间(秒)"], 30.0)
        self.assertEqual(task.default_config["防抖等待(秒)"], 1.0)

        config_type = task.config_type
        self.assertIn("目标界面", config_type)
        self.assertEqual(config_type["目标界面"]["type"], "drop_down")
        # Ensure registered pages are in options
        for page_name in Page.all_pages.keys():
            self.assertIn(page_name, config_type["目标界面"]["options"])

    @mock.patch.object(TestUINavigateTask, "ui_goto")
    def test_run_success(self, mock_ui_goto):
        mock_ui_goto.return_value = True
        task = build_task()
        task.config = {
            "目标界面": "page_home",
            "超时时间(秒)": 15.0,
            "防抖等待(秒)": 0.5,
        }

        task.run()
        mock_ui_goto.assert_called_once_with("page_home", time_out=15.0, interval=0.5)

    @mock.patch.object(TestUINavigateTask, "ui_goto")
    def test_run_interrupted(self, mock_ui_goto):
        mock_ui_goto.return_value = False
        task = build_task()
        task.config = {
            "目标界面": "page_menu",
            "超时时间(秒)": 10.0,
            "防抖等待(秒)": 1.0,
        }

        task.run()
        mock_ui_goto.assert_called_once_with("page_menu", time_out=10.0, interval=1.0)

    @mock.patch.object(TestUINavigateTask, "ui_goto")
    def test_run_timeout_exception(self, mock_ui_goto):
        mock_ui_goto.side_effect = WaitFailedException("timeout reached")
        task = build_task()
        task.config = {
            "目标界面": "page_main",
            "超时时间(秒)": 5.0,
            "防抖等待(秒)": 0.2,
        }

        with self.assertRaises(WaitFailedException):
            task.run()


if __name__ == "__main__":
    unittest.main()
