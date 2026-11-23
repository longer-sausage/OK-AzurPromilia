"""屏幕中心「可射击光圈 / 弧形标识」检测（HSV 阈值 + 圆弧拟合，无 OCR、无模板匹配）。

适用问题
--------
光标指向怪物时，屏幕中心会出现一道**彩色弧线 / 光圈**作为可射击标识：

* 绿色（高概率提示，100%）；
* 金黄色（中等概率提示，48.3%）；
* 橙红色（低概率提示，1.4%）。

实测（1920x1080 三张样本）这道弧是**同一个圆上的一段**：
圆心 ≈ 屏幕中心 (963, 541)、半径 ≈ 52px、拟合残差 < 1.5px，
弧带只有 3~6px 宽。它通常是开放弧而不是闭合圆环，因此不能靠「有没有内孔」
来判断 —— 那正是早期版本在真实截图上漏检的原因。

判据因此定为**圆弧拟合**：这块彩色像素是不是都落在同一个圆上。
噪声、色块、直线都拟合不出这种低残差的小圆：

* 实心色块 —— 点铺满整个盘面，残差 / 半径 ≈ 0.28，远超阈值；
* 直线 / 长条 —— 拟合出的半径是几百甚至上千，被半径上限挡掉；
* 只有真正的弧 / 环，残差 / 半径 < 0.03。

流程
----
    Box ──> 裁剪 ROI ──> RGB→HSV ──> 三色 inRange ──> 闭运算连接弧带
       ──> 合并掩膜 ──> 外轮廓 ──> 逐候选：面积 / 圆弧拟合 / 半径 / 跨度过滤
       ──> 取「面积 ÷ (1+残差)」最优 ──> 弧带内各色像素投票
       ──> 返回以**圆心**为中心的 Box + 颜色

为什么要返回圆心而不是外接框中心
--------------------------------
这道弧只是圆的一段（往往只有右侧 90°~120°），它的外接框中心偏向弧那一侧，
点在框中心就点歪了；而拟合出的圆心就是屏幕准心 —— 那才是该点的位置。

判定条件（默认，1920x1080 实测样本）
--------------------------------
    H/S/V 落在 green / yellow / red 区间之一（红色跨 0° 两侧，需两段区间）
    连通域面积 >= 60
    拟合圆半径 in [30, 90]           # 太小的噪点圆、太大的背景弧都不要
    残差均值 <= 6px 且 <= 0.12 × 半径  # 「是不是圆」的核心判据
    弧跨度（外接框对角线）>= 40        # 排除小碎点
    圆心落在 ROI 内（可关）           # 避免误检画面别处的大圆弧

坐标约定
--------
返回的 ``Box`` 是**整帧坐标**（不是 ROI 内相对坐标），为以圆心为中心、
边长 ``2r`` 的正方形，可直接 ``click()`` / 交给 ``draw_boxes()`` 画在覆盖层上。

阈值全部集中在 :class:`GlowThresholds`，便于按具体画面微调。
若画面里的提示是实心色块而非弧 / 环，把 ``require_arc`` 关掉即可退回
「最大彩色团块」模式。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace

import cv2
import numpy as np
from ok import Box

#: HSV 三元组 (H, S, V)
HSV = tuple[int, int, int]

#: 三种可检测的泛光颜色
GLOW_GREEN = "green"
GLOW_YELLOW = "yellow"
GLOW_RED = "red"

#: 默认检测顺序（也用作投票平票时的优先顺序）
ALL_GLOW_COLORS: tuple[str, ...] = (GLOW_GREEN, GLOW_YELLOW, GLOW_RED)

#: 颜色显示名（日志用）
GLOW_COLOR_LABELS = {
    GLOW_GREEN: "绿色",
    GLOW_YELLOW: "黄色",
    GLOW_RED: "红色",
}


@dataclass(frozen=True)
class GlowThresholds:
    """泛光检测的全部阈值。

    命名约定：

    * ``green`` / ``yellow`` / ``red`` —— HSV 区间。红色在 HSV 里跨 0° 两端，
      因此需要两段区间，其余颜色一段；
    * ``min_area`` / ``min_radius`` / ``max_radius`` / ``min_span`` —— 尺寸门槛；
    * ``max_residual`` / ``max_residual_ratio`` —— 圆弧拟合的贴合度门槛，
      这是区分「弧 / 环」与「色块」的核心；
    * ``morph_kernel`` —— 闭运算核大小，把粒子感的弧带连成整条。

    色相区间刻意**不重叠**：黄绿交界取在 34 / 35 之间（黄 ``15~34``、
    绿 ``35~95``），避免同一像素被两色同时计数导致投票失真。
    S/V 下限偏高（110 / 190）是刻意的：光效是自发光的高亮高饱和色，
    背景里的草地、服装虽然同色相但没那么亮，抬门槛能把它们甩掉。
    实际画面偏色时按 :meth:`with_` 调整。
    """

    # ── 1. 颜色（主特征） ────────────────────────────────────────
    green: tuple[HSV, HSV] = ((35, 110, 190), (95, 255, 255))
    yellow: tuple[HSV, HSV] = ((15, 110, 190), (34, 255, 255))
    red: tuple[tuple[HSV, HSV], tuple[HSV, HSV]] = (
        ((0, 110, 190), (14, 255, 255)),
        ((160, 110, 190), (180, 255, 255)),
    )

    # ── 2. 形态学去噪 ────────────────────────────────────────────
    # 3x3 闭运算：把粒子感 / 抗锯齿切断的弧带连成整条。
    morph_kernel: int = 3

    # ── 3. 尺寸门槛 ──────────────────────────────────────────────
    min_area: float = 60.0  # 连通域面积下限（像素）
    min_radius: float = 30.0  # 拟合圆半径下限
    max_radius: float = 90.0  # 拟合圆半径上限（背景大圆弧会被这里挡掉）
    min_span: float = 40.0  # 弧跨度下限（外接框对角线）

    # ── 4. 圆弧贴合度 ────────────────────────────────────────────
    max_residual: float = 6.0  # 轮廓点到拟合圆的距离残差均值上限（像素）
    max_residual_ratio: float = 0.12  # 残差 / 半径上限，越小越严格
    # 光效是「一圈」不是「一坨」。注意实心圆盘的外轮廓本身也是个完美的圆，
    # 光看残差会把圆盘也判成环，所以必须再看厚度：圆盘厚度 ≈ 半径，
    # 而真实弧带只有半径的 6%（3px / 52px）。
    max_thickness_ratio: float = 0.35  # 内切厚度 / 半径上限
    require_arc: bool = True  # True：只认弧 / 环；False：退回最大色块
    require_center_in_roi: bool = True  # 拟合圆心必须落在 ROI 内

    # ── 5. 输出 ──────────────────────────────────────────────────
    box_name: str = "glow_target"

    def ranges(self, color: str) -> list[tuple[HSV, HSV]]:
        """返回某颜色的一组 HSV 区间（红色两段，其余一段）。

        Args:
            color: :data:`GLOW_GREEN` / :data:`GLOW_YELLOW` / :data:`GLOW_RED`。

        Returns:
            list[tuple[HSV, HSV]]: ``[(lower, upper), ...]``。

        Raises:
            ValueError: 传入了未知颜色。
        """
        if color not in ALL_GLOW_COLORS:
            raise ValueError(f"未知的泛光颜色: {color}")
        value = getattr(self, color)
        if color == GLOW_RED:
            return [tuple(item) for item in value]
        return [tuple(value)]

    def with_(self, **overrides) -> GlowThresholds:
        """返回改写了部分阈值的副本（不修改基线）。

        Raises:
            TypeError: 传入了不存在的阈值名。
        """
        unknown = sorted(set(overrides) - {f.name for f in fields(self)})
        if unknown:
            raise TypeError(f"未知的阈值参数: {unknown}")
        return replace(self, **overrides)


#: 默认阈值
DEFAULT_GLOW_THRESHOLDS = GlowThresholds()


@dataclass(frozen=True)
class GlowDetection:
    """单次检测结果。

    Attributes:
        matched: 是否命中（是可射击的弧 / 环）。
        box: 命中时的帧坐标 Box（以圆心为中心、边长 2r 的正方形）；未命中为 None。
        color: 命中的颜色（``green`` / ``yellow`` / ``red``）。
        area: 连通域面积（像素）。
        counts: 连通域内各颜色的像素数，用于判断投票是否明确。
        failed: 未命中原因（``empty_input`` / ``empty_roi`` / ``no_color``
            / ``no_contour`` / ``area`` / ``shape`` / ``not_arc``）。
        x / y / width / height: 命中方框的位置与尺寸（帧坐标）。
        radius: 拟合圆半径。
        residual: 轮廓点到拟合圆的距离残差均值（越小越像正圆）。
        span: 弧跨度（外接框对角线）。
        thickness: 弧带内切厚度（距离变换最大值，即弧宽的一半）。
        is_arc: 是否通过圆弧判据。
        center: 拟合圆心（帧坐标），也就是实际点击的位置。
    """

    matched: bool
    box: Box | None = None
    color: str = ""
    area: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)
    failed: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    radius: float = 0.0
    residual: float = 0.0
    span: float = 0.0
    thickness: float = 0.0
    is_arc: bool = False
    center: tuple[float, float] = (0.0, 0.0)

    def __bool__(self) -> bool:
        return self.matched


class GlowTargetDetector:
    """可射击光圈 / 弧形标识检测器。

    实例只持有阈值与启用颜色，不持有帧数据；请复用实例，不要在每次检测时新建。

    Example:
        >>> detector = GlowTargetDetector(colors=("green",))
        >>> box = task.box_of_screen(0.4724, 0.4426, 0.5365, 0.5611)
        >>> result = detector.analyze(task.next_frame(), box)
        >>> result.color, result.is_arc, result.radius
        ('green', True, 52.0)
    """

    def __init__(self, thresholds: GlowThresholds | None = None, colors=None):
        """
        Args:
            thresholds: 自定义阈值，缺省用 :data:`DEFAULT_GLOW_THRESHOLDS`。
            colors: 启用的颜色；缺省全部启用。顺序用于投票平票时的优先级。
        """
        self.thresholds = thresholds or DEFAULT_GLOW_THRESHOLDS
        self.colors = tuple(colors) if colors else ALL_GLOW_COLORS

    # ── 对外 API ─────────────────────────────────────────────

    def find(self, frame, box, name: str | None = None) -> Box | None:
        """检测 Box 内最像可射击标识的弧 / 环。

        Args:
            frame: BGR 帧。
            box: 检测区域（``box_of_screen`` 生成）。
            name: 结果 Box 的名称。

        Returns:
            Box | None: 命中的帧坐标 Box（可直接 click），未命中返回 None。
        """
        return self.analyze(frame, box, name=name).box

    def analyze(self, frame, box, name: str | None = None) -> GlowDetection:
        """执行检测并返回完整结果（含未命中原因与各颜色像素数）。

        Args:
            frame: BGR 帧。
            box: 检测区域。
            name: 命中 Box 的名称。

        Returns:
            GlowDetection: 检测结果。

        Example:
            >>> result = detector.analyze(frame, box)
            >>> result.matched, result.color, result.failed
            (True, 'green', '')
        """
        t = self.thresholds
        if frame is None or box is None:
            return GlowDetection(False, failed="empty_input")

        frame_height, frame_width = frame.shape[:2]
        x, y, width, height = self._clamp_box(box, frame_width, frame_height)
        if width <= 0 or height <= 0:
            return GlowDetection(False, failed="empty_roi")

        roi = frame[y : y + height, x : x + width]
        if roi.size == 0:
            return GlowDetection(False, failed="empty_roi")

        hsv = cv2.cvtColor(self._to_bgr(roi), cv2.COLOR_BGR2HSV)
        masks = {color: self._mask(hsv, color) for color in self.colors}
        if not masks:
            return GlowDetection(False, failed="no_color")

        combined = self._combine(list(masks.values()))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(combined, 8)
        if count <= 1:
            return GlowDetection(False, failed="no_contour")

        # 面积门槛单独先过：全都太小是「没戏」，与形状不符不是一回事，
        # 分开报 failed 便于调参时判断该调 min_area 还是形状阈值。
        max_area = float(stats[1:, cv2.CC_STAT_AREA].max())
        if max_area < t.min_area:
            return GlowDetection(False, failed="area", area=max_area)

        best = None  # 通过圆弧判据的最优候选
        blob = None  # 不成弧的备选（放宽模式用 / 诊断用）
        for index in range(1, count):
            candidate = self._evaluate(labels, stats, index, (height, width))
            if candidate is None:
                continue
            if candidate["is_arc"]:
                if best is None or candidate["score"] > best["score"]:
                    best = candidate
            elif blob is None or candidate["score"] > blob["score"]:
                blob = candidate

        chosen = best if best is not None else (None if t.require_arc else blob)
        if chosen is None:
            return GlowDetection(
                False,
                failed="not_arc" if blob is not None else "shape",
                area=max_area,
            )

        return self._build_detection(chosen, masks, x, y, name)

    # ── 候选评估 ─────────────────────────────────────────────

    def _evaluate(self, labels, stats, index: int, roi_shape) -> dict | None:
        """给单个连通域打分；不满足面积 / 圆弧 / 半径 / 跨度门槛返回 None。

        用连通域而不是轮廓：厚度必须在**连通域自己的像素**上算，
        否则闭合圆环一填充就变成实心盘，会被厚度判据误杀。

        Args:
            labels: ``connectedComponentsWithStats`` 的标签图。
            stats: 同上，提供面积与外接框。
            index: 连通域编号（>= 1）。
            roi_shape: ``(height, width)``，用于判断圆心是否落在 ROI 内。

        Returns:
            dict | None: 含 mask / area / center / radius / residual /
            span / thickness / is_arc / score 的候选字典。
        """
        t = self.thresholds
        area = float(stats[index, cv2.CC_STAT_AREA])
        if area < t.min_area:
            return None

        own = labels == index
        ys, xs = np.nonzero(own)
        cx, cy, radius, residual = self._fit_circle(np.column_stack([xs, ys]))
        if radius <= 0 or not t.min_radius <= radius <= t.max_radius:
            return None

        on_circle = residual <= t.max_residual and residual <= radius * t.max_residual_ratio
        if on_circle and t.require_center_in_roi:
            if not (0 <= cx < roi_shape[1] and 0 <= cy < roi_shape[0]):
                return None

        span = math.hypot(stats[index, cv2.CC_STAT_WIDTH], stats[index, cv2.CC_STAT_HEIGHT])
        if span < t.min_span:
            return None

        thickness = float(cv2.distanceTransform(own.astype(np.uint8), cv2.DIST_L2, 5).max())
        # 圆 + 薄 = 弧 / 环；圆 + 厚 = 实心圆盘。
        is_arc = on_circle and thickness <= radius * t.max_thickness_ratio

        return {
            "mask": own,
            "area": area,
            "center": (cx, cy),
            "radius": radius,
            "residual": residual,
            "span": span,
            "thickness": thickness,
            "is_arc": is_arc,
            # 面积越大越可信、残差越小越像真圆：面积 ÷ (1 + 残差)。
            "score": area / (1.0 + residual) * (2.0 if is_arc else 1.0),
        }

    def _build_detection(self, candidate, masks, offset_x: int, offset_y: int, name):
        """把选中候选组装成 :class:`GlowDetection`（坐标换算回整帧）。"""
        t = self.thresholds
        own = candidate["mask"].astype(np.uint8)

        counts = {
            color: int(cv2.countNonZero(cv2.bitwise_and(mask, own)))
            for color, mask in masks.items()
        }
        color = max(counts, key=lambda item: counts[item])
        color_ratio = counts[color] / max(1, int(candidate["area"]))

        cx, cy = candidate["center"]
        radius = candidate["radius"]
        side = max(8, int(round(2 * radius)))
        bx, by = int(round(cx - radius)), int(round(cy - radius))

        return GlowDetection(
            matched=True,
            box=Box(
                offset_x + bx,
                offset_y + by,
                side,
                side,
                self._confidence(candidate["residual"] / radius, color_ratio),
                name or t.box_name,
            ),
            color=color,
            counts=counts,
            area=candidate["area"],
            x=offset_x + bx,
            y=offset_y + by,
            width=side,
            height=side,
            radius=radius,
            residual=candidate["residual"],
            span=candidate["span"],
            thickness=candidate["thickness"],
            is_arc=candidate["is_arc"],
            center=(offset_x + cx, offset_y + cy),
        )

    # ── 内部步骤 ─────────────────────────────────────────────

    def _mask(self, hsv: np.ndarray, color: str) -> np.ndarray:
        """单色 HSV inRange（多段区间取并集）+ 闭运算把弧带连成整条。"""
        t = self.thresholds
        mask = None
        for lower, upper in t.ranges(color):
            part = cv2.inRange(hsv, np.array(lower, np.uint8), np.array(upper, np.uint8))
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        size = max(1, int(t.morph_kernel))
        if size <= 1:
            return mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    @staticmethod
    def _combine(masks: list[np.ndarray]) -> np.ndarray:
        """把所有启用颜色的掩膜合并成一张。"""
        combined = masks[0]
        for mask in masks[1:]:
            combined = cv2.bitwise_or(combined, mask)
        return combined

    @staticmethod
    def _fit_circle(points) -> tuple[float, float, float, float]:
        """最小二乘拟合圆，返回 ``(cx, cy, radius, 残差均值)``。

        解的是 ``x² + y² = 2·cx·x + 2·cy·y + c`` 的线性最小二乘，
        比三点定圆稳（弧上有几十上百个点，噪声被平均掉）。

        Returns:
            tuple[float, float, float, float]: 圆心、半径、残差均值。
            点太少或拟合退化时返回 ``(0, 0, 0, inf)``。
        """
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if len(pts) < 8:
            return 0.0, 0.0, 0.0, float("inf")
        x, y = pts[:, 0], pts[:, 1]
        design = np.column_stack([x, y, np.ones(len(x))])
        target = x * x + y * y
        try:
            sol = np.linalg.lstsq(design, target, rcond=None)[0]
        except np.linalg.LinAlgError:  # pragma: no cover - 输入退化时
            return 0.0, 0.0, 0.0, float("inf")
        cx, cy = sol[0] / 2.0, sol[1] / 2.0
        squared = sol[2] + cx * cx + cy * cy
        if squared <= 0:
            return 0.0, 0.0, 0.0, float("inf")
        radius = math.sqrt(squared)
        residual = float(np.abs(np.hypot(x - cx, y - cy) - radius).mean())
        return cx, cy, radius, residual

    @staticmethod
    def _confidence(residual_ratio: float, color_ratio: float) -> float:
        """把贴合度与颜色纯度折算成 0.5~1.0 的置信度，仅用于展示 / 排序。"""
        fitness = max(0.0, 1.0 - residual_ratio / 0.12)
        return round(0.5 + 0.5 * min(1.0, fitness) * min(1.0, color_ratio), 4)

    @staticmethod
    def _clamp_box(box, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
        """把 Box 裁剪到帧内，返回 (x, y, width, height)。"""
        x = max(0, min(int(box.x), max(0, frame_width - 1)))
        y = max(0, min(int(box.y), max(0, frame_height - 1)))
        width = max(0, min(int(box.width), frame_width - x))
        height = max(0, min(int(box.height), frame_height - y))
        return x, y, width, height

    @staticmethod
    def _to_bgr(roi: np.ndarray) -> np.ndarray:
        """统一成 3 通道 BGR，兼容灰度 / BGRA 输入。"""
        if roi.ndim == 2:
            return cv2.cvtColor(roi, cv2.COLOR_GRAY2BGR)
        if roi.shape[2] == 4:
            return cv2.cvtColor(roi, cv2.COLOR_BGRA2BGR)
        return roi
