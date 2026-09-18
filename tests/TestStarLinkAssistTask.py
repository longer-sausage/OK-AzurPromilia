"""辅助星结触发任务测试。

框架调用（截图 / 点击 / 计时 / 覆盖层 / 特征匹配）全部用 stub 替换，
只验证「星结界面前置 → 检测 → 点击」这条链路的门槛逻辑；
检测本身跑在真实合成帧上，不做假。
"""

import unittest

import cv2
import numpy as np
from ok import Box

from src.data.FeatureList import FeatureList
from src.tasks.trigger.StarLinkAssistTask import StarLinkAssistTask

FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080
ROI = Box(907, 478, 123, 128)  # 归一化 (0.4724, 0.4426, 0.5365, 0.5611)

BACKGROUND_BGR = (30, 30, 30)
GREEN_BGR = (0, 255, 0)  # BGR，HSV 约 (60, 255, 255)
YELLOW_BGR = (0, 255, 255)  # BGR，HSV 约 (30, 255, 255)

# ROI 中心的一道可射击弧：半径 52、右侧 120°、弧宽 5（与真实样本同量级）
ARC_CENTER = (962, 542)
ARC_RADIUS = 52

DEFAULT_CONFIG = {
    "检测绿色": True,
    "检测黄色": True,
    "检测红色": True,
    "要求环形": True,
    "_最小面积": 60,
    "_连续命中帧数": 1,
    "_点击冷却(秒)": 0.35,
    "_按下时长(秒)": 0.01,
    "_点击后等待(秒)": 0.0,
    "记录点击日志": True,
    "画调试框": False,
}


def _make_frame(glow_color=None, solid=False):
    """合成一帧；``solid=True`` 时画实心色块（不该被弧形模式接受）。"""
    frame = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), BACKGROUND_BGR, dtype=np.uint8)
    if glow_color is None:
        return frame
    if solid:
        cv2.circle(frame, ARC_CENTER, ARC_RADIUS, glow_color, thickness=-1)
    else:
        cv2.ellipse(frame, ARC_CENTER, (ARC_RADIUS, ARC_RADIUS), 0, -60, 60,
                    glow_color, thickness=5)
    return frame


class TaskHarness:
    """把 StarLinkAssistTask 从框架里剥出来：假时钟 + 假截图 + 假点击 + 假特征匹配。"""

    def __init__(self, glow_color=None, solid=False, star_link=True, **config_overrides):
        self.glow_color = glow_color
        self.solid = solid
        self.star_link = star_link
        self.now = 0.0
        self.logs: list[str] = []
        self.clicks: list[tuple[int, int]] = []
        self.features: list[str] = []

        task = StarLinkAssistTask.__new__(StarLinkAssistTask)
        task.config = dict(DEFAULT_CONFIG)
        task.config.update(config_overrides)

        task._streak = 0
        task._last_click_at = None
        task._detector_cache = None
        task._detector_key = None
        task._roi_cache = None

        def _find_feature(feature_name, frame=None, **kwargs):
            # FeatureList 是 str Enum，取 .value 才是 'star_link_icon'
            self.features.append(getattr(feature_name, "value", str(feature_name)))
            return Box(0, 0, 1, 1) if self.star_link else None

        task.log_info = lambda message, notify=False: self.logs.append(str(message))
        task.active_time = lambda: self.now
        task.next_frame = lambda: _make_frame(self.glow_color, self.solid)
        task.click = lambda box, **kwargs: self.clicks.append(box.center())
        task.draw_boxes = lambda *args, **kwargs: None
        task.find_feature = _find_feature
        task.resolution_scale = lambda: 1.0
        task.box_of_screen = lambda x, y, to_x, to_y, name=None, **kwargs: Box(
            round(x * FRAME_WIDTH),
            round(y * FRAME_HEIGHT),
            round((to_x - x) * FRAME_WIDTH),
            round((to_y - y) * FRAME_HEIGHT),
            name=name,
        )

        self.task = task

    def advance(self, seconds):
        self.now += seconds

    def run(self, times=1, gap=0.1):
        for _ in range(times):
            self.task.run()
            self.advance(gap)


class TestStarLinkAssistTask(unittest.TestCase):
    # ── 1. 星结界面前置闸门 ─────────────────────────────────

    def test_no_click_when_star_link_icon_absent(self):
        """不在星结界面，就算有可射击光圈也不能点。"""
        harness = TaskHarness(GREEN_BGR, star_link=False)
        harness.run(times=3)
        self.assertEqual(harness.clicks, [])

    def test_clicks_when_star_link_icon_present(self):
        harness = TaskHarness(GREEN_BGR, star_link=True)
        harness.run()
        self.assertEqual(len(harness.clicks), 1)

    def test_gate_uses_star_link_icon_feature(self):
        """前置闸门查的必须是 star_link_icon，不是别的特征。"""
        harness = TaskHarness(GREEN_BGR)
        harness.run()
        self.assertEqual(harness.features, [FeatureList.star_link_icon.value])

    def test_gate_short_circuits_detection(self):
        """闸门没过时不该再跑检测：省掉一整轮 HSV + 拟合。"""
        harness = TaskHarness(GREEN_BGR, star_link=False)
        harness.run()
        self.assertEqual(harness.features, [FeatureList.star_link_icon.value])
        self.assertEqual(harness.clicks, [])

    # ── 2. 命中即点击 ───────────────────────────────────────

    def test_clicks_once_on_green_arc(self):
        """点击位置必须是拟合圆心（准心），不是弧的外接框中心。"""
        harness = TaskHarness(GREEN_BGR)
        harness.run()
        self.assertEqual(len(harness.clicks), 1)
        cx, cy = harness.clicks[0]
        self.assertAlmostEqual(cx, ARC_CENTER[0], delta=8)
        self.assertAlmostEqual(cy, ARC_CENTER[1], delta=8)

    def test_clicks_on_yellow_arc(self):
        harness = TaskHarness(YELLOW_BGR)
        harness.run()
        self.assertEqual(len(harness.clicks), 1)
        self.assertIn("黄色可射击光圈", harness.logs[0])

    def test_solid_blob_not_clicked_by_default(self):
        """实心色块不是光环，默认不该点（避免误点中心区域的彩色 UI）。"""
        harness = TaskHarness(GREEN_BGR, solid=True)
        harness.run(times=3)
        self.assertEqual(harness.clicks, [])

    def test_solid_blob_clicked_when_ring_not_required(self):
        harness = TaskHarness(GREEN_BGR, solid=True, **{"要求环形": False})
        harness.run()
        self.assertEqual(len(harness.clicks), 1)

    def test_no_click_without_glow(self):
        harness = TaskHarness()
        harness.run(times=5)
        self.assertEqual(harness.clicks, [])

    # ── 3. 冷却 ─────────────────────────────────────────────

    def test_cooldown_prevents_click_storm(self):
        harness = TaskHarness(GREEN_BGR, **{"_点击冷却(秒)": 0.35})
        harness.run(times=5, gap=0.1)  # 第 0s / 0.4s 各一次，中间 3 次被冷却挡掉
        self.assertEqual(len(harness.clicks), 2)

    def test_clicks_again_after_cooldown(self):
        harness = TaskHarness(GREEN_BGR, **{"_点击冷却(秒)": 0.2})
        harness.run()
        harness.advance(0.5)
        harness.run()
        self.assertEqual(len(harness.clicks), 2)

    # ── 4. 连续命中帧数 ─────────────────────────────────────

    def test_streak_gate_delays_first_click(self):
        harness = TaskHarness(GREEN_BGR, **{"_连续命中帧数": 3, "_点击冷却(秒)": 0.0})
        harness.run(times=2)
        self.assertEqual(harness.clicks, [], "两帧未达阈值，不该点击")
        harness.run()
        self.assertEqual(len(harness.clicks), 1)

    def test_streak_resets_when_glow_disappears(self):
        harness = TaskHarness(GREEN_BGR, **{"_连续命中帧数": 3, "_点击冷却(秒)": 0.0})
        harness.run()
        harness.glow_color = None  # 光效消失
        harness.run()
        harness.glow_color = GREEN_BGR
        harness.run()
        self.assertEqual(harness.clicks, [], "中断后重新计数，尚未达到 3 帧")

    # ── 5. 颜色开关 ─────────────────────────────────────────

    def test_disabled_color_is_not_clicked(self):
        harness = TaskHarness(YELLOW_BGR, **{"检测黄色": False})
        harness.run()
        self.assertEqual(harness.clicks, [])

    def test_enabled_color_is_clicked(self):
        harness = TaskHarness(YELLOW_BGR, **{"检测绿色": False, "检测红色": False})
        harness.run()
        self.assertEqual(len(harness.clicks), 1)

    # ── 6. 其他 ─────────────────────────────────────────────

    def test_no_frame_is_ignored(self):
        harness = TaskHarness(GREEN_BGR)
        harness.task.next_frame = lambda: None
        harness.run()
        self.assertEqual(harness.clicks, [])

    def test_roi_matches_configured_region(self):
        harness = TaskHarness()
        self.assertEqual(
            (harness.task._roi().x, harness.task._roi().y,
             harness.task._roi().width, harness.task._roi().height),
            (ROI.x, ROI.y, ROI.width, ROI.height),
        )


if __name__ == "__main__":
    unittest.main()
