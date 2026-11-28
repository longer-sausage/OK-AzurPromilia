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

    def test_uid_overlay_registers_optional_framework_setting(self):
        from ok.util.GlobalConfig import create_basic_options

        from src.config import config

        options = create_basic_options(enable_blur=callable(config.get("blur_area")))
        self.assertIs(options.default_config["Enable Blur"], False)
        self.assertIn("Blur Algorithm", options.default_config)
        self.assertIn("Blur Interval", options.default_config)

    def test_uid_overlay_matches_existing_screenshot_mask(self):
        import numpy as np
        from ok.util.blur import get_blur_rects, make_blur_patches

        from src.config import config

        callback = config["blur_area"]
        for w, h, rect in [(1920, 1080, (163, 1057, 135, 23)), (3840, 2160, (326, 2113, 270, 47))]:
            with self.subTest(size=(w, h)):
                self.assertEqual(get_blur_rects(callback, w, h), [rect])
                frame = np.full((h, w, 3), 255, dtype=np.uint8)
                patches = make_blur_patches(frame, callback, "Blur")
                self.assertEqual(patches[0][:4], rect)
                self.assertTrue(np.all(frame == 255))
                x, y, rw, rh = rect
                expected = frame.copy()
                expected[y:y + rh, x:x + rw] = 0
                np.testing.assert_array_equal(config["screenshot_processor"](frame), expected)
        self.assertEqual(callback(0, 1080), [])
        self.assertEqual(callback(1920, 0), [])

    def test_windows_config_includes_cn_and_jp_executables(self):
        from src.config import config

        windows = config["windows"]
        self.assertIn("AzurPromilia.exe", windows["exe"])
        self.assertIn("AzurPromiliaJP.exe", windows["exe"])
        self.assertEqual(windows["hwnd_class"], "UnityWndClass")


if __name__ == "__main__":
    unittest.main()
