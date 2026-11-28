"""识别基础设施单元测试：模板库（多分辨率/预处理/probe）、槽位识别、识别报告。"""

from __future__ import annotations

import unittest
from unittest import mock

import cv2
import numpy as np

from src.combat import profile
from src.combat.recognition import (
    Region,
    TemplateLibrary,
    TemplateSpec,
    badge_scores,
    binarize_brightness,
    detect_current_slot,
    find_lock_star,
    load_lock_star,
    read_skill,
    recognize,
    scale_anchor,
)
from src.combat.recognition.slots import SlotConfig


def _checker_template(size: int = 40) -> np.ndarray:
    """四象限黑白块：高方差，适合 CCOEFF_NORMED。"""
    image = np.zeros((size, size, 3), np.uint8)
    half = size // 2
    image[:half, :half] = 255
    image[half:, half:] = 255
    return image


class TestRegions(unittest.TestCase):
    def test_region_pixels_and_crop(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        region = Region(0.5, 0.5, 0.75, 0.75)
        self.assertEqual(region.pixels(1920, 1080), (960, 540, 1440, 810))
        self.assertEqual(region.crop(frame).shape[:2], (270, 480))

    def test_scale_anchor_keeps_distance_to_edge(self):
        self.assertEqual(scale_anchor(100, 100, 1920, 1080, 1920, 1080), (100, 100))
        # 左半区按比例缩放，右半区贴右边
        x, y = scale_anchor(1280, 720, 2560, 1440, 1920, 1080)
        self.assertEqual((x, y), (960, 540))
        x2, _ = scale_anchor(2500, 100, 2560, 1440, 1920, 1080)
        self.assertEqual(x2, 1920 - round(60 * 0.75))


class TestTemplateLibrary(unittest.TestCase):
    def test_match_same_resolution(self):
        template = _checker_template()
        library = TemplateLibrary()
        library.register(TemplateSpec("checker", template, 1920, 1080, (100, 100)))

        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[100:140, 100:140] = template
        match = library.match(frame, "checker", threshold=0.9)
        self.assertIsNotNone(match)
        self.assertAlmostEqual(match.score, 1.0, delta=0.01)
        self.assertLessEqual(abs(match.box[0] - 100), 2)
        self.assertLessEqual(abs(match.box[1] - 100), 2)

    def test_match_across_resolution_uses_scaled_template(self):
        template = _checker_template(40)
        library = TemplateLibrary()
        library.register(TemplateSpec("checker", template, 2560, 1440, (1280, 720)))

        frame = np.zeros((1080, 1920, 3), np.uint8)
        scaled = cv2.resize(template, (30, 30), interpolation=cv2.INTER_AREA)
        frame[540:570, 960:990] = scaled
        match = library.match(frame, "checker", threshold=0.85)
        self.assertIsNotNone(match)
        self.assertLessEqual(abs(match.box[0] - 960), 3)
        self.assertLessEqual(abs(match.box[1] - 540), 3)
        self.assertEqual(match.box[2], 30)

    def test_probe_returns_score_below_threshold(self):
        template = _checker_template()
        library = TemplateLibrary()
        library.register(TemplateSpec("checker", template, 1920, 1080, (100, 100)))
        frame = np.zeros((1080, 1920, 3), np.uint8)
        found, score = library.probe(frame, "checker", threshold=0.9)
        self.assertIsNone(found)
        self.assertLess(score, 0.9)

    def test_preprocess_applied_to_template_and_search(self):
        template = _checker_template()
        library = TemplateLibrary()
        library.register(
            TemplateSpec(
                "checker",
                template,
                1920,
                1080,
                (100, 100),
                preprocess=lambda image: binarize_brightness(image, 100),
            )
        )
        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[100:140, 100:140] = (template * 0.8).astype(np.uint8)  # 变暗后二值化仍一致
        match = library.match(frame, "checker", threshold=0.9)
        self.assertIsNotNone(match)

    def test_load_folder_parses_filename(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            image = _checker_template(20)
            path = Path(folder) / "btn_1920x1080_100_200.png"
            cv2.imwrite(str(path), image)
            library = TemplateLibrary()
            self.assertEqual(library.load_folder(folder), 1)
            spec = library.get("btn")
            self.assertIsNotNone(spec)
            self.assertEqual((spec.base_width, spec.base_height), (1920, 1080))
            self.assertEqual(spec.anchor, (100, 200))


class TestSlots(unittest.TestCase):
    def _config(self):
        return SlotConfig(
            saturation_weight=profile.SLOT_SATURATION_WEIGHT,
            accept_margin=profile.SLOT_ACCEPT_MARGIN,
        )

    def _frame_with_pale_badge(self, slot_index: int):
        """当前角色 = 淡色徽章（低饱和高亮），其余 = 深蓝底。"""
        frame = np.zeros((1080, 1920, 3), np.uint8)
        for index, region in enumerate(profile.SLOT_REGIONS):
            x1, y1, x2, y2 = Region(*region).pixels(1920, 1080)
            color = (200, 200, 200) if index == slot_index else (90, 40, 20)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)
        return frame

    def test_detects_pale_badge_slot(self):
        detection = detect_current_slot(self._frame_with_pale_badge(1), profile.SLOT_REGIONS, self._config())
        self.assertTrue(detection.accepted)
        self.assertEqual(detection.index, 1)

    def test_rejects_when_all_badges_alike(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        for region in profile.SLOT_REGIONS:
            x1, y1, x2, y2 = Region(*region).pixels(1920, 1080)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (90, 40, 20), -1)
        detection = detect_current_slot(frame, profile.SLOT_REGIONS, self._config())
        self.assertFalse(detection.accepted)
        self.assertEqual(detection.index, -1)

    def test_badge_scores_prefer_pale(self):
        scores = badge_scores(self._frame_with_pale_badge(2), profile.SLOT_REGIONS, self._config())
        self.assertGreater(scores[2], scores[0])
        self.assertGreater(scores[2], scores[1])


class TestLockStar(unittest.TestCase):
    SCALE = 1.6  # 实测星形随距离在 0.8~1.6 倍之间；用偏大的尺寸做稳定样本

    def _frame(self, with_star: bool, with_glow: bool = True):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        if not with_star:
            return frame
        if with_glow:
            cv2.circle(frame, (900, 500), 74, (0, 0, 220), -1)
        template = load_lock_star()
        self.assertIsNotNone(template)
        mask = cv2.resize(template, None, fx=self.SCALE, fy=self.SCALE, interpolation=cv2.INTER_NEAREST) > 127
        top, left = 500 - mask.shape[0] // 2, 900 - mask.shape[1] // 2
        patch = frame[top : top + mask.shape[0], left : left + mask.shape[1]]
        patch[mask] = (255, 255, 255)
        return frame

    def test_detects_star_with_red_glow(self):
        reading = find_lock_star(self._frame(True), profile.LOCK_REGION)
        self.assertIsNotNone(reading)
        self.assertGreaterEqual(reading.red_ratio, profile.LOCK_STAR_RED_MIN)

    def test_ignores_star_without_glow(self):
        self.assertIsNone(find_lock_star(self._frame(True, with_glow=False), profile.LOCK_REGION))

    def test_ignores_empty_frame(self):
        self.assertIsNone(find_lock_star(self._frame(False), profile.LOCK_REGION))


class TestReport(unittest.TestCase):
    def _battle_frame(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        # 中部薄血条
        cv2.rectangle(frame, (760, 400), (1040, 406), (40, 40, 40), -1)
        cv2.rectangle(frame, (760, 400), (984, 406), (0, 0, 220), -1)
        # 槽位 0 淡色徽章（当前角色）
        x1, y1, x2, y2 = Region(*profile.SLOT_REGIONS[0]).pixels(1920, 1080)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (200, 200, 200), -1)
        # 技能 Q 白环
        x1, y1, x2, y2 = Region(*profile.SKILL_REGIONS["skill"]).pixels(1920, 1080)
        cv2.circle(
            frame,
            ((x1 + x2) // 2, (y1 + y2) // 2),
            int(min(x2 - x1, y2 - y1) * 0.45),
            (255, 255, 255),
            3,
        )
        return frame

    def test_recognize_reports_all_signals(self):
        frame = self._battle_frame()
        find = mock.Mock(return_value=None)
        report = recognize(frame, find=find)

        self.assertTrue(report.frame_ok)
        self.assertIsNotNone(report.health)
        self.assertAlmostEqual(report.health.fill_ratio, 0.8, delta=0.06)
        self.assertTrue(report.skills["skill"].ready)
        self.assertIn("血量=", report.summary())

    def test_recognize_handles_missing_frame(self):
        report = recognize(None)
        self.assertFalse(report.frame_ok)

    def test_skill_reading_with_ocr_hook(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        reading = read_skill(frame, "skill", profile.SKILL_REGIONS["skill"], ocr=lambda roi: "3.2")
        self.assertFalse(reading.ready)
        self.assertEqual(reading.cooldown_text, "3.2")


if __name__ == "__main__":
    unittest.main()
