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

        for key in ("custom_tasks", "use_gui", "config_folder", "onetime_tasks", "trigger_tasks"):
            self.assertIn(key, config)


if __name__ == "__main__":
    unittest.main()