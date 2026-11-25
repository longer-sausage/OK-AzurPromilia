import importlib
import unittest


class TestConfig(unittest.TestCase):

    def test_config_registered_tasks_importable(self):
        from src.config import config

        for module_path, class_name in config["onetime_tasks"] + config["trigger_tasks"]:
            with self.subTest(task=class_name):
                module = importlib.import_module(module_path)
                task_class = getattr(module, class_name)
                self.assertTrue(callable(task_class))

    def test_config_required_keys(self):
        from src.config import config

        for key in ("custom_tasks", "config_folder", "onetime_tasks", "trigger_tasks"):
            self.assertIn(key, config)
        from ok.core.ui_config import resolve_ui_config

        self.assertEqual(resolve_ui_config(config)["type"], "qt")


if __name__ == "__main__":
    unittest.main()
