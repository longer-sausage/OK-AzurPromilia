import unittest

import cv2
import numpy as np
from ok import Box

from src.image.treasure_band_detector import (
    DEFAULT_BAND_THRESHOLDS,
    BandThresholds,
    TreasureBandDetector,
)

# ── 样本布局取自 1920x1080 实测：ROI = (1305, 251, 43, 589) ──────────
FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080
ROI_BOX = Box(1305, 251, 43, 589)

# 低饱和高亮（青白色）→ HSV 约 (90, 28, 225)，落在默认区间内
BAND_BGR = (225, 225, 200)  # BGR
# 深色轨道背景 → V≈40，不落在区间内
RAIL_BGR = (40, 35, 30)

# 期望命中的三条颜色带（帧坐标）
BAND_1 = (1317, 286, 16, 105)  # y 286~390
BAND_2 = (1316, 660, 19, 53)  # y 660~712
BAND_3 = (1316, 730, 19, 53)  # y 730~782

# 期望被过滤的干扰项（各干扰之间 Y 不重叠，否则会被并成一个连通域）
HORIZONTAL_KEY = (1316, 600, 28, 35)  # 矮胖：h/w = 1.25 < 2 → not_vertical
EDGE_RAIL = (1305, 420, 11, 155)  # 贴 ROI 左边缘 → on_edge
TOO_WIDE = (1307, 810, 35, 100)  # 宽 35 > 30（超出 ROI 下沿被裁到 h=31）→ too_wide
TINY_NOISE = (1330, 800, 4, 4)  # 面积 16 < 300 → area


def _background(color=RAIL_BGR):
    return np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), color, dtype=np.uint8)


def _fill(frame, rect, color=BAND_BGR):
    x, y, w, h = rect
    cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), color, thickness=-1)


def _sample_frame():
    frame = _background()
    for rect in (BAND_1, BAND_2, BAND_3, HORIZONTAL_KEY, EDGE_RAIL, TOO_WIDE, TINY_NOISE):
        _fill(frame, rect)
    return frame


class TestTreasureBandDetector(unittest.TestCase):
    def setUp(self):
        self.detector = TreasureBandDetector()
        self.frame = _sample_frame()

    # ── 1. 命中 ─────────────────────────────────────────────

    def test_finds_three_bands_sorted_by_y(self):
        bands = self.detector.find(self.frame, ROI_BOX)
        self.assertEqual(len(bands), 3, "三条竖向颜色带应全部命中")
        self.assertEqual(
            [(b.x, b.y, b.width, b.height) for b in bands],
            [BAND_1, BAND_2, BAND_3],
        )
        self.assertTrue(all(b.name == DEFAULT_BAND_THRESHOLDS.box_name for b in bands))

    def test_box_coordinates_are_frame_absolute(self):
        """返回的 Box 必须是整帧坐标，可直接交给 click() / draw_boxes()。"""
        bands = self.detector.find(self.frame, ROI_BOX)
        for band in bands:
            self.assertGreater(band.x, ROI_BOX.x)
            self.assertGreaterEqual(band.y, ROI_BOX.y)

    def test_confidence_in_range(self):
        bands = self.detector.find(self.frame, ROI_BOX)
        for band in bands:
            self.assertGreaterEqual(band.confidence, 0.5)
            self.assertLessEqual(band.confidence, 1.0)

    # ── 2. 过滤干扰 ─────────────────────────────────────────

    def _failed_reasons(self):
        return [item.failed for item in self.detector.analyze(self.frame, ROI_BOX) if not item.matched]

    def test_rejects_horizontal_key_structure(self):
        self.assertIn("not_vertical", self._failed_reasons())

    def test_rejects_rail_touching_roi_edge(self):
        self.assertIn("on_edge", self._failed_reasons())

    def test_rejects_too_wide_block(self):
        self.assertIn("too_wide", self._failed_reasons())

    def test_rejects_tiny_noise(self):
        self.assertIn("area", self._failed_reasons())

    def test_rejected_items_carry_metrics(self):
        by_reason = {item.failed: item for item in self.detector.analyze(self.frame, ROI_BOX) if not item.matched}
        self.assertEqual((by_reason["not_vertical"].width, by_reason["not_vertical"].height), (28, 35))
        self.assertEqual(by_reason["area"].area, 16)

    # ── 3. 无命中 ───────────────────────────────────────────

    def test_no_detection_on_dark_frame(self):
        self.assertEqual(self.detector.find(_background(), ROI_BOX), [])

    def test_empty_input(self):
        self.assertEqual(self.detector.find(None, ROI_BOX), [])
        self.assertEqual(self.detector.find(self.frame, None), [])

    def test_saturated_color_rejected(self):
        """高饱和（纯红）不该被当成颜色带。"""
        frame = _background()
        _fill(frame, BAND_1, color=(0, 0, 255))
        self.assertEqual(self.detector.find(frame, ROI_BOX), [])

    # ── 4. Y 区间与命中判定 ─────────────────────────────────

    def test_y_ranges(self):
        bands = self.detector.find(self.frame, ROI_BOX)
        self.assertEqual(
            TreasureBandDetector.y_ranges(bands),
            [(286, 391), (660, 713), (730, 783)],
        )

    def test_hit_by_center_y(self):
        bands = self.detector.find(self.frame, ROI_BOX)
        hit = TreasureBandDetector.hit_by_center_y(bands, 690)
        self.assertIsNotNone(hit)
        self.assertEqual((hit.y, hit.y + hit.height), (660, 713))
        self.assertIsNone(TreasureBandDetector.hit_by_center_y(bands, 640), "两条带之间的空隙不该命中")

    # ── 5. 阈值 / 输入兼容 ─────────────────────────────────

    def test_thresholds_with_unknown_raises(self):
        with self.assertRaises(TypeError):
            DEFAULT_BAND_THRESHOLDS.with_(no_such_field=1)

    def test_lower_v_threshold_drops_bands(self):
        """V 下限抬高到 250 后，V≈225 的颜色带全部落空。"""
        detector = TreasureBandDetector(DEFAULT_BAND_THRESHOLDS.with_(
            lower=(0, 0, 250), upper=(180, 80, 255)))
        self.assertEqual(detector.find(self.frame, ROI_BOX), [])

    def test_accepts_bgra_and_gray_frames(self):
        bgra = cv2.cvtColor(self.frame, cv2.COLOR_BGR2BGRA)
        self.assertEqual(len(self.detector.find(bgra, ROI_BOX)), 3)
        gray = cv2.cvtColor(self.frame, cv2.COLOR_BGR2GRAY)
        self.assertEqual(len(self.detector.find(gray, ROI_BOX)), 3)

    def test_box_clamped_to_frame(self):
        """ROI 超出帧范围时不抛异常，只检测帧内部分。"""
        oversized = Box(1900, 1000, 200, 200)
        self.assertEqual(self.detector.find(self.frame, oversized), [])


if __name__ == "__main__":
    unittest.main()
