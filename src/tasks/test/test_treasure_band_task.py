"""宝箱开锁「颜色带」连通域检测的**可视化调试任务**。

在覆盖层（overlay）上实时画框，和 YOLO 检测画框走的是同一条通路::

    self.draw_boxes(key, boxes, color)
        └─> communicate.draw_box.emit
              ├─> Screenshot.ui_dict        （截图用）
              └─> Win32GdiOverlay           （GDI 直接画在游戏窗口上）

两个前提
--------
1. ``use_overlay`` 必须为 True（GUI 顶部的覆盖层开关）；本任务默认自动打开。
2. 覆盖层上的框 4 秒后过期，所以任务在运行时以「检测间隔」持续重画，
   只要任务还在跑，框就一直在。

框的颜色（ok 覆盖层只支持这三种）
--------------------------------
* **红** —— 命中：通过全部形状判据的颜色带（就是你要看的红框）
* **绿** —— 检测区域 ROI
* **蓝** —— 被过滤掉的候选，框上标注过滤原因（area / too_wide /
  too_short / not_vertical / on_edge），用来校准阈值
"""

from __future__ import annotations

from ok import Box

from src.core.base_game_task import BaseGameTask
from src.icons import Icons
from src.image.treasure_band_detector import (
    DEFAULT_BAND_THRESHOLDS,
    BandThresholds,
    TreasureBandDetector,
)

#: 颜色带 ROI（比例坐标，取自 1920x1080 实测：x 1305~1348, y 251~840）
ROI_X = 0.6797
ROI_Y = 0.2324
ROI_TO_X = 0.7021
ROI_TO_Y = 0.7778


class TestTreasureBandTask(BaseGameTask):
    """测试任务：实时检测开锁颜色带并在覆盖层上画框，用于肉眼验证效果。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "颜色带检测"
        self.icon = Icons.Test
        self.description = "HSV+连通域检测开锁颜色带，实时在覆盖层画红框（绿=检测区，蓝=被过滤）"
        # 纯调试任务：只在 debug 模式（main_debug.py）下出现在任务列表
        self.visible = self.debug

        self.default_config = {
            "_运行时长(秒)": 20,
            "_检测间隔(秒)": 0.2,
            "V 下限": DEFAULT_BAND_THRESHOLDS.lower[2],
            "S 上限": DEFAULT_BAND_THRESHOLDS.upper[1],
            "最小高度": DEFAULT_BAND_THRESHOLDS.min_height,
            "最大宽度": DEFAULT_BAND_THRESHOLDS.max_width,
            "最小长宽比": DEFAULT_BAND_THRESHOLDS.min_aspect,
            "显示被过滤候选": True,
            "自动开启覆盖层": True,
            "结束时保存截图": False,
        }

    # ── 主流程 ──────────────────────────────────────────────

    def run(self):
        duration = max(0.0, float(self.config.get("_运行时长(秒)", 20)))
        interval = max(0.05, float(self.config.get("_检测间隔(秒)", 0.2)))
        show_rejected = bool(self.config.get("显示被过滤候选", True))

        if not self._ensure_overlay():
            self.log_info("覆盖层未开启，框只会写进截图，游戏窗口上看不到红框", notify=True)

        detector = TreasureBandDetector(self._thresholds())
        roi = self.box_of_screen(ROI_X, ROI_Y, ROI_TO_X, ROI_TO_Y, name="treasure_roi")
        self.log_info(f"颜色带 ROI: x={roi.x} y={roi.y} w={roi.width} h={roi.height}")

        last_signature = None
        saved = False

        for frame in self.loop(duration, raise_if_time_out=False):
            if frame is None:
                self.sleep(interval)
                continue

            results = detector.analyze(frame, roi)
            bands = [item.box for item in results if item.matched]
            rejected = [item for item in results if not item.matched]

            # 覆盖层画框：持续重画以刷新 4 秒过期计时
            self.draw_boxes("treasure_roi", roi, color="green")
            self.draw_boxes("treasure_band", bands, color="red")
            if show_rejected:
                self.draw_boxes("treasure_band_rejected", self._rejected_boxes(rejected), color="blue")
            elif rejected:
                # 关掉候选显示时要清掉上一次残留，否则旧框会一直挂到过期
                self.draw_boxes("treasure_band_rejected", [], color="blue")

            signature = tuple((b.y, b.y + b.height, b.width) for b in bands)
            if signature != last_signature:
                last_signature = signature
                self._log_bands(frame, bands, rejected)

            if self.config.get("结束时保存截图", False) and not saved and bands:
                self.screenshot("treasure_band", frame=frame, show_box=True)
                saved = True

            self.sleep(interval)

        self.log_info(
            f"颜色带检测结束：最后一次命中 {0 if last_signature is None else len(last_signature)} 条",
            notify=True,
        )

    # ── 输出 ────────────────────────────────────────────────

    def _log_bands(self, frame, bands, rejected):
        """结果变化时输出一次，避免逐帧刷屏。"""
        height = frame.shape[0]
        if bands:
            rows = []
            for index, band in enumerate(bands, start=1):
                top, bottom = band.y, band.y + band.height
                rows.append(
                    f"  ①~{index} x={band.x} y={top}~{bottom} "
                    f"w={band.width} h={band.height} "
                    f"(比例 {top / height:.3f}~{bottom / height:.3f})"
                )
            self.log_info(f"命中 {len(bands)} 条颜色带：\n" + "\n".join(rows))
        else:
            self.log_info("未命中颜色带")

        if rejected:
            reasons = {}
            for item in rejected:
                reasons[item.failed] = reasons.get(item.failed, 0) + 1
            detail = " ".join(f"{name}x{count}" for name, count in sorted(reasons.items()))
            self.log_info(f"过滤 {len(rejected)} 个候选（蓝框）：{detail}")

    @staticmethod
    def _rejected_boxes(rejected) -> list[Box]:
        """把被过滤的候选转成可画的 Box，名称里带上过滤原因。"""
        boxes = []
        for item in rejected:
            if item.width <= 0 or item.height <= 0:
                continue
            boxes.append(Box(item.x, item.y, item.width, item.height, 0.0, f"rej_{item.failed}"))
        return boxes

    # ── 阈值 / 覆盖层 ───────────────────────────────────────

    def _thresholds(self) -> BandThresholds:
        """从任务配置组装阈值，方便不重启调参。"""
        lower = list(DEFAULT_BAND_THRESHOLDS.lower)
        upper = list(DEFAULT_BAND_THRESHOLDS.upper)
        lower[2] = int(self.config.get("V 下限", lower[2]))
        upper[1] = int(self.config.get("S 上限", upper[1]))
        return DEFAULT_BAND_THRESHOLDS.with_(
            lower=tuple(lower),
            upper=tuple(upper),
            min_height=int(self.config.get("最小高度", DEFAULT_BAND_THRESHOLDS.min_height)),
            max_width=int(self.config.get("最大宽度", DEFAULT_BAND_THRESHOLDS.max_width)),
            min_aspect=float(self.config.get("最小长宽比", DEFAULT_BAND_THRESHOLDS.min_aspect)),
        )

    def _ensure_overlay(self) -> bool:
        """确保覆盖层画框开关已打开，返回最终状态。"""
        if self._is_debug_overlay_enabled():
            return True
        if not self.config.get("自动开启覆盖层", True):
            return False
        try:
            from ok import og

            app = getattr(og, "app", None)
            setter = getattr(app, "set_overlay_setting", None)
            if not callable(setter):
                return False
            setter("boxes", True)
            self.log_info("已自动打开覆盖层画框开关（use_overlay）", notify=True)
            return True
        except Exception as e:  # 无 GUI / headless 下静默降级
            self.log_info(f"自动打开覆盖层失败：{e}")
            return False
