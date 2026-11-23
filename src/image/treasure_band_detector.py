"""宝箱开锁「颜色带」检测（HSV + 连通域，无 OCR、无模板匹配）。

适用问题
--------
开锁小游戏的右侧轨道里，颜色带表现为**低饱和、高亮度、竖向细长的矩形块**。
同一区域里还存在两类干扰：

* 轨道边框 / 轨道本体 —— 贴着 ROI 左右边缘的竖条（``11 x 155`` 那种）；
* 横向钥匙 / 指针结构 —— 亮色但矮胖（``43 x 18`` 那种）。

两者都不是颜色问题，而是**形状问题**，因此统一用连通域 + 形状判据过滤，
色相不限（H 取全区间），只认「亮 + 低饱和」。

流程
----
    Box ──> 裁剪 ROI ──> RGB→HSV ──> inRange(S 低 / V 高) ──> 开运算去噪
       ──> 连通域 ──> 逐块过滤（面积 / 宽度 / 高度 / 长宽比 / 贴边）
       ──> 按 Y 从上到下排序 ──> 返回帧坐标 Box 列表

判定条件（默认，取自 1920x1080 实测样本）
------------------------------------
    area    >= 300      # 16x105 / 19x53 都远大于此
    width   <= 30       # 排除过宽的块
    height  >= 30       # 排除横向结构的碎片
    h / w   >= 2.0      # 关键：43x18 的横向钥匙在这里被排除
    不贴 ROI 左右边缘    # 排除轨道边框

坐标约定
--------
返回的 ``Box`` 是**整帧坐标**（不是 ROI 内相对坐标），可直接 ``click()`` /
交给 ``draw_boxes()`` 画在覆盖层上。框采用 OpenCV 语义：
``x, y`` 为左上，``width / height`` 为实际宽高（右下角 = x + width）。

阈值全部集中在 :class:`BandThresholds`，便于按具体画面微调。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

import cv2
import numpy as np
from ok import Box

#: HSV 三元组 (H, S, V)
HSV = tuple[int, int, int]


@dataclass(frozen=True)
class BandThresholds:
    """颜色带检测的全部阈值。

    命名约定：

    * ``min_*`` / ``max_*`` —— 过滤上下限，单位像素（长宽比除外）；
    * ``lower`` / ``upper`` —— HSV 区间，**色相不限**，只用 S / V 两个维度。

    默认区间 ``(0, 0, 190) ~ (180, 80, 255)`` 即「不限色相 + S<=80 + V>=190」，
    覆盖白 / 浅灰 / 浅青等开锁界面出现过的颜色带。
    """

    # ── 1. 颜色（主特征） ────────────────────────────────────────
    lower: HSV = (0, 0, 190)
    upper: HSV = (180, 80, 255)

    # ── 2. 形态学去噪 ────────────────────────────────────────────
    # 3x3 开运算：去掉 1~2 px 的抗锯齿噪点，不会把竖向色带切断。
    morph_kernel: int = 3

    # ── 3. 形状过滤 ──────────────────────────────────────────────
    min_area: int = 300  # boundingRect 面积下限
    max_width: int = 30  # 宽度上限：排除轨道整体等宽块
    min_height: int = 30  # 高度下限
    min_aspect: float = 2.0  # height / width 下限：排除横向钥匙 / 指针

    # ── 4. 贴边过滤 ──────────────────────────────────────────────
    # 距 ROI 左右边缘小于该值的连通域视为轨道边框，直接丢弃。
    edge_margin: int = 1

    # ── 5. 输出 ──────────────────────────────────────────────────
    box_name: str = "treasure_band"

    def with_(self, **overrides) -> BandThresholds:
        """返回改写了部分阈值的副本（不修改基线）。

        Raises:
            TypeError: 传入了不存在的阈值名。
        """
        unknown = sorted(set(overrides) - {f.name for f in fields(self)})
        if unknown:
            raise TypeError(f"未知的阈值参数: {unknown}")
        return replace(self, **overrides)


#: 默认阈值
DEFAULT_BAND_THRESHOLDS = BandThresholds()


@dataclass(frozen=True)
class BandDetection:
    """单个连通域的检测结果。

    Attributes:
        matched: 是否通过全部过滤（是真正的颜色带）。
        box: 命中时的帧坐标 Box；未命中为 None。
        failed: 未命中原因，便于排查阈值（``area`` / ``too_wide`` / ``too_short``
            / ``not_vertical`` / ``on_edge``）。
        width / height / area / aspect: 该连通域的实际形状指标。
    """

    matched: bool
    box: Box | None = None
    failed: str = ""
    width: int = 0
    height: int = 0
    area: int = 0
    aspect: float = 0.0
    x: int = 0
    y: int = 0

    def __bool__(self) -> bool:
        return self.matched


class TreasureBandDetector:
    """宝箱开锁界面颜色带检测器。

    实例只持有阈值，不持有帧数据；请复用实例，不要在每次检测时新建。

    Example:
        >>> detector = TreasureBandDetector()
        >>> box = task.box_of_screen(0.68, 0.232, 0.702, 0.778)
        >>> bands = detector.find(task.next_frame(), box)
        >>> task.draw_boxes("treasure_band", bands, color="red")
        >>> [(b.y, b.y + b.height) for b in bands]
        [(286, 391), (552, 605), (672, 725)]
    """

    def __init__(self, thresholds: BandThresholds | None = None):
        self.thresholds = thresholds or DEFAULT_BAND_THRESHOLDS

    # ── 对外 API ─────────────────────────────────────────────

    def find(self, frame, box, name: str | None = None) -> list[Box]:
        """检测 Box 内的所有颜色带。

        Args:
            frame: BGR 帧。
            box: 颜色带所在区域（``box_of_screen`` 生成）。
            name: 结果 Box 的名称。

        Returns:
            list[Box]: 按 Y 从上到下排序的帧坐标 Box；无命中返回空列表。
        """
        return [item.box for item in self.analyze(frame, box, name=name) if item.box]

    def analyze(self, frame, box, name: str | None = None) -> list[BandDetection]:
        """执行检测并返回**全部**连通域的判定结果（含被过滤项）。

        与 :meth:`find` 的区别：被过滤掉的候选也会返回，``failed`` 给出原因，
        便于在覆盖层上用另一颜色画出来做阈值校准。

        Args:
            frame: BGR 帧。
            box: 颜色带所在区域。
            name: 命中 Box 的名称。

        Returns:
            list[BandDetection]: 按 Y 从上到下排序，命中与未命中混合。
        """
        t = self.thresholds
        if frame is None or box is None:
            return []

        frame_height, frame_width = frame.shape[:2]
        x, y, width, height = self._clamp_box(box, frame_width, frame_height)
        if width <= 0 or height <= 0:
            return []

        roi = frame[y : y + height, x : x + width]
        if roi.size == 0:
            return []

        mask = self._mask(roi)
        contours = self._contours(mask)
        roi_w = mask.shape[1]

        results: list[BandDetection] = []
        for contour in contours:
            bx, by, bw, bh = cv2.boundingRect(contour)
            area = bw * bh
            aspect = bh / max(bw, 1)
            common = dict(width=bw, height=bh, area=area, aspect=aspect, x=x + bx, y=y + by)

            if area < t.min_area:
                results.append(BandDetection(False, failed="area", **common))
                continue
            if bw > t.max_width:
                results.append(BandDetection(False, failed="too_wide", **common))
                continue
            if bh < t.min_height:
                results.append(BandDetection(False, failed="too_short", **common))
                continue
            if aspect < t.min_aspect:
                results.append(BandDetection(False, failed="not_vertical", **common))
                continue
            if bx <= t.edge_margin or bx + bw >= roi_w - t.edge_margin:
                results.append(BandDetection(False, failed="on_edge", **common))
                continue

            results.append(
                BandDetection(
                    matched=True,
                    box=Box(
                        x + bx,
                        y + by,
                        bw,
                        bh,
                        self._confidence(aspect, bh),
                        name or t.box_name,
                    ),
                    **common,
                )
            )

        results.sort(key=lambda item: item.y)
        return results

    def presence(self, frame, box) -> float:
        """统计 Box 内落在 HSV 区间的像素占比（0~1），用于存在 / 消失判定。

        为什么不用 :meth:`find`：钥匙是横向结构，压在条带上会把条带切成上下两段，
        连通域各自变小后会被形状判据拒绝，导致「明明还在却被判消失」。
        存在性只关心「这块区域里还有没有那种亮色像素」，不看形状，因此抗遮挡。

        Args:
            frame: BGR 帧。
            box: 待判定的区域（通常用校准保存下来的条带 bbox）。

        Returns:
            float: 0~1 的占比；区域非法或为空时返回 0.0。

        Example:
            >>> detector.presence(frame, calibrated_band) > 0.15
            True
        """
        if frame is None or box is None:
            return 0.0
        x, y, width, height = self._clamp_box(box, frame.shape[1], frame.shape[0])
        if width <= 0 or height <= 0:
            return 0.0
        mask = self._mask(frame[y : y + height, x : x + width])
        return round(float(np.count_nonzero(mask)) / float(width * height), 4)

    @staticmethod
    def y_ranges(boxes) -> list[tuple[int, int]]:
        """把 Box 列表压成 Y 区间列表，用于与钥匙 center_y 比对。

        Args:
            boxes: :meth:`find` 的返回值。

        Returns:
            list[tuple[int, int]]: ``[(top, bottom), ...]``，按 Y 升序。

        Example:
            >>> TreasureBandDetector.y_ranges(bands)
            [(286, 391), (552, 605), (672, 725)]
        """
        ordered = sorted(boxes, key=lambda b: b.y)
        return [(b.y, b.y + b.height) for b in ordered]

    @staticmethod
    def hit_by_center_y(boxes, center_y: int) -> Box | None:
        """返回 Y 区间包含 ``center_y`` 的颜色带（钥匙命中判定）。

        Args:
            boxes: :meth:`find` 的返回值。
            center_y: 钥匙图标中心 Y（帧坐标）。

        Returns:
            Box | None: 命中的颜色带，未命中返回 None。
        """
        for b in boxes:
            if b.y <= center_y <= b.y + b.height:
                return b
        return None

    # ── 内部步骤 ─────────────────────────────────────────────

    def _mask(self, roi: np.ndarray) -> np.ndarray:
        """HSV inRange + 开运算去噪。"""
        t = self.thresholds
        hsv = cv2.cvtColor(self._to_bgr(roi), cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(t.lower, np.uint8), np.array(t.upper, np.uint8))
        size = max(1, int(t.morph_kernel))
        if size <= 1:
            return mask
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    @staticmethod
    def _contours(mask: np.ndarray):
        """取外轮廓，兼容 OpenCV 3 / 4 的返回值差异。"""
        found = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return found[0] if len(found) == 2 else found[1]

    @staticmethod
    def _confidence(aspect: float, height: int) -> float:
        """把形状指标折算成 0.5~1.0 的置信度，仅用于展示 / 排序。"""
        aspect_score = min(1.0, aspect / 4.0)
        height_score = min(1.0, height / 80.0)
        return round(0.5 + 0.5 * (aspect_score + height_score) / 2, 4)

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
