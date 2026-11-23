"""宝箱开锁任务状态机测试。

框架调用（截图 / 模板匹配 / 点击 / 计时）全部用 stub 替换，
只验证状态机本身的推进逻辑；条带检测跑在真实合成帧上，不做假。
"""

import unittest

import cv2
import numpy as np
from ok import Box

from src.data.feature_list import FeatureList
from src.image.treasure_band_detector import TreasureBandDetector
from src.tasks.trigger.treasure_unlock_task import TreasureUnlockTask

FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080
ROI = Box(1305, 251, 43, 589)

BAND_BGR = (225, 225, 200)  # 低饱和高亮
RAIL_BGR = (40, 35, 30)  # 深色轨道背景
KEY_BGR = (30, 60, 90)  # 钥匙（深色，不落在颜色带区间内）

# 三条颜色带，与真实截图一致
BANDS = [(1317, 286, 16, 105), (1316, 552, 19, 53), (1316, 672, 19, 53)]
TREASURE_ICON = Box(918, 335, 85, 126)


def _make_frame(bands, key_center_y=None):
    """合成一帧：深色轨道 + 若干颜色带 + 可选的横向钥匙。"""
    frame = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), RAIL_BGR, dtype=np.uint8)
    for x, y, w, h in bands:
        cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), BAND_BGR, thickness=-1)
    if key_center_y is not None:
        # 钥匙横跨轨道，会压住条带中段（模拟真实遮挡）
        cv2.rectangle(frame, (1238, key_center_y - 9), (1274, key_center_y + 9), KEY_BGR, thickness=-1)
    return frame


class FakeGame:
    """可控的假游戏状态：谁在画面上、点了之后消不消失。"""

    def __init__(self, bands=None):
        self.bands = list(bands if bands is not None else BANDS)
        self.key_center_y = None
        self.has_treasure = True
        self.clicks: list[int] = []
        self.remove_on_click = True

    def frame(self):
        return _make_frame(self.bands, self.key_center_y)

    def click(self, box):
        self.clicks.append(box.y)
        if not self.remove_on_click:
            return
        for band in list(self.bands):
            if band[1] == box.y:
                self.bands.remove(band)
                return


class TaskHarness:
    """把 TreasureUnlockTask 从框架里剥出来：假时钟 + 假截图 + 假点击。"""

    def __init__(self, game: FakeGame, **config_overrides):
        self.game = game
        self.now = 0.0
        self.logs: list[str] = []

        task = TreasureUnlockTask.__new__(TreasureUnlockTask)
        task.config = {
            "_校准稳定帧数": 5,
            "_校准位置容差": 3,
            "_校准超时(秒)": 8.0,
            "_消失确认时长(秒)": 0.35,
            "_消失确认超时(秒)": 3.0,
            "_条带存在阈值": 0.15,
            "_完成确认时长(秒)": 2.5,
            "_点击重试上限": 3,
            "_钥匙丢失超时(秒)": 3.0,
            "_单次运行时长上限(秒)": 25.0,
            "_检测间隔(秒)": 0.08,
            "画调试框": False,
        }
        task.config.update(config_overrides)

        task._detector_cache = None
        task._roi_cache = None
        task._key_box_cache = None
        task._reset()

        task.log_info = lambda message, notify=False: self.logs.append(str(message))
        task.log_warning = lambda message, notify=False: self.logs.append(str(message))
        task.active_time = lambda: self.now
        task.sleep = lambda t: self._advance(t)
        task.next_frame = lambda: game.frame()
        task.find_one = self._find_one
        task.click = lambda box, **kw: game.click(box)
        task.draw_boxes = lambda *a, **kw: None
        task.box_of_screen = lambda x, y, to_x, to_y, name=None, **kw: Box(
            round(x * FRAME_WIDTH), round(y * FRAME_HEIGHT),
            round((to_x - x) * FRAME_WIDTH), round((to_y - y) * FRAME_HEIGHT), name=name)
        task.scale_distance = lambda value, minimum=1: max(minimum, int(value))
        task.resolution_scale = lambda: 1.0
        task.wait_until = self._wait_until

        self.task = task

    # ── 假计时 / 假等待 ──

    def _advance(self, seconds):
        self.now += seconds

    def _wait_until(self, condition, time_out=0, settle_time=-1, **_kwargs):
        """简化语义：条件成立后连续两次成立才算稳定（对应 settle_time > 0）。"""
        if not condition():
            return None
        if settle_time and settle_time > 0 and not condition():
            return None
        return True

    # ── 假模板匹配 ──

    def _find_one(self, feature=None, frame=None, box=None, **_kwargs):
        name = str(getattr(feature, "value", feature))
        if name == FeatureList.treasure_icon.value:
            return TREASURE_ICON if self.game.has_treasure else None
        if name == FeatureList.treasure_key_icon.value:
            if self.game.key_center_y is None:
                return None
            return Box(1243, self.game.key_center_y - 14, 21, 27)
        return None

    # ── 驱动 ──

    def step(self, times=1):
        for _ in range(times):
            self.task._step(self.game.frame())
            self._advance(0.08)

    def run_once(self):
        self.task.run()

    def calibrate(self):
        """把状态推进到 UNLOCKING（喂够稳定帧）。"""
        self.step()
        self.step(int(self.task.config["_校准稳定帧数"]))
        return self.task._state


class TestCalibration(unittest.TestCase):
    """重点 1：初始条带校准稳定。"""

    def setUp(self):
        self.game = FakeGame()
        self.h = TaskHarness(self.game)

    def test_icon_starts_calibration(self):
        self.h.step()
        self.assertEqual(self.h.task._state, TreasureUnlockTask.CALIBRATING_BANDS)

    def test_no_icon_stays_in_wait(self):
        self.game.has_treasure = False
        self.h.step()
        self.assertEqual(self.h.task._state, TreasureUnlockTask.WAIT_TREASURE)

    def test_calibration_needs_enough_stable_frames(self):
        need = int(self.h.task.config["_校准稳定帧数"])
        self.h.step()  # 进入校准
        for i in range(need - 1):
            self.h.step()
            self.assertEqual(self.h.task._state, TreasureUnlockTask.CALIBRATING_BANDS,
                             f"第 {i + 1} 帧还不该完成校准")
        self.h.step()
        self.assertEqual(self.h.task._state, TreasureUnlockTask.UNLOCKING)

    def test_calibration_caches_all_bands(self):
        self.h.calibrate()
        self.assertEqual(len(self.h.task._calibrated_bands), 3)
        self.assertEqual([b.y for b in self.h.task._calibrated_bands], [286, 552, 672])
        self.assertEqual(len(self.h.task._active_bands), 3)

    def test_band_count_change_resets_stability(self):
        """数量变了必须从头数稳定帧，不能沿用之前的 streak。"""
        self.h.step(4)
        self.game.bands.append((1316, 800, 19, 40))
        self.h.step(4)
        self.assertEqual(self.h.task._state, TreasureUnlockTask.CALIBRATING_BANDS,
                         "数量变化后稳定计数应重置")

    def test_band_position_drift_resets_stability(self):
        """位置漂移超过容差 → 重置。"""
        self.h.step(4)
        self.game.bands[0] = (1317, 296, 16, 105)  # Y 从 286 → 296，超出容差 3
        self.h.step(4)
        self.assertEqual(self.h.task._state, TreasureUnlockTask.CALIBRATING_BANDS)

    def test_small_jitter_within_tolerance_is_stable(self):
        """2px 抖动属于合理误差，不应打断校准。"""
        self.h.step(2)
        self.game.bands[0] = (1317, 288, 16, 105)  # Y 286 → 288，容差内
        self.assertEqual(self.h.calibrate(), TreasureUnlockTask.UNLOCKING)

    def test_calibration_timeout_returns_to_wait(self):
        self.h.step()
        self.game.bands = []  # 一直检测不到条带 → 必然超时
        for _ in range(200):
            self.h.step()
            if self.h.task._state == TreasureUnlockTask.WAIT_TREASURE:
                break
        self.assertEqual(self.h.task._state, TreasureUnlockTask.WAIT_TREASURE)


class TestUnlocking(unittest.TestCase):
    """重点 2：钥匙 Y 与缓存条带可靠匹配。"""

    def setUp(self):
        self.game = FakeGame()
        self.h = TaskHarness(self.game)
        self.assertEqual(self.h.calibrate(), TreasureUnlockTask.UNLOCKING)

    def test_clicks_band_containing_key_center(self):
        self.game.key_center_y = 580  # 落在 552~605
        self.h.step()
        self.assertEqual(self.game.clicks, [552])

    def test_clicks_middle_band_only(self):
        self.game.key_center_y = 300  # 落在 286~391
        self.h.step()
        self.assertEqual(self.game.clicks, [286])

    def test_waits_when_key_between_bands(self):
        self.game.key_center_y = 500  # 391~552 的缝隙
        self.h.step()
        self.assertEqual(self.game.clicks, [], "钥匙在缝隙里不该点击")
        self.assertEqual(self.h.task._state, TreasureUnlockTask.UNLOCKING)

    def test_no_key_does_not_click(self):
        self.h.step()
        self.assertEqual(self.game.clicks, [])

    def test_cached_bbox_not_rebuilt_when_key_occludes(self):
        """钥匙遮挡时不得重建坐标（这是校准后坐标唯一的来源）。"""
        self.game.key_center_y = 580
        before = list(self.h.task._calibrated_bands)
        self.h.step()
        self.assertEqual(list(self.h.task._calibrated_bands), before)


class TestDisappearConfirmation(unittest.TestCase):
    """重点 3：点击后的条带消失能够可靠确认。"""

    def setUp(self):
        self.game = FakeGame()
        self.h = TaskHarness(self.game)
        self.h.calibrate()

    def test_band_removed_after_stable_disappear(self):
        self.game.key_center_y = 580
        self.h.step()
        self.assertEqual(len(self.h.task._active_bands), 2)
        self.assertNotIn(552, [b.y for b in self.h.task._active_bands])

    def test_band_kept_when_click_does_not_remove_it(self):
        self.game.remove_on_click = False
        self.game.key_center_y = 580
        self.h.step()
        self.assertIn(552, [b.y for b in self.h.task._active_bands], "没消失就不该移除")

    def test_repeated_failure_rotates_band_to_tail(self):
        """连续点击失败到上限后移到队尾，避免死磕一条带。"""
        self.game.remove_on_click = False
        self.game.key_center_y = 580
        limit = int(self.h.task.config["_点击重试上限"])
        self.h.step(limit)
        order = [b.y for b in self.h.task._active_bands]
        self.assertEqual(order[-1], 552, "失败到上限的条带应排到队尾")
        self.assertEqual(len(order), 3)


class TestCompletionCheck(unittest.TestCase):
    """重点 4：最终完成状态不因短暂遮挡而误判。"""

    def setUp(self):
        self.game = FakeGame()
        self.h = TaskHarness(self.game)
        self.h.calibrate()

    def _drain(self):
        """把三条带点掉，再多走一帧让状态从 UNLOCKING 进入完成确认。"""
        for center_y in (300, 580, 700):
            self.game.key_center_y = center_y
            self.h.step()
        self.h.step()

    def test_enters_completion_check_when_all_clicked(self):
        self._drain()
        self.assertEqual(self.h.task._state, TreasureUnlockTask.COMPLETION_CHECK)

    def test_reappearing_band_returns_to_unlocking(self):
        """完成确认期间条带又出现 → 回开锁，不能直接结束。"""
        self._drain()
        self.game.bands = [(1316, 552, 19, 53)]  # 之前那条其实没消掉
        self.h.step()
        self.assertEqual(self.h.task._state, TreasureUnlockTask.UNLOCKING)
        self.assertEqual([b.y for b in self.h.task._active_bands], [552])

    def test_finishes_only_after_stable_empty(self):
        self._drain()
        self.game.has_treasure = False
        need = float(self.h.task.config["_完成确认时长(秒)"])
        for _ in range(200):
            self.h.step()
            if self.h.task._state == TreasureUnlockTask.FINISHED:
                break
        self.assertEqual(self.h.task._state, TreasureUnlockTask.FINISHED)
        self.assertGreaterEqual(self.h.now, need)

    def test_not_finished_before_duration_elapsed(self):
        self._drain()
        self.game.has_treasure = False
        self.h.step(2)  # 远不到 2.5 秒
        self.assertNotEqual(self.h.task._state, TreasureUnlockTask.FINISHED)

    def test_treasure_still_present_delays_finish(self):
        """宝箱 UI 还在 → 不会一到完成确认时长就立刻结束。"""
        self._drain()
        for _ in range(40):  # ≈3.2s，刚过 2.5s 但不到 2 倍上限
            self.h.step()
        self.assertNotEqual(self.h.task._state, TreasureUnlockTask.FINISHED)

    def test_treasure_still_present_eventually_finishes(self):
        """但也不能因为图标常驻就永远卡在完成确认里。"""
        self._drain()
        for _ in range(200):
            self.h.step()
            if self.h.task._state == TreasureUnlockTask.FINISHED:
                break
        self.assertEqual(self.h.task._state, TreasureUnlockTask.FINISHED)


class TestPresenceAgainstOcclusion(unittest.TestCase):
    """存在性判定必须抗钥匙遮挡。"""

    def setUp(self):
        self.detector = TreasureBandDetector()
        self.band = Box(1316, 552, 19, 53)

    def test_full_band_present(self):
        self.assertGreater(self.detector.presence(_make_frame(BANDS), self.band), 0.15)

    def test_occluded_band_still_present(self):
        """钥匙压在条带中段，连通域会被切断，但像素占比仍在。"""
        frame = _make_frame(BANDS, key_center_y=578)
        score = self.detector.presence(frame, self.band)
        self.assertGreater(score, 0.15, "被钥匙遮挡时仍应判定为存在")

    def test_removed_band_absent(self):
        frame = _make_frame([b for b in BANDS if b[1] != 552])
        self.assertLess(self.detector.presence(frame, self.band), 0.15)

    def test_occluded_band_fails_connected_component_but_passes_presence(self):
        """对比说明为什么存在性要用像素占比而不是 find()。"""
        frame = _make_frame(BANDS, key_center_y=578)
        region = self.band.copy(y_offset=-20, height_offset=40)
        self.assertEqual(self.detector.find(frame, region), [], "连通域会被钥匙切碎而漏检")
        self.assertGreater(self.detector.presence(frame, self.band), 0.15)


class TestRunLoop(unittest.TestCase):
    """run() 的时间片与状态衔接。"""

    def test_run_returns_immediately_without_treasure(self):
        game = FakeGame()
        game.has_treasure = False
        h = TaskHarness(game)
        h.run_once()
        self.assertEqual(h.task._state, TreasureUnlockTask.WAIT_TREASURE)

    def test_run_resets_after_finish(self):
        game = FakeGame()
        h = TaskHarness(game)
        h.calibrate()
        for center_y in (300, 580, 700):
            game.key_center_y = center_y
            h.step()
        game.has_treasure = False
        for _ in range(300):
            h.step()
            if h.task._state == TreasureUnlockTask.FINISHED:
                break
        h.run_once()
        self.assertEqual(h.task._state, TreasureUnlockTask.WAIT_TREASURE)
        self.assertEqual(h.task._calibrated_bands, [], "完成后应清空校准结果")


if __name__ == "__main__":
    unittest.main()
