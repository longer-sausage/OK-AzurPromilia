import unittest
from pathlib import Path

import cv2
import numpy as np
from ok import Box

import src.image.button_detector as detector_module
from src.image.button_detector import (
    DARK_BUTTON_THRESHOLDS,
    DEFAULT_BUTTON_THRESHOLDS,
    LIGHT_BUTTON_THRESHOLDS,
    ButtonDetector,
    ButtonThresholds,
)

# ── 样本参数取自「跳过剧情」实际截图（589x264） ─────────────────
# 按钮 RGB(50,50,53) -> BGR(53,50,50)；文字亮白；文字 bbox ≈ (222,119)-(318,140)
FRAME_WIDTH, FRAME_HEIGHT = 589, 264
DARK_BGR = (53, 50, 50)  # 按钮与背景都用它，用于「颜色接近」用例
TEXT_BGR = (255, 255, 255)
BUTTON_BOX = Box(53, 101, 435, 58)  # x=53 y=101 w=435 h=58
GLYPH_CELL = 20
GLYPH_GAP = 6
GLYPH_COUNT = 4


def _background(color=DARK_BGR, noise=5, seed=7):
    """带轻微噪点的纯色背景，模拟真实游戏画面（噪点不会达到亮色阈值）。"""
    frame = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), color, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    noisy = frame.astype(np.int16) + rng.integers(-noise, noise + 1, frame.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def _draw_button(frame, box, color=DARK_BGR):
    cv2.rectangle(frame, (box.x, box.y), (box.x + box.width - 1, box.y + box.height - 1), color, thickness=-1)


def _draw_glyphs(frame, box, color=TEXT_BGR, count=GLYPH_COUNT, cell=GLYPH_CELL, gap=GLYPH_GAP, thickness=2):
    """在 Box 正中画一排「日」字形伪汉字，模拟按钮中央的亮色文本。"""
    total_width = count * cell + (count - 1) * gap
    x0 = box.x + (box.width - total_width) // 2
    y0 = box.y + (box.height - cell) // 2
    for index in range(count):
        left = x0 + index * (cell + gap)
        cv2.rectangle(frame, (left, y0), (left + cell - 1, y0 + cell - 1), color, thickness=thickness)
        cv2.line(frame, (left, y0 + cell // 2), (left + cell - 1, y0 + cell // 2), color, thickness=thickness)
    return Box(x0, y0, total_width, cell)


def _button_frame(background_color=DARK_BGR, button_color=DARK_BGR, text_color=TEXT_BGR, with_text=True):
    frame = _background(background_color)
    _draw_button(frame, BUTTON_BOX, button_color)
    text_box = _draw_glyphs(frame, BUTTON_BOX, text_color) if with_text else None
    return frame, text_box


# 亮色按钮：白/浅灰底 + 深色文字
LIGHT_BGR = (230, 230, 230)
LIGHT_TEXT_BGR = (30, 30, 30)


class TestButtonDetector(unittest.TestCase):
    def setUp(self):
        self.detector = ButtonDetector()

    # ── 1. 有按钮 ──────────────────────────────────────────
    def test_detect_button_with_center_text(self):
        frame, _ = _button_frame()
        result = self.detector.find(frame, BUTTON_BOX)
        self.assertIsNotNone(result, "按钮中央有亮色文本时应命中")
        self.assertEqual((result.x, result.y, result.width, result.height),
                         (BUTTON_BOX.x, BUTTON_BOX.y, BUTTON_BOX.width, BUTTON_BOX.height))
        self.assertEqual(result.name, DEFAULT_BUTTON_THRESHOLDS.box_name)
        self.assertGreater(result.confidence, 0.5)

    def test_text_box_matches_drawn_text(self):
        """检测到的文本带应落在真实文字区域内。"""
        frame, text_box = _button_frame()
        detection = self.detector.analyze(frame, BUTTON_BOX)
        self.assertTrue(detection.matched)
        self.assertTrue(
            text_box.x - 5 <= detection.text_box.x
            and detection.text_box.x + detection.text_box.width <= text_box.x + text_box.width + 5,
            f"文本带 {detection.text_box} 应落在 {text_box} 内",
        )

    def test_result_center_is_inside_button(self):
        """返回的 Box 必须可直接交给 click()。"""
        frame, _ = _button_frame()
        result = self.detector.find(frame, BUTTON_BOX)
        center_x, center_y = result.center()
        self.assertGreaterEqual(center_x, BUTTON_BOX.x)
        self.assertLessEqual(center_x, BUTTON_BOX.x + BUTTON_BOX.width)
        self.assertGreaterEqual(center_y, BUTTON_BOX.y)
        self.assertLessEqual(center_y, BUTTON_BOX.y + BUTTON_BOX.height)

    # ── 2. 无按钮 ──────────────────────────────────────────
    def test_no_button_plain_background(self):
        self.assertIsNone(self.detector.find(_background(), BUTTON_BOX), "只有背景时不应命中")

    def test_no_button_darker_background(self):
        frame = _background(color=(32, 30, 30))
        self.assertIsNone(self.detector.find(frame, BUTTON_BOX))

    # ── 3. 背景颜色与按钮接近 ──────────────────────────────
    def test_same_color_background_with_text_is_detected(self):
        """按钮与背景完全同色时，仍应靠中央亮色文本检出。"""
        frame, _ = _button_frame(background_color=DARK_BGR, button_color=DARK_BGR)
        self.assertIsNotNone(self.detector.find(frame, BUTTON_BOX))

    def test_same_color_background_without_text_is_not_detected(self):
        """同色且没有文本时，不能仅凭深灰底色判定。"""
        frame, _ = _button_frame(background_color=DARK_BGR, button_color=DARK_BGR, with_text=False)
        detection = self.detector.analyze(frame, BUTTON_BOX)
        self.assertFalse(detection.matched, "没有亮色文本时不应命中")
        self.assertTrue(detection.failed)

    def test_almost_identical_button_color_is_detected(self):
        """按钮与背景只差 1~2 个色阶时依旧靠文本检出。"""
        frame, _ = _button_frame(background_color=DARK_BGR, button_color=(55, 52, 52))
        self.assertIsNotNone(self.detector.find(frame, BUTTON_BOX))

    # ── 4. 中央没有亮色文本 ────────────────────────────────
    def test_button_without_center_text(self):
        frame, _ = _button_frame(with_text=False)
        self.assertIsNone(self.detector.find(frame, BUTTON_BOX))

    def test_dark_text_is_not_detected(self):
        """暗色文字（V<170）不构成亮色结构。"""
        frame, _ = _button_frame(text_color=(120, 120, 120))
        self.assertIsNone(self.detector.find(frame, BUTTON_BOX))

    # ── 5. Box 内存在少量无关亮色区域 ───────────────────────
    def test_stray_bright_pixels_are_ignored(self):
        frame, _ = _button_frame(with_text=False)
        for offset in (10, 60, 120):
            cv2.rectangle(frame, (60 + offset, 108), (66 + offset, 114), TEXT_BGR, thickness=-1)
        detection = self.detector.analyze(frame, BUTTON_BOX)
        self.assertFalse(detection.matched, "零散亮色像素不应命中")
        self.assertIn(detection.failed, {"not_enough_text_pixels", "no_text_row", "text_not_centered"})

    def test_bright_band_at_box_edge_is_not_detected(self):
        """亮色横条贴在 Box 边缘（不是按钮中央文本）时应拒绝。"""
        frame, _ = _button_frame(with_text=False)
        cv2.rectangle(frame, (80, 104), (420, 110), TEXT_BGR, thickness=-1)
        cv2.rectangle(frame, (80, 152), (420, 158), TEXT_BGR, thickness=-1)
        detection = self.detector.analyze(frame, BUTTON_BOX)
        self.assertFalse(detection.matched)
        self.assertIn(detection.failed, {"not_enough_text_pixels", "no_text_row", "text_not_centered"})

    def test_solid_bright_block_is_not_detected(self):
        """整块高亮面板不是文本。"""
        frame, _ = _button_frame(with_text=False)
        cx0, cx1 = 53 + 130, 53 + 305
        cy0, cy1 = 101 + 13, 101 + 46
        cv2.rectangle(frame, (cx0, cy0), (cx1, cy1), TEXT_BGR, thickness=-1)
        detection = self.detector.analyze(frame, BUTTON_BOX)
        self.assertFalse(detection.matched)
        self.assertEqual(detection.failed, "bad_band_density")

    def test_bright_text_outside_box_is_ignored(self):
        """只检测传入的 Box，Box 外的亮色文本不影响结果。"""
        frame, _ = _button_frame(with_text=False)
        _draw_glyphs(frame, Box(53, 10, 435, 40))
        _draw_glyphs(frame, Box(53, 200, 435, 40))
        self.assertIsNone(self.detector.find(frame, BUTTON_BOX))

    def test_detector_only_inspects_requested_box(self):
        """按钮在别处时，传错 Box 不应命中。"""
        frame, _ = _button_frame()
        self.assertIsNone(self.detector.find(frame, Box(53, 10, 435, 40)))

    # ── 6. 异常输入不抛异常 ────────────────────────────────
    def test_invalid_inputs_return_empty_result(self):
        frame, _ = _button_frame()
        self.assertIsNone(self.detector.find(None, BUTTON_BOX))
        self.assertIsNone(self.detector.find(frame, None))
        self.assertIsNone(self.detector.find(frame, Box(0, 0, 3, 3)), "ROI 过小应直接判未命中")
        self.assertIsNone(self.detector.find(frame, Box(5000, 5000, 100, 50)), "越界 Box 应安全返回")
        self.assertIsNone(self.detector.find(frame, Box(-200, -200, 300, 300)), "负坐标 Box 应安全返回")

    def test_oversized_box_is_clamped(self):
        frame, _ = _button_frame()
        result = self.detector.find(frame, Box(-50, -50, FRAME_WIDTH + 200, FRAME_HEIGHT + 200))
        self.assertIsNone(result, "Box 远大于按钮时文本带过薄，应判未命中")

    # ── 7. 阈值集中管理 ────────────────────────────────────
    def test_thresholds_override_does_not_mutate_defaults(self):
        custom = DEFAULT_BUTTON_THRESHOLDS.with_(min_text_px=1)
        self.assertEqual(custom.min_text_px, 1)
        self.assertEqual(DEFAULT_BUTTON_THRESHOLDS.min_text_px, ButtonThresholds().min_text_px)

    def test_custom_thresholds_are_used(self):
        frame, _ = _button_frame()
        strict = ButtonThresholds(min_text_ratio=0.9)  # 不可能满足的数量下限
        self.assertIsNone(ButtonDetector(strict).find(frame, BUTTON_BOX))

    # ── 8. 底色为可选辅助特征 ──────────────────────────────
    def test_dark_backdrop_accepts_dark_button(self):
        frame, _ = _button_frame()
        detector = ButtonDetector(DEFAULT_BUTTON_THRESHOLDS.with_(require_backdrop=True))
        self.assertIsNotNone(detector.find(frame, BUTTON_BOX))

    def test_dark_backdrop_rejects_light_panel(self):
        """亮底 + 亮字：主特征满足，但底色辅助条件不满足时应拒绝。"""
        frame, _ = _button_frame(button_color=(150, 150, 150))
        self.assertIsNotNone(self.detector.find(frame, BUTTON_BOX), "默认只靠中央亮色文本即可命中")
        detector = ButtonDetector(DEFAULT_BUTTON_THRESHOLDS.with_(require_backdrop=True))
        detection = detector.analyze(frame, BUTTON_BOX)
        self.assertFalse(detection.matched)
        self.assertEqual(detection.failed, "backdrop_mismatch")

    # ── 9. 不使用 OCR ──────────────────────────────────────
    def test_detector_does_not_use_ocr_or_template_matching(self):
        source = Path(detector_module.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("onnxocr", "ok.ocr", "paddleocr", "cv2.matchtemplate", "find_feature("):
            self.assertNotIn(forbidden, source, f"检测器不应依赖 {forbidden}")

    # ── 10. 结果信息完整 ───────────────────────────────────
    def test_analyze_returns_metrics_and_failure_reason(self):
        hit_frame, _ = _button_frame()
        hit = self.detector.analyze(hit_frame, BUTTON_BOX)
        self.assertEqual(hit.failed, "")
        for key in ("text_center", "band_width", "band_height", "band_density", "centroid_x", "confidence"):
            self.assertIn(key, hit.metrics)

        miss_frame, _ = _button_frame(with_text=False)
        miss = self.detector.analyze(miss_frame, BUTTON_BOX)
        self.assertFalse(miss.matched)
        self.assertIsNone(miss.box)
        self.assertTrue(miss.failed)

    def test_default_detector_is_reusable(self):
        """同一实例可连续处理多帧，结果稳定。"""
        frame, _ = _button_frame()
        results = [self.detector.find(frame, BUTTON_BOX) for _ in range(3)]
        self.assertTrue(all(results))
        self.assertEqual(results[0], results[1])


class TestThresholdPresets(unittest.TestCase):
    """泛化：文字与底色的颜色都是参数，深灰按钮只是其中一个预设。"""

    def setUp(self):
        self.default = ButtonDetector()
        self.light = ButtonDetector(LIGHT_BUTTON_THRESHOLDS)

    def test_for_dark_button_matches_defaults(self):
        self.assertEqual(ButtonThresholds.for_dark_button(), DEFAULT_BUTTON_THRESHOLDS)
        self.assertIs(DARK_BUTTON_THRESHOLDS, DEFAULT_BUTTON_THRESHOLDS)

    def test_light_preset_detects_dark_text_on_light_button(self):
        """亮色按钮 + 深色文字：换成亮色预设即可命中，不需要改算法。"""
        frame, _ = _button_frame(background_color=(60, 58, 58), button_color=LIGHT_BGR, text_color=LIGHT_TEXT_BGR)
        self.assertIsNone(self.default.find(frame, BUTTON_BOX), "深灰预设不应把亮底深字判为命中")
        result = self.light.find(frame, BUTTON_BOX)
        self.assertIsNotNone(result, "亮色预设应命中亮底深字")
        self.assertEqual(result.name, "light_button")

    def test_light_preset_ignores_button_without_text(self):
        frame, _ = _button_frame(background_color=(60, 58, 58), button_color=LIGHT_BGR, with_text=False)
        self.assertIsNone(self.light.find(frame, BUTTON_BOX))

    def test_light_preset_ignores_dark_button(self):
        """深灰按钮的亮字不满足「深色文字」范围，亮色预设不应命中它。"""
        frame, _ = _button_frame()
        self.assertIsNone(self.light.find(frame, BUTTON_BOX))

    def test_light_preset_backdrop_check_can_be_enabled(self):
        frame, _ = _button_frame(background_color=(60, 58, 58), button_color=LIGHT_BGR, text_color=LIGHT_TEXT_BGR)
        strict = ButtonDetector(LIGHT_BUTTON_THRESHOLDS.with_(require_backdrop=True))
        self.assertIsNotNone(strict.find(frame, BUTTON_BOX))

        # 中灰底 + 深字：文字结构成立，但底色不亮 → 应被底色校验拒绝
        mid_gray, _ = _button_frame(button_color=(130, 130, 130), text_color=LIGHT_TEXT_BGR)
        relaxed = ButtonDetector(LIGHT_BUTTON_THRESHOLDS.with_(require_backdrop=False))
        self.assertIsNotNone(relaxed.find(mid_gray, BUTTON_BOX), "不校验底色时文字结构本身是成立的")
        detection = strict.analyze(mid_gray, BUTTON_BOX)
        self.assertFalse(detection.matched)
        self.assertEqual(detection.failed, "backdrop_mismatch")

    def test_for_button_accepts_arbitrary_text_color(self):
        """金色文字：只换文字颜色区间即可。"""
        frame, _ = _button_frame(text_color=(60, 200, 240))  # BGR -> 金色
        gold = ButtonDetector(ButtonThresholds.for_button(((15, 40, 180), (60, 255, 255)), name="gold_button"))
        self.assertIsNotNone(gold.find(frame, BUTTON_BOX))
        self.assertIsNone(self.default.find(frame, BUTTON_BOX), "默认(亮色低饱和)区间不应命中饱和金色文字")

    def test_for_button_can_set_backdrop(self):
        frame, _ = _button_frame()
        thresholds = ButtonThresholds.for_button(
            ((0, 0, 170), (180, 100, 255)),
            ((0, 0, 30), (180, 80, 110)),
            name="skip_button",
        )
        self.assertTrue(thresholds.require_backdrop)
        self.assertIsNotNone(ButtonDetector(thresholds).find(frame, BUTTON_BOX))

    def test_with_overrides_single_field(self):
        custom = DEFAULT_BUTTON_THRESHOLDS.with_(text_lower=(0, 0, 1), require_backdrop=True)
        self.assertEqual(custom.text_lower, (0, 0, 1))
        self.assertTrue(custom.require_backdrop)
        self.assertEqual(custom.text_upper, DEFAULT_BUTTON_THRESHOLDS.text_upper)

    def test_with_rejects_unknown_names(self):
        with self.assertRaises(TypeError):
            DEFAULT_BUTTON_THRESHOLDS.with_(does_not_exist=1)

    def test_presets_do_not_mutate_defaults(self):
        ButtonThresholds.for_light_button()
        ButtonThresholds.for_button(((0, 0, 0), (180, 255, 90)))
        self.assertEqual(DEFAULT_BUTTON_THRESHOLDS, ButtonThresholds())
        self.assertNotEqual(LIGHT_BUTTON_THRESHOLDS.text_lower, DEFAULT_BUTTON_THRESHOLDS.text_lower)


class TestButtonMixin(unittest.TestCase):
    """RuntimeMixin 暴露的检测入口（与 find_feature / find_one 风格一致）。"""

    def setUp(self):
        try:
            from src.core.base_mixin.runtime_mixin import RuntimeMixin
        except Exception as error:  # pragma: no cover - 环境缺少 GUI 依赖时跳过
            self.skipTest(f"RuntimeMixin 不可用: {error}")

        class _StubTask(RuntimeMixin):
            def __init__(self, frame):
                self._frame = frame

            def next_frame(self):
                return self._frame

        self.frame, _ = _button_frame()
        self.task = _StubTask(self.frame)

    def test_find_button_returns_clickable_box(self):
        result = self.task.find_button(BUTTON_BOX)
        self.assertIsNotNone(result)
        self.assertEqual((result.x, result.y), (BUTTON_BOX.x, BUTTON_BOX.y))

    def test_find_button_uses_current_frame(self):
        self.task._frame = _background()
        self.assertIsNone(self.task.find_button(BUTTON_BOX))

    def test_find_buttons_returns_first_hit(self):
        result = self.task.find_buttons([Box(0, 0, 50, 50), BUTTON_BOX])
        self.assertIsNotNone(result)
        self.assertEqual(result.x, BUTTON_BOX.x)

    def test_find_buttons_returns_none_when_all_miss(self):
        self.task._frame = _background()
        self.assertIsNone(self.task.find_buttons([Box(0, 0, 50, 50), BUTTON_BOX]))

    def test_detector_instance_is_cached(self):
        self.assertIs(self.task.button_detector(), self.task.button_detector())
        custom = ButtonThresholds()
        self.assertIsNot(self.task.button_detector(custom), self.task.button_detector())

    def test_analyze_button_exposes_metrics(self):
        detection = self.task.analyze_button(BUTTON_BOX)
        self.assertTrue(detection.matched)
        self.assertIn("band_density", detection.metrics)

    # ── 两个核心参数：text_hsv / backdrop_hsv ────────────────
    def test_text_hsv_parameter_overrides_preset(self):
        """只传文字颜色即可改检测目标，不用先构造阈值对象。"""
        gold_frame, _ = _button_frame(text_color=(60, 200, 240))
        self.task._frame = gold_frame
        self.assertIsNone(self.task.find_button(BUTTON_BOX), "默认亮色低饱和区间不应命中金色文字")
        result = self.task.find_button(BUTTON_BOX, text_hsv=((15, 40, 180), (60, 255, 255)))
        self.assertIsNotNone(result)

    def test_backdrop_hsv_parameter_enables_backdrop_check(self):
        """传了底色区间就应该启用底色校验。"""
        detection = self.task.analyze_button(
            BUTTON_BOX,
            text_hsv=((0, 0, 170), (180, 100, 255)),
            backdrop_hsv=((0, 0, 30), (180, 80, 110)),
        )
        self.assertTrue(detection.matched)
        self.assertIn("backdrop_ratio", detection.metrics)

    def test_backdrop_hsv_parameter_rejects_mismatched_backdrop(self):
        """底色不对时，即使文字结构成立也应拒绝。"""
        result = self.task.find_button(
            BUTTON_BOX,
            text_hsv=((0, 0, 170), (180, 100, 255)),
            backdrop_hsv=((0, 0, 170), (180, 80, 255)),  # 要求亮底，实际是深灰底
        )
        self.assertIsNone(result)

    def test_require_backdrop_can_be_disabled_explicitly(self):
        detection = self.task.analyze_button(
            BUTTON_BOX,
            text_hsv=((0, 0, 170), (180, 100, 255)),
            backdrop_hsv=((0, 0, 170), (180, 80, 255)),
            require_backdrop=False,
        )
        self.assertTrue(detection.matched)
        self.assertNotIn("backdrop_ratio", detection.metrics)

    def test_semantic_threshold_constant_is_reusable(self):
        """封装成语义常量后可在多处复用。"""
        skip_button = ButtonThresholds.for_button(
            ((0, 0, 170), (180, 100, 255)),
            ((0, 0, 30), (180, 80, 110)),
            name="skip_button",
        )
        result = self.task.find_button(BUTTON_BOX, thresholds=skip_button)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "skip_button")

    def test_find_button_with_light_preset(self):
        light_frame, _ = _button_frame(background_color=(60, 58, 58), button_color=LIGHT_BGR, text_color=LIGHT_TEXT_BGR)
        self.task._frame = light_frame
        self.assertIsNone(self.task.find_button(BUTTON_BOX))
        result = self.task.find_button(BUTTON_BOX, thresholds=LIGHT_BUTTON_THRESHOLDS)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "light_button")

    def test_find_buttons_with_custom_preset(self):
        light_frame, _ = _button_frame(background_color=(60, 58, 58), button_color=LIGHT_BGR, text_color=LIGHT_TEXT_BGR)
        self.task._frame = light_frame
        result = self.task.find_buttons([Box(0, 0, 50, 50), BUTTON_BOX], thresholds=LIGHT_BUTTON_THRESHOLDS)
        self.assertIsNotNone(result)
        self.assertEqual(result.x, BUTTON_BOX.x)

    def test_analyze_button_with_custom_preset(self):
        light_frame, _ = _button_frame(background_color=(60, 58, 58), button_color=LIGHT_BGR, text_color=LIGHT_TEXT_BGR)
        self.task._frame = light_frame
        detection = self.task.analyze_button(BUTTON_BOX, thresholds=LIGHT_BUTTON_THRESHOLDS)
        self.assertTrue(detection.matched)
        self.assertIn("band_density", detection.metrics)


if __name__ == "__main__":
    unittest.main()
