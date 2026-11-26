"""跳过剧情按钮检测（含日服宽文本回归）单元测试。"""

import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from ok.feature.Box import relative_box

from src.core.base_game_task import SKIP_STORY_BOX, SKIP_STORY_BUTTON
from src.image.button_detector import ButtonDetector
from src.tasks.trigger.skip_dialog_task import SkipDialogTask

REPO_ROOT = Path(__file__).resolve().parent.parent
CN_SKIP_STORY_SCREENSHOT = REPO_ROOT / "ok_templates" / "screenshot_20260917_105823.png"
JP_SKIP_STORY_SCREENSHOT = REPO_ROOT / "ok_templates" / "screenshot_20261126_192226.png"
JP_CONFIRM_POPUP_SCREENSHOT = REPO_ROOT / "ok_templates" / "screenshot_20261126_191643.png"


def _make_skip_story_frame(
    width: int = 1920,
    height: int = 1080,
    text_width_ratio: float = 0.69,
    button_bgr: tuple[int, int, int] = (54, 51, 51),
    background_bgr: tuple[int, int, int] = (120, 100, 90),
    with_text: bool = True,
):
    """合成剧情梗概页面帧，在 SKIP_STORY_BOX 内绘制按钮与指定宽度的伪文字。"""
    frame = np.full((height, width, 3), background_bgr, dtype=np.uint8)
    box = relative_box(width, height, SKIP_STORY_BOX[0], SKIP_STORY_BOX[1], to_x=SKIP_STORY_BOX[2], to_y=SKIP_STORY_BOX[3])
    cv2.rectangle(
        frame,
        (box.x, box.y),
        (box.x + box.width - 1, box.y + box.height - 1),
        button_bgr,
        thickness=-1,
    )
    if with_text:
        text_w = max(20, int(box.width * text_width_ratio))
        text_h = max(10, int(box.height * 0.40))
        x0 = box.x + (box.width - text_w) // 2
        y0 = box.y + (box.height - text_h) // 2
        step = max(10, int(text_h * 0.85))
        glyph_w = max(6, int(step * 0.65))
        thickness = max(1, round(height / 720))
        for gx in range(x0, x0 + text_w - glyph_w + 1, step):
            cv2.rectangle(frame, (gx, y0), (gx + glyph_w, y0 + text_h), (255, 255, 255), thickness=thickness)
    return frame, box


def _build_skip_dialog_task(frame):
    """构造绑定固定帧的 SkipDialogTask 实例（仅用于单元测试）。"""
    executor = mock.MagicMock()
    executor.debug = False
    executor.frame = frame
    executor.pause_start = None
    height, width = frame.shape[:2]
    executor.method.width = width
    executor.method.height = height
    executor.device_manager.supported_ratio = 16 / 9
    task = SkipDialogTask(executor, mock.MagicMock())
    task.out_of_ratio = mock.Mock(return_value=False)
    task.next_frame = mock.Mock(return_value=frame)
    return task


class TestSkipStoryButtonThresholds(unittest.TestCase):
    """验证 SKIP_STORY_BOX + SKIP_STORY_BUTTON 对多语言文本宽度的兼容性。"""

    def setUp(self):
        self.detector = ButtonDetector(SKIP_STORY_BUTTON)

    def test_detects_short_cn_and_wide_jp_text(self):
        """短文本（国服 4 字 ~22% 宽）与宽文本（日服 10 字 ~69% 宽）均应稳定命中。"""
        for ratio in (0.22, 0.50, 0.69, 0.80):
            with self.subTest(text_width_ratio=ratio):
                frame, box = _make_skip_story_frame(text_width_ratio=ratio)
                detection = self.detector.analyze(frame, box)
                self.assertTrue(detection.matched, f"ratio={ratio} 未命中: {detection.failed} {detection.metrics}")
                self.assertEqual(detection.box.name, "skip_story")
                self.assertGreater(detection.metrics["center_ratio"], 0.80)

    def test_detects_wide_text_across_resolutions(self):
        """常见 16:9 分辨率下均应识别日服宽文本按钮。"""
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            with self.subTest(resolution=f"{width}x{height}"):
                frame, box = _make_skip_story_frame(width=width, height=height, text_width_ratio=0.69)
                detection = self.detector.analyze(frame, box)
                self.assertTrue(detection.matched, f"{width}x{height} 未命中: {detection.failed}")

    def test_rejects_light_backdrop_even_with_centered_text(self):
        """非深灰底色区域即使有亮色条带也应被底色校验拒绝。"""
        frame, box = _make_skip_story_frame(button_bgr=(165, 165, 165), text_width_ratio=0.69)
        detection = self.detector.analyze(frame, box)
        self.assertFalse(detection.matched)
        self.assertEqual(detection.failed, "backdrop_mismatch")

    def test_rejects_dark_button_without_text(self):
        """纯深灰底无文字时不应误判。"""
        frame, box = _make_skip_story_frame(with_text=False)
        detection = self.detector.analyze(frame, box)
        self.assertFalse(detection.matched)


class TestSkipDialogTaskSkipStoryIntegration(unittest.TestCase):
    """验证 SkipDialogTask.handle_dialog 与 BaseGameTask.find_confirm 的调用链路。"""

    def test_handle_dialog_clicks_jp_wide_skip_story_button(self):
        jp_frame, expected_box = _make_skip_story_frame(text_width_ratio=0.69)
        empty_frame, _ = _make_skip_story_frame(with_text=False)
        task = _build_skip_dialog_task(jp_frame)
        task.find_one = mock.Mock(return_value=None)
        task.click = mock.Mock()
        task.active_time = mock.Mock(side_effect=[0.0, 0.1, 1.2])
        task.loop = mock.Mock(return_value=iter([jp_frame, empty_frame]))

        task.handle_dialog()

        task.click.assert_called_once()
        clicked_box = task.click.call_args.args[0]
        self.assertEqual(clicked_box.name, "skip_story")
        self.assertEqual(
            (clicked_box.x, clicked_box.y, clicked_box.width, clicked_box.height),
            (expected_box.x, expected_box.y, expected_box.width, expected_box.height),
        )

    def test_find_confirm_detects_jp_wide_skip_story_button(self):
        jp_frame, _ = _make_skip_story_frame(text_width_ratio=0.69)
        task = _build_skip_dialog_task(jp_frame)
        task.find_one = mock.Mock(return_value=None)

        result = task.find_confirm()

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "skip_story")
        task.find_one.assert_not_called()


class TestSkipStoryRealScreenshots(unittest.TestCase):
    """使用仓库本地截图回归验证日服与国服跳过剧情按钮。"""

    @unittest.skipUnless(JP_SKIP_STORY_SCREENSHOT.exists(), "本地缺少日服截图样本")
    def test_jp_skip_story_screenshot_is_detected(self):
        frame = cv2.imread(str(JP_SKIP_STORY_SCREENSHOT))
        task = _build_skip_dialog_task(frame)

        result = task.find_skip_story_button(frame=frame)

        self.assertIsNotNone(result, "日服 screenshot_20261126_192226.png 跳过剧情按钮应被识别")
        self.assertEqual(result.name, "skip_story")
        cx, cy = result.center()
        self.assertTrue(1150 <= cx <= 1448 and 770 <= cy <= 826, f"点击中心 {(cx, cy)} 应落在按钮内")

    @unittest.skipUnless(CN_SKIP_STORY_SCREENSHOT.exists(), "本地缺少国服截图样本")
    def test_cn_skip_story_screenshot_is_detected(self):
        frame = cv2.imread(str(CN_SKIP_STORY_SCREENSHOT))
        task = _build_skip_dialog_task(frame)

        result = task.find_skip_story_button(frame=frame)

        self.assertIsNotNone(result, "国服 screenshot_20260917_105823.png 跳过剧情按钮应继续被识别")
        self.assertEqual(result.name, "skip_story")

    @unittest.skipUnless(JP_CONFIRM_POPUP_SCREENSHOT.exists(), "本地缺少日服二次确认弹窗样本")
    def test_jp_confirm_popup_is_not_misdetected_as_skip_story_button(self):
        frame = cv2.imread(str(JP_CONFIRM_POPUP_SCREENSHOT))
        task = _build_skip_dialog_task(frame)

        self.assertIsNone(task.find_skip_story_button(frame=frame))


if __name__ == "__main__":
    unittest.main()
