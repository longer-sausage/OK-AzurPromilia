"""辅助星结触发式任务。

星结是智慧种族与奇波彼此认可后、通过星结卡缔结的更深一层契约关系。缔结过程中
需要对准目标射击，本任务就是在这一步代劳：星结界面里光标指向目标时，屏幕中心
会出现一道彩色可射击光圈（绿 / 黄 / 红，对应不同命中概率），命中即自动点击准心。

流程（每次 ``run()`` 取一帧判定一次，跨调用只保留计数与冷却时间戳）::

    next_frame ──> 有 star_link_icon? ──否──> 返回（不在星结界面，不干预）
                                     │ 是
                                     ↓
                    HSV + 圆弧拟合检测 ROI ──> 命中? ──否──> 清空连续命中计数，返回
                                     │
                                     是
                                     ↓
                        连续命中计数 +1 ── 未到「连续命中帧数」──> 返回
                                     ↓ 达到
                              冷却未到? ──是──> 返回（不重复点击）
                                     ↓ 否
                          单击左键（圆心）+ 记录冷却时间

为什么要 star_link_icon 前置
----------------------------
可射击光圈只在星结时出现，但屏幕中心那片区域的彩色元素并不少（技能特效、
场景光斑都可能拟合成圆弧）。用 ``star_link_icon`` 把检测限定在星结界面内，
等于给后面的颜色 / 形状判据加了一道「场合」闸门，避免在非星结场景乱点。

为什么检测用圆弧拟合
--------------------
那道光圈是**同一个圆上的一段弧**（实测：圆心 ≈ 屏幕中心、半径 ≈ 52px、
残差 < 1.5px、弧宽 3~6px），不是闭合环，因此不能靠「有没有内孔」判断。
颜色阈值之后再做一次最小二乘圆拟合，只有像素都落在同一个圆上才算命中。

为什么还需要两个闸门
--------------------
* **连续命中帧数**：光效出现 / 消失时有淡入淡出动画，单帧命中可能是过渡帧。
  默认 1 帧（立即点击），画面抖动误点时可上调。
* **点击冷却**：触发任务由执行器循环驱动，命中期间每轮都会进来，
  不加冷却会把一次提示点成连击。
"""

from __future__ import annotations

from ok import TriggerTask

from src.core.base_game_task import BaseGameTask
from src.data.feature_list import FeatureList
from src.icons import Icons
from src.image.glow_target_detector import (
    ALL_GLOW_COLORS,
    DEFAULT_GLOW_THRESHOLDS,
    GLOW_COLOR_LABELS,
    GlowTargetDetector,
)

# ── 检测区域（比例坐标，取自 1920x1080 实测） ──────────────────
ROI_X, ROI_Y, ROI_TO_X, ROI_TO_Y = 0.4724, 0.4426, 0.5365, 0.5611

#: 颜色开关配置项 -> 颜色常量
COLOR_SWITCHES = {
    "检测绿色": "green",
    "检测黄色": "yellow",
    "检测红色": "red",
}


class StarLinkAssistTask(BaseGameTask, TriggerTask):
    """辅助星结触发式任务：星结界面出现可射击光圈时自动点击屏幕准心。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "辅助星结"
        self.description = (
            "星结辅助触发式任务：缔结星结时，光标指向目标出现可射击光圈，"
            "自动点击屏幕准心完成射击。"
        )
        self.icon = Icons.Trigger
        self.trigger_interval = 0  # 每轮都参与，靠自身冷却限流

        self.default_config = {
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
            "画调试框": True,
        }
        self.config_description.update({
            "要求环形": "只认圆环形 / 弧形的彩色光效（可射击标识）；关掉后退回「最大彩色团块」模式，可匹配实心提示。",
            "_最小面积": "光效像素数下限，按 1920x1080 为基准，随分辨率自动缩放。",
            "_连续命中帧数": "连续多少帧命中才点击，用于过滤淡入淡出的过渡帧。",
            "_点击冷却(秒)": "两次点击之间的最短间隔，防止一次提示被点成连击。",
        })

        self._streak = 0
        self._last_click_at: float | None = None  # None = 尚未点击过，不受冷却限制
        self._detector_cache: GlowTargetDetector | None = None
        self._detector_key: tuple | None = None
        self._roi_cache = None

    # ── 主循环 ──────────────────────────────────────────────

    def run(self):
        frame = self.next_frame()
        if frame is None:
            return

        # 前置闸门：不在星结界面就完全不干预，后面所有判据都不必跑。
        if not self.find_feature(feature_name=FeatureList.star_link_icon, frame=frame):
            self._streak = 0
            return

        detection = self._detector().analyze(frame, self._roi())
        self._draw(detection)

        if not detection:
            self._streak = 0
            return

        self._streak += 1
        if self._streak < max(1, int(self.config.get("_连续命中帧数", 1))):
            return

        now = self.active_time()
        cooldown = max(0.0, float(self.config.get("_点击冷却(秒)", 0.35)))
        if self._last_click_at is not None and now - self._last_click_at < cooldown:
            return

        self._last_click_at = now
        self.click(
            detection.box,
            down_time=max(0.0, float(self.config.get("_按下时长(秒)", 0.01))),
            after_sleep=max(0.0, float(self.config.get("_点击后等待(秒)", 0.0))),
        )
        if self.config.get("记录点击日志", True):
            label = GLOW_COLOR_LABELS.get(detection.color, detection.color)
            center = detection.box.center()
            shape = "光圈" if detection.is_arc else "色块"
            self.log_info(
                f"检测到{label}可射击{shape}（半径 {detection.radius:.0f}px，"
                f"残差 {detection.residual:.1f}px），点击 ({center[0]}, {center[1]})"
            )

    # ── 工具 ────────────────────────────────────────────────

    def _roi(self):
        if self._roi_cache is None:
            self._roi_cache = self.box_of_screen(
                ROI_X, ROI_Y, ROI_TO_X, ROI_TO_Y, name="glow_roi"
            )
        return self._roi_cache

    def _detector(self) -> GlowTargetDetector:
        """按当前配置（颜色开关 + 环形要求 + 分辨率缩放后的面积阈值）取检测器实例。"""
        colors = tuple(color for key, color in COLOR_SWITCHES.items() if self.config.get(key, True))
        if not colors:
            colors = ALL_GLOW_COLORS
        scale = self.resolution_scale()
        min_area = max(20, round(float(self.config.get("_最小面积", 60)) * self._area_scale()))
        require_arc = bool(self.config.get("要求环形", True))
        # 半径 / 跨度同样随分辨率线性缩放：4K 下的光圈本身就比 1080p 大一圈。
        overrides = {
            "min_area": min_area,
            "require_arc": require_arc,
            "min_radius": DEFAULT_GLOW_THRESHOLDS.min_radius * scale,
            "max_radius": DEFAULT_GLOW_THRESHOLDS.max_radius * scale,
            "min_span": DEFAULT_GLOW_THRESHOLDS.min_span * scale,
        }
        key = (colors, *overrides.values())
        if self._detector_cache is None or self._detector_key != key:
            self._detector_cache = GlowTargetDetector(
                DEFAULT_GLOW_THRESHOLDS.with_(**overrides), colors=colors
            )
            self._detector_key = key
        return self._detector_cache

    def _area_scale(self) -> float:
        """面积随分辨率按平方缩放（1920x1080 为 1.0）。"""
        scale = self.resolution_scale()
        return scale * scale

    def _draw(self, detection):
        """在覆盖层画调试框；不开启时直接跳过。"""
        if not self.config.get("画调试框", True):
            return
        self.draw_boxes("glow_roi", self._roi(), color="blue")
        if detection.box is not None:
            self.draw_boxes("glow_target", detection.box, color="green")
