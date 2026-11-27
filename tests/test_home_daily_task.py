import unittest
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
from ok.feature.FeatureSet import FeatureSet

from src.config import config
from src.data.feature_list import FeatureList
from src.tasks.onetime.home_daily_task import HomeDailyTask


SCREENSHOTS = Path(__file__).resolve().parents[1] / "ok_templates"
MEDAL = np.s_[160:281, 900:1021]


class TestHomeHarvest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = config["template_matching"]
        cls.templates = FeatureSet(False, options["coco_feature_json"], options["default_horizontal_variance"],
                                   options["default_vertical_variance"], options["default_threshold"])
        cls.popup = cv2.imread(str(SCREENSHOTS / "screenshot_20261127_225433.png"))

    def claim_one_frame(self, frame):
        executor = Mock(frame=frame, feature_set=self.templates, pause_start=None)
        executor.method.height, executor.method.width = frame.shape[:2]
        executor.device_manager.supported_ratio = 16 / 9
        task = HomeDailyTask(executor, Mock())
        task.ui_ensure = Mock()
        task.loop = Mock(return_value=[frame])
        task.click = Mock()
        task.sleep = Mock()
        task.claim()
        return task

    def test_levelup_closes_across_medal_colors(self):
        medal = self.popup[MEDAL]
        gray = cv2.cvtColor(medal, cv2.COLOR_BGR2GRAY)[:, :, None]
        for name, recolored in (
            ("original", medal),
            ("silver", gray * (0.92, 0.94, 0.96)),
            ("gold", gray * (0.3, 0.7, 1.0)),
            ("blue", gray * (1, 0, 0)),
            ("reversed highlights", 255 - medal),
        ):
            with self.subTest(color=name):
                frame = self.popup.copy()
                frame[MEDAL] = recolored
                task = self.claim_one_frame(frame)
                task.click.assert_called_once()
                self.assertEqual(task.click.call_args.args[0].center(), task.safe_box.center())

    def test_same_colors_without_the_medal_pattern_do_not_dismiss(self):
        frame = self.popup.copy()
        frame[MEDAL] = cv2.flip(frame[MEDAL], 0)
        self.claim_one_frame(frame).click.assert_not_called()

    def test_harvest_result_still_uses_its_close_button(self):
        frame = cv2.imread(str(SCREENSHOTS / "screenshot_20260920_180007.png"))
        task = self.claim_one_frame(frame)
        task.click.assert_called_once()
        self.assertEqual(task.click.call_args.args[0].name, FeatureList.home_claim_cross)
