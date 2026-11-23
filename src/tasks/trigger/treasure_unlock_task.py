"""宝箱开锁触发式任务。

流程（状态机跨 ``run()`` 调用保持，单次调用只推进到「单次运行时长上限」）::

    WAIT_TREASURE ── treasure_icon ──> CALIBRATING_BANDS ── 连续多帧稳定 ──> UNLOCKING
        ^                                                                        │
        │                                                     钥匙 Y 命中缓存 bbox → 点击
        │                                                     稳定消失 → 移出 active
        │                                                                        ↓
        └──────────────────── FINISHED <── 持续无条带 ── COMPLETION_CHECK <── active 空

两个阶段的分工
--------------
* **校准阶段**：反复检测，直到连续 N 帧「数量一致 + 各条带 Y/高度基本一致」，
  把这一组 bbox 存为 ``_calibrated_bands`` —— **后续所有定位都以它为基准**。
* **开锁阶段**：不再重建坐标，只用缓存 bbox 做两件事：
  1. 钥匙 ``center_y`` 落在哪个条带 → 点它；
  2. 该 bbox 区域里还有没有条带 → 判断点击是否生效。

为什么存在性判断不用连通域
--------------------------
钥匙是横向结构，压在条带上会把它切成上下两段，连通域各自变小后会被形状判据
拒绝，导致「明明还在却被判消失」。因此存在 / 消失只统计 bbox 内**亮色低饱和
像素的占比**（:meth:`TreasureBandDetector.presence`），不看形状，天然抗遮挡。
再叠加「持续稳定」而非「单帧」判定，避免动画 / 闪烁误判。
"""

from __future__ import annotations

from ok import Box, TriggerTask

from src.core.base_game_task import BaseGameTask
from src.core.detector import PredicateDetector
from src.data.feature_list import FeatureList
from src.icons import Icons
from src.image.treasure_band_detector import (
    DEFAULT_BAND_THRESHOLDS,
    BandThresholds,
    TreasureBandDetector,
)

# ── 区域（比例坐标，取自 1920x1080 实测） ────────────────────────
# 颜色带轨道：x 1305~1348, y 251~840
BAND_X, BAND_Y, BAND_TO_X, BAND_TO_Y = 0.6797, 0.2324, 0.7021, 0.7778
# 钥匙：模板标注在 x 1243，但钥匙会沿轨道上下滑动，
# 而 find_feature 不传 box 时只搜标注位置 ±variance（约 4px），
# 所以必须显式给一个覆盖整条轨道的搜索框。
KEY_X, KEY_Y, KEY_TO_X, KEY_TO_Y = 0.630, 0.200, 0.700, 0.820


class TreasureUnlockTask(BaseGameTask, TriggerTask):
    """宝箱开锁触发式任务：校准颜色条带后按钥匙位置逐个点击。"""

    # ── 状态 ──
    WAIT_TREASURE = "WAIT_TREASURE"
    CALIBRATING_BANDS = "CALIBRATING_BANDS"
    UNLOCKING = "UNLOCKING"
    COMPLETION_CHECK = "COMPLETION_CHECK"
    FINISHED = "FINISHED"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "宝箱开锁"
        self.description = "宝箱开锁触发式任务：校准颜色条带后按钥匙位置逐个点击开锁。"
        self.icon = Icons.Trigger

        self.default_config = {
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
            "画调试框": True,
        }

        self._state = self.WAIT_TREASURE
        self._calibrated_bands: list[Box] = []
        self._active_bands: list[Box] = []
        self._calib_prev: list[Box] = []
        self._calib_streak = 0
        self._calib_started_at = 0.0
        self._completion_clean_since: float | None = None
        self._key_missing_since: float | None = None
        self._click_failures: dict[int, int] = {}
        self._detector_cache: TreasureBandDetector | None = None
        self._roi_cache: Box | None = None
        self._key_box_cache: Box | None = None

    # ── 主循环 ──────────────────────────────────────────────

    def run(self):
        budget = float(self.config.get("_单次运行时长上限(秒)", 25.0))
        interval = max(0.02, float(self.config.get("_检测间隔(秒)", 0.08)))

        for frame in self.loop(budget, raise_if_time_out=False):
            if frame is None:
                self.sleep(interval)
                continue

            self._step(frame)

            if self._state == self.FINISHED:
                self._reset()
                return
            if self._state == self.WAIT_TREASURE:
                # 没有宝箱就别占着触发任务的时间片
                return
            self.sleep(interval)

    def _step(self, frame):
        if self._state == self.CALIBRATING_BANDS:
            self._step_calibrate(frame)
        elif self._state == self.UNLOCKING:
            self._step_unlock(frame)
        elif self._state == self.COMPLETION_CHECK:
            self._step_completion(frame)
        else:
            self._step_wait(frame)

    # ── WAIT_TREASURE ───────────────────────────────────────

    def _step_wait(self, frame):
        if not self.find_one(
            feature=FeatureList.treasure_icon,
            frame=frame,
            horizontal_variance=0.01,
            vertical_variance=0.01,
        ):
            return
        self.log_info("检测到宝箱，进入条带校准阶段", notify=True)
        self._state = self.CALIBRATING_BANDS
        self._calib_prev = []
        self._calib_streak = 0
        self._calib_started_at = self.active_time()

    # ── CALIBRATING_BANDS ───────────────────────────────────

    def _step_calibrate(self, frame):
        if self.active_time() - self._calib_started_at > float(self.config.get("_校准超时(秒)", 8.0)):
            self.log_info("条带校准超时，回到等待宝箱")
            self._reset()
            return

        bands = self._detector().find(frame, self._roi())
        self._draw(bands=bands)

        if not bands:
            self._calib_streak = 0
            self._calib_prev = []
            return

        if self._same_layout(self._calib_prev, bands):
            self._calib_streak += 1
        else:
            self._calib_streak = 1
            self._calib_prev = bands

        if self._calib_streak >= int(self.config.get("_校准稳定帧数", 5)):
            self._calibrated_bands = list(bands)
            self._active_bands = list(bands)
            self._click_failures = {}
            ranges = ", ".join(f"{b.y}~{b.y + b.height}" for b in bands)
            self.log_info(f"条带校准完成：{len(bands)} 条，Y 区间 [{ranges}]", notify=True)
            self._state = self.UNLOCKING

    def _same_layout(self, previous, current) -> bool:
        """连续两帧的条带布局是否一致（数量 + 逐条位置 / 高度）。"""
        if not previous or len(previous) != len(current):
            return False
        tolerance = self._tolerance()
        for before, after in zip(previous, current):
            if abs(before.y - after.y) > tolerance:
                return False
            if abs(before.height - after.height) > tolerance:
                return False
            if abs(before.x - after.x) > tolerance:
                return False
        return True

    # ── UNLOCKING ───────────────────────────────────────────

    def _step_unlock(self, frame):
        if not self._active_bands:
            self._enter_completion_check()
            return

        key = self.find_one(feature=FeatureList.treasure_key_icon, frame=frame, box=self._key_box())
        if key is None:
            self._draw()
            if self._key_missing_since is None:
                self._key_missing_since = self.active_time()
            elif self.active_time() - self._key_missing_since > float(self.config.get("_钥匙丢失超时(秒)", 3.0)):
                # 钥匙长时间不见：可能是已完成，也可能是界面关了
                if not self.find_one(feature=FeatureList.treasure_icon, frame=frame,
                                     horizontal_variance=0.01, vertical_variance=0.01):
                    self.log_info("开锁界面已关闭，回到等待宝箱")
                    self._reset()
                else:
                    self.log_info("钥匙持续未出现，转入完成确认")
                    self._enter_completion_check()
            return

        self._key_missing_since = None
        band = TreasureBandDetector.hit_by_center_y(self._active_bands, key.center()[1])
        self._draw(bands=self._active_bands, key=key, target=band)
        if band is None:
            return  # 钥匙正停在两条带之间的缝隙，等它继续移动

        self._click_band(band)

    def _click_band(self, band: Box):
        """点击条带，并用「稳定消失」确认生效。"""
        self.click(band)
        # 预期结果：条带消失。判据本身只关心「还在不在」，
        # 点击目标由 band 提供（已点过，这里只用于取帧判定）。
        gone = self.wait_expectation(
            PredicateDetector(
                lambda frame: not self._band_present(frame, band),
                box=band,
                name=f"band_gone_y{band.y}",
            ),
            time_out=float(self.config.get("_消失确认超时(秒)", 3.0)),
            settle_time=float(self.config.get("_消失确认时长(秒)", 0.35)),
        )
        if not gone:
            self._on_click_failed(band)
            return
        self._active_bands.remove(band)
        self.log_info(f"条带 y={band.y} 已消失，剩余 {len(self._active_bands)} 条")

    def _on_click_failed(self, band: Box):
        """点击未生效：计入失败次数，轮到队尾稍后再试。"""
        index = self._band_index(band)
        count = self._click_failures.get(index, 0) + 1
        self._click_failures[index] = count
        limit = int(self.config.get("_点击重试上限", 3))
        if count < limit:
            self.log_info(f"条带 y={band.y} 点击后仍存在（{count}/{limit}）")
            return
        self.log_info(f"条带 y={band.y} 连续 {count} 次点击未消失，移到队尾稍后重试")
        self._click_failures[index] = 0
        if band in self._active_bands:
            self._active_bands.remove(band)
            self._active_bands.append(band)

    # ── COMPLETION_CHECK ────────────────────────────────────

    def _enter_completion_check(self):
        self._state = self.COMPLETION_CHECK
        self._completion_clean_since = None
        self._key_missing_since = None
        self.log_info("条带已全部点击，进入完成确认阶段")

    def _step_completion(self, frame):
        remaining = [b for b in self._calibrated_bands if self._band_present(frame, b)]
        self._draw(bands=remaining)

        if remaining:
            self.log_info(f"完成确认期间仍有 {len(remaining)} 条带，恢复开锁流程")
            self._active_bands = remaining
            self._click_failures = {}
            self._state = self.UNLOCKING
            return

        now = self.active_time()
        if self._completion_clean_since is None:
            self._completion_clean_since = now
            return

        clean = now - self._completion_clean_since
        needed = float(self.config.get("_完成确认时长(秒)", 2.5))
        if clean < needed:
            return

        # 条带是主判据；宝箱 UI 只作为「已退出开锁界面」的辅助信号。
        # 它不能反过来阻塞结束，否则图标常驻时会永远卡在完成确认里。
        icon_gone = not self.find_one(
            feature=FeatureList.treasure_icon,
            frame=frame,
            horizontal_variance=0.01,
            vertical_variance=0.01,
        )
        if icon_gone or clean >= needed * 2:
            self.log_info("宝箱开锁完成", notify=True)
            self._state = self.FINISHED

    # ── 存在性判定 ──────────────────────────────────────────

    def _band_present(self, frame, band: Box) -> bool:
        """缓存 bbox 区域里是否还有条带（抗钥匙遮挡，只看颜色像素占比）。"""
        threshold = float(self.config.get("_条带存在阈值", 0.15))
        return self._detector().presence(frame, band) >= threshold

    # ── 工具 ────────────────────────────────────────────────

    def _reset(self):
        self._state = self.WAIT_TREASURE
        self._calibrated_bands = []
        self._active_bands = []
        self._calib_prev = []
        self._calib_streak = 0
        self._calib_started_at = 0.0
        self._completion_clean_since = None
        self._key_missing_since = None
        self._click_failures = {}

    def _band_index(self, band: Box) -> int:
        """条带在校准结果中的序号，用作失败计数的稳定 key。"""
        for index, known in enumerate(self._calibrated_bands):
            if known == band:
                return index
        return -1

    def _tolerance(self) -> int:
        """位置容差（像素），按当前分辨率缩放。"""
        return self.scale_distance(int(self.config.get("_校准位置容差", 3)), minimum=1)

    def _roi(self) -> Box:
        if self._roi_cache is None:
            self._roi_cache = self.box_of_screen(
                BAND_X, BAND_Y, BAND_TO_X, BAND_TO_Y, name="treasure_roi"
            )
        return self._roi_cache

    def _key_box(self) -> Box:
        if self._key_box_cache is None:
            self._key_box_cache = self.box_of_screen(
                KEY_X, KEY_Y, KEY_TO_X, KEY_TO_Y, name="treasure_key_zone"
            )
        return self._key_box_cache

    def _detector(self) -> TreasureBandDetector:
        """按当前分辨率缩放阈值的检测器（尺寸类阈值随分辨率变，长宽比不变）。"""
        if self._detector_cache is not None:
            return self._detector_cache
        scale = self.resolution_scale()
        thresholds: BandThresholds = DEFAULT_BAND_THRESHOLDS.with_(
            min_area=max(60, round(DEFAULT_BAND_THRESHOLDS.min_area * scale * scale)),
            max_width=max(12, round(DEFAULT_BAND_THRESHOLDS.max_width * scale)),
            min_height=max(12, round(DEFAULT_BAND_THRESHOLDS.min_height * scale)),
        )
        self._detector_cache = TreasureBandDetector(thresholds)
        return self._detector_cache

    def _draw(self, bands=None, key=None, target=None):
        """在覆盖层画调试框；不开启时直接跳过。"""
        if not self.config.get("画调试框", True):
            return
        if bands:
            self.draw_boxes("treasure_band_active", bands, color="red")
        if target is not None:
            self.draw_boxes("treasure_band_target", target, color="green")
        if key is not None:
            self.draw_boxes("treasure_key", key, color="blue")
