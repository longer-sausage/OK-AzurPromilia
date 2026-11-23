"""可射击光圈 / 弧形标识检测器测试（合成帧，不依赖真实截图）。

重点验证**圆弧拟合判据**：同样一块颜色，画成圆弧要命中并点在圆心，
画成实心块或直线则必须被拒 —— 这是跟纯颜色阈值的本质区别。
真实样本（1920x1080）里那道弧的实测值是：圆心 ≈ 屏幕中心、半径 ≈ 52px、
残差 < 1.5px、弧宽 3~6px，这里的合成帧按同量级构造。
"""

import math
import unittest

import cv2
import numpy as np
from ok import Box

from src.image.glow_target_detector import (
    ALL_GLOW_COLORS,
    DEFAULT_GLOW_THRESHOLDS,
    GLOW_GREEN,
    GLOW_RED,
    GLOW_YELLOW,
    GlowTargetDetector,
    GlowThresholds,
)

# ── ROI：归一化 (0.4724, 0.4426, 0.5365, 0.5611) 在 1920x1080 下的像素框 ──
FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080
ROI_BOX = Box(907, 478, 123, 128)

# 深色背景：V=30，远低于三色的 V 下限
BACKGROUND_BGR = (30, 30, 30)

# ROI 中心的一道弧：半径 52，只画右侧 60°~120°（与真实样本同量级）
ARC_CENTER = (962, 542)
ARC_RADIUS = 52
ARC_START, ARC_END = -60, 60  # OpenCV 角度制，0° 指向右侧


def _bgr_from_hsv(h, s, v):
    """按 HSV 生成 BGR 颜色，避免手算通道值出错。"""
    pixel = np.uint8([[[h, s, v]]])
    return tuple(int(channel) for channel in cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0][0])


GREEN_BGR = _bgr_from_hsv(75, 255, 255)
YELLOW_BGR = _bgr_from_hsv(25, 255, 255)
RED_BGR = _bgr_from_hsv(0, 255, 255)
RED_FAR_BGR = _bgr_from_hsv(170, 255, 255)  # 红色在 HSV 另一端的取值


def _background():
    return np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), BACKGROUND_BGR, dtype=np.uint8)


def _arc(frame, color, center=ARC_CENTER, radius=ARC_RADIUS, width=5,
         start=ARC_START, end=ARC_END):
    """画一段圆弧（可射击标识的样子）。"""
    cv2.ellipse(frame, center, (radius, radius), 0, start, end, color, thickness=width)


def _ring(frame, color, center=ARC_CENTER, radius=ARC_RADIUS, width=5):
    """画一整圈光环（闭合情形也要能命中）。"""
    cv2.circle(frame, center, radius, color, thickness=width)


def _disc(frame, color, center=ARC_CENTER, radius=ARC_RADIUS):
    """画一个实心色块（不该被弧形模式接受）。"""
    cv2.circle(frame, center, radius, color, thickness=-1)


def _fill(frame, rect, color):
    x, y, w, h = rect
    cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), color, thickness=-1)


class TestGlowTargetDetector(unittest.TestCase):
    def setUp(self):
        self.detector = GlowTargetDetector()

    # ── 1. 三色弧命中 ───────────────────────────────────────

    def test_detects_green_arc(self):
        frame = _background()
        _arc(frame, GREEN_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertTrue(result.matched)
        self.assertEqual(result.color, GLOW_GREEN)
        self.assertTrue(result.is_arc)

    def test_detects_yellow_arc(self):
        frame = _background()
        _arc(frame, YELLOW_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertTrue(result.matched)
        self.assertEqual(result.color, GLOW_YELLOW)

    def test_detects_red_arc_on_both_hsv_ends(self):
        for color in (RED_BGR, RED_FAR_BGR):
            frame = _background()
            _arc(frame, color)
            result = self.detector.analyze(frame, ROI_BOX)
            self.assertTrue(result.matched, f"{color} 应命中红色区间")
            self.assertEqual(result.color, GLOW_RED)

    def test_detects_full_ring_too(self):
        """闭合光环同样是圆，不能只认开放弧。"""
        frame = _background()
        _ring(frame, GREEN_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertTrue(result.matched)
        self.assertTrue(result.is_arc)

    def test_short_arc_still_detected(self):
        """60° 的短弧（弦长约等于半径）也要能拟合出来。"""
        frame = _background()
        _arc(frame, GREEN_BGR, start=-30, end=30, width=5)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertTrue(result.matched)

    # ── 2. 点击位置必须是圆心 ───────────────────────────────

    def test_box_centers_on_fitted_circle_center(self):
        """弧只是圆的一段，点外接框中心就歪了 —— 必须点拟合圆心。"""
        frame = _background()
        _arc(frame, GREEN_BGR)
        box = self.detector.find(frame, ROI_BOX)
        self.assertIsNotNone(box)
        cx, cy = box.center()
        self.assertAlmostEqual(cx, ARC_CENTER[0], delta=4)
        self.assertAlmostEqual(cy, ARC_CENTER[1], delta=4)
        self.assertEqual(box.name, DEFAULT_GLOW_THRESHOLDS.box_name)

    def test_reports_arc_metrics(self):
        """半径 / 残差 / 跨度是调参依据，必须是合理值。"""
        frame = _background()
        _arc(frame, GREEN_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertAlmostEqual(result.radius, ARC_RADIUS, delta=4.0)
        self.assertLess(result.residual, 3.0)
        self.assertGreaterEqual(result.span, DEFAULT_GLOW_THRESHOLDS.min_span)
        # 圆心允许几像素偏差：拟合的是离散像素点，不是解析曲线
        self.assertAlmostEqual(result.center[0], ARC_CENTER[0], delta=5)
        self.assertAlmostEqual(result.center[1], ARC_CENTER[1], delta=5)

    def test_confidence_and_color_votes(self):
        frame = _background()
        _arc(frame, GREEN_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertEqual(max(result.counts, key=lambda c: result.counts[c]), GLOW_GREEN)
        self.assertGreaterEqual(result.box.confidence, 0.5)
        self.assertLessEqual(result.box.confidence, 1.0)

    # ── 3. 弧形判据：不像圆的都要拒 ─────────────────────────

    def test_solid_disc_rejected_by_default(self):
        """同色实心圆盘：颜色达标但点不落在同一个圆上 → 不命中。"""
        frame = _background()
        _disc(frame, GREEN_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertFalse(result.matched)
        self.assertEqual(result.failed, "not_arc")

    def test_solid_disc_detected_when_arc_not_required(self):
        frame = _background()
        _disc(frame, GREEN_BGR)
        detector = GlowTargetDetector(DEFAULT_GLOW_THRESHOLDS.with_(require_arc=False))
        result = detector.analyze(frame, ROI_BOX)
        self.assertTrue(result.matched)
        self.assertFalse(result.is_arc)

    def test_straight_line_rejected(self):
        """直线会被拟合成半径无穷大的圆，应被半径上限挡掉。"""
        frame = _background()
        cv2.line(frame, (940, 490), (990, 600), GREEN_BGR, thickness=5)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertFalse(result.matched)
        self.assertIn(result.failed, ("shape", "not_arc"))

    def test_tiny_dot_rejected_by_span(self):
        """一小粒亮点不是可射击标识。"""
        frame = _background()
        _arc(frame, GREEN_BGR, radius=ARC_RADIUS, start=-3, end=3)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertFalse(result.matched)

    def test_radius_out_of_range_rejected(self):
        """半径超出光圈量级（太大 / 太小）都不认。"""
        frame = _background()
        _arc(frame, GREEN_BGR, radius=200, start=-20, end=20, width=5)
        self.assertFalse(self.detector.analyze(frame, ROI_BOX).matched)

    # ── 4. 未命中 ───────────────────────────────────────────

    def test_dark_frame_has_no_detection(self):
        result = self.detector.analyze(_background(), ROI_BOX)
        self.assertFalse(result.matched)
        self.assertEqual(result.failed, "no_contour")
        self.assertIsNone(self.detector.find(_background(), ROI_BOX))

    def test_empty_input(self):
        self.assertEqual(self.detector.analyze(None, ROI_BOX).failed, "empty_input")
        self.assertEqual(self.detector.analyze(_background(), None).failed, "empty_input")

    def test_glow_outside_roi_is_ignored(self):
        """ROI 之外的弧不该被点到。"""
        frame = _background()
        _arc(frame, GREEN_BGR, center=(300, 300))
        self.assertFalse(self.detector.analyze(frame, ROI_BOX).matched)

    def test_tiny_blob_rejected_by_area(self):
        frame = _background()
        _fill(frame, (960, 540, 5, 5), GREEN_BGR)  # 面积 25 < 60
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertFalse(result.matched)
        self.assertEqual(result.failed, "area")
        self.assertLess(result.area, DEFAULT_GLOW_THRESHOLDS.min_area)

    def test_dull_color_rejected(self):
        """亮度 / 饱和度不够的背景色（草地之类）不该被当成光效。"""
        frame = _background()
        _arc(frame, _bgr_from_hsv(75, 90, 150), width=6)
        self.assertFalse(self.detector.analyze(frame, ROI_BOX).matched)

    def test_min_area_threshold_can_be_raised(self):
        frame = _background()
        _arc(frame, GREEN_BGR)
        detector = GlowTargetDetector(DEFAULT_GLOW_THRESHOLDS.with_(min_area=50000))
        self.assertFalse(detector.analyze(frame, ROI_BOX).matched)

    # ── 5. 颜色开关 ─────────────────────────────────────────

    def test_disabled_color_is_not_detected(self):
        frame = _background()
        _arc(frame, YELLOW_BGR)
        detector = GlowTargetDetector(colors=(GLOW_GREEN,))
        self.assertFalse(detector.analyze(frame, ROI_BOX).matched)

    def test_enabled_color_still_detected(self):
        frame = _background()
        _arc(frame, GREEN_BGR)
        detector = GlowTargetDetector(colors=(GLOW_GREEN,))
        self.assertTrue(detector.analyze(frame, ROI_BOX).matched)

    # ── 6. 阈值 / 输入兼容 ─────────────────────────────────

    def test_default_thresholds_cover_all_colors(self):
        self.assertEqual(ALL_GLOW_COLORS, ("green", "yellow", "red"))
        for color in ALL_GLOW_COLORS:
            self.assertTrue(DEFAULT_GLOW_THRESHOLDS.ranges(color))

    def test_red_has_two_hsv_ranges(self):
        self.assertEqual(len(DEFAULT_GLOW_THRESHOLDS.ranges(GLOW_RED)), 2)
        self.assertEqual(len(DEFAULT_GLOW_THRESHOLDS.ranges(GLOW_GREEN)), 1)

    def test_unknown_color_raises(self):
        with self.assertRaises(ValueError):
            DEFAULT_GLOW_THRESHOLDS.ranges("purple")

    def test_thresholds_with_unknown_raises(self):
        with self.assertRaises(TypeError):
            DEFAULT_GLOW_THRESHOLDS.with_(no_such_field=1)

    def test_thresholds_are_immutable(self):
        """with_ 返回副本，不改动基线阈值。"""
        GlowThresholds().with_(min_area=999)
        self.assertEqual(DEFAULT_GLOW_THRESHOLDS.min_area, 60.0)
        self.assertTrue(DEFAULT_GLOW_THRESHOLDS.require_arc)

    def test_accepts_bgra_and_gray_frames(self):
        frame = _background()
        _arc(frame, GREEN_BGR)
        bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.assertTrue(self.detector.analyze(bgra, ROI_BOX).matched)
        gray_result = self.detector.analyze(gray, ROI_BOX)
        self.assertFalse(gray_result.matched, "灰度帧无色相信息，不应命中")

    def test_box_clamped_to_frame(self):
        """ROI 超出帧范围时不抛异常，只检测帧内部分。"""
        oversized = Box(1900, 1000, 200, 200)
        self.assertFalse(self.detector.analyze(_background(), oversized).matched)

    def test_fit_circle_recovers_known_circle(self):
        """拟合本身要准：给一段已知圆弧，还原出的圆心半径得对得上。"""
        pts = np.array(
            [
                [ARC_CENTER[0] + ARC_RADIUS * math.cos(math.radians(a)),
                 ARC_CENTER[1] + ARC_RADIUS * math.sin(math.radians(a))]
                for a in range(-60, 61, 5)
            ],
            dtype=np.float32,
        ).reshape(-1, 1, 2)
        cx, cy, radius, residual = GlowTargetDetector._fit_circle(pts)
        self.assertAlmostEqual(cx, ARC_CENTER[0], delta=1.0)
        self.assertAlmostEqual(cy, ARC_CENTER[1], delta=1.0)
        self.assertAlmostEqual(radius, ARC_RADIUS, delta=1.0)
        self.assertLess(residual, 0.5)


if __name__ == "__main__":
    unittest.main()
