"""固定 Box 的「按钮中央文本带」检测（无 OCR、无模板匹配）。

适用问题
--------
游戏内部分按钮（如「跳过剧情 / 确认」）的底色与游戏背景几乎一致
（深灰按钮 RGB≈(50,50,53)），模板匹配会因背景变化失效，「整块颜色判定」会
把大量背景误判成按钮。这类按钮真正稳定的特征是**按钮中央的文字**：
与底色高对比、位置固定、横向成条带状。

两个核心参数
------------
检测器只认两件事的颜色，其余（位置 / 形状 / 数量）是结构判据：

* ``text_hsv``     —— **文字（前景）颜色**区间，主特征，必填语义；
* ``backdrop_hsv`` —— **底色（背景）颜色**区间，可选辅助特征。

有了这两个参数就能把任意按钮封装成可复用的语义常量：

    SKIP_BUTTON = ButtonThresholds.for_button(
        ((0, 0, 170), (180, 100, 255)),   # 文字：亮色
        ((0, 0, 30), (180, 80, 110)),     # 底色：深灰
        name="skip_button",
    )
    if result := self.find_button(box, thresholds=SKIP_BUTTON):
        self.click(result)

底色参数是否值得开？（真实 1080P 截图，250x60 的框滑窗 884 个位置实测）

* 只用文字特征：误报 17 / 884（命中的都是「底色不对但恰好有横向亮字」的区域）
* 加上底色区间：误报 0 / 884，命中率不变，单次耗时 0.58 → 0.65 ms

结论：底色区间**只在 Box 不够贴合按钮时才明显生效**，代价约 +0.07 ms；
Box 贴合按钮时开不开差别不大，因此默认关闭，按需开启
（``for_button`` / ``with_button_colors`` 传了 ``backdrop_hsv`` 就自动打开）。

流程
----
    Box ──> 裁剪 ROI ──> HSV 文字 Mask ──> 横向闭运算（把字连成条带）
       ──> 取中央窗口 ──> 水平投影找连续文字行（文本带）
       ──> 垂直投影取横向跨度 ──> 数量 / 位置 / 形状 三项判定
       ──> [可选] 底色区间校验
       ──> 命中返回可直接 click() 的 Box，未命中返回 None（不抛异常）

成本
----
只处理传入 Box 的 ROI（通常只有几十像素高），算子全部是 cv2 / numpy 的
向量化操作（inRange、morphologyEx、count_nonzero、flatnonzero），
实测 1920x1080 下 124x32 的 Box 单次检测约 0.36~0.65 ms，适合实时帧循环。

阈值全部集中在 :class:`ButtonThresholds`，便于按具体 UI 微调。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

import cv2
import numpy as np
from ok import Box

#: HSV 三元组 (H, S, V)
HSV = tuple[int, int, int]
#: 一对 HSV 三元组：((h_min, s_min, v_min), (h_max, s_max, v_max))
HSVRanges = tuple[HSV, HSV]


@dataclass(frozen=True)
class ButtonThresholds:
    """检测器全部阈值，颜色相关项都是参数。

    命名约定：

    * ``*_ratio`` —— 相对比例（0~1），相对 ROI 或中央窗口尺寸，天然适配不同分辨率；
    * ``*_px``    —— 绝对像素下限，只在 ROI 极小时兜底；
    * ``text_*``     —— 文字（前景）的 HSV 范围，**主特征**；
    * ``backdrop_*`` —— 底色（背景）的 HSV 范围，**可选辅助特征**。

    构造方式（由粗到细）：

    * :meth:`for_button` —— 只给文字 / 底色颜色，其余用默认值；
    * :meth:`for_dark_button` / :meth:`for_light_button` —— 现成预设；
    * :meth:`with_` —— 在任意基线之上改单项。

    使用前提：**传入的 Box 应当贴合按钮**（与按钮同量级，允许少量留白）。
    若 Box 远大于按钮，文本带相对 Box 会过薄，会被形状判定拒绝。
    """

    # ── 1. 文字颜色（主特征，必填语义） ──────────────────────────
    # 默认 = 深灰按钮上的亮色文字：不限色相、V>=170、S<=100，
    # 覆盖白 / 浅灰 / 浅黄的按钮文字。亮底深字用 for_light_button() 换成
    # (0,0,0)~(180,255,90)，算法其余部分完全不变。
    text_lower: HSV = (0, 0, 170)
    text_upper: HSV = (180, 100, 255)

    # 闭运算核（占 ROI 尺寸的比例，并受像素上限约束）：只用来填补 1~3 px 的
    # 抗锯齿缝隙，让水平投影不被笔画间隙切断。核必须很小——核一大就会把
    # 字间空隙也填掉，整条文本带变成实心块，密度判据随之失效。
    close_kernel_width_ratio: float = 0.01
    close_kernel_height_ratio: float = 0.02
    close_kernel_max_width: int = 3
    close_kernel_max_height: int = 3

    # ── 2. 中央窗口（只看按钮中部，排除 Box 边缘） ───────────────
    center_x_range: tuple[float, float] = (0.30, 0.70)
    center_y_range: tuple[float, float] = (0.22, 0.80)

    # ── 3. 文字像素数量 ──────────────────────────────────────────
    # 中央窗口内文字像素下限 = max(min_text_px, 中央窗口面积 * min_text_ratio)。
    min_text_ratio: float = 0.012
    min_text_px: int = 30
    # 中央文字像素 / Box 内全部文字像素：文字是否集中在中央而不是堆在边缘。
    min_center_ratio: float = 0.55

    # ── 4. 水平投影：连续文字行 = 文本带 ─────────────────────────
    # 单行文字像素下限 = max(min_row_px, 中央窗口宽度 * min_row_fill_ratio)。
    min_row_fill_ratio: float = 0.05
    min_row_px: int = 3
    # 文本带高度（相对中央窗口高度）下限 / 上限。
    min_band_height_ratio: float = 0.15
    min_band_height_px: int = 5
    # 上限默认关闭（1.0）：Box 贴合按钮时文本可能占满中央窗口，
    # 「整块高亮」由下面的密度上限拦截，不需要再用高度上限卡。
    max_band_height_ratio: float = 1.0

    # ── 5. 垂直投影：文本带的横向跨度与形状 ──────────────────────
    min_col_px: int = 2  # 带内单列文字像素下限，抗孤立噪点
    min_band_width_ratio: float = 0.20  # 带宽 / 中央窗口宽度，文本应横向铺开
    min_band_aspect: float = 1.2  # 带宽 / 带高下限：拒绝竖条
    max_band_aspect: float = 40.0  # 上限：拒绝极细的线
    min_band_density: float = 0.08  # 带内填充率下限：拒绝散点
    max_band_density: float = 0.90  # 上限：拒绝整块高亮面板

    # ── 6. 位置：文字重心必须靠近 Box 中心 ───────────────────────
    max_center_offset_x: float = 0.20  # |重心x / Box宽 - 0.5| 的上限
    max_center_offset_y: float = 0.28  # |重心y / Box高 - 0.5| 的上限

    # ── 7. 底色（可选辅助特征，默认关闭） ────────────────────────
    # 底色与背景接近时单靠颜色不可靠，所以默认不参与判定；
    # 传了 backdrop_hsv（能确定按钮底色，例如弹窗固定）时自动开启，
    # 用于排除「文字结构成立但底色不对」的误判。
    # 默认区间 = 深灰按钮 RGB(50,50,53) -> HSV(120,14,53)。
    require_backdrop: bool = False
    backdrop_lower: HSV = (0, 0, 30)
    backdrop_upper: HSV = (180, 80, 110)
    min_backdrop_ratio: float = 0.35  # 非文字像素中落入底色区间的比例下限

    # ── 8. 置信度评分用的「理想值」 ──────────────────────────────
    # 命中后按各指标与理想值的接近程度给出 0.5~1.0 的置信度，
    # 仅用于排序 / 观察，不参与是否命中的判定。
    good_band_height_ratio: float = 0.45
    good_band_width_ratio: float = 0.50
    good_band_density: float = 0.35

    # ── 9. 结果与兜底 ────────────────────────────────────────────
    box_name: str = "button"
    text_box_name: str = "button_text"
    min_roi_size: int = 6  # ROI 宽 / 高下限，低于此直接判未命中

    # ── 构造 ─────────────────────────────────────────────────────
    @classmethod
    def for_button(
        cls,
        text_hsv: HSVRanges,
        backdrop_hsv: HSVRanges | None = None,
        name: str | None = None,
        require_backdrop: bool | None = None,
        **overrides,
    ) -> ButtonThresholds:
        """按「文字颜色 + 底色」生成阈值，便于封装成语义化的按钮常量。

        Args:
            text_hsv: 文字颜色区间 ``((h,s,v), (h,s,v))``，主特征。
            backdrop_hsv: 底色区间；传入后默认开启底色校验。
            name: 结果 Box 的名称，同时作为 ``text_box_name`` 的 ``<name>_text``。
            require_backdrop: 显式指定是否校验底色；不传时按是否给了 backdrop_hsv 决定。
            **overrides: 其他阈值项（同 :meth:`with_`）。

        Returns:
            ButtonThresholds: 新的阈值实例。

        Example:
            >>> SKIP = ButtonThresholds.for_button(
            ...     ((0, 0, 170), (180, 100, 255)),
            ...     ((0, 0, 30), (180, 80, 110)),
            ...     name="skip_button",
            ... )
        """
        lower, upper = text_hsv
        values: dict[str, object] = {"text_lower": lower, "text_upper": upper}
        if backdrop_hsv is not None:
            backdrop_lower, backdrop_upper = backdrop_hsv
            values["backdrop_lower"] = backdrop_lower
            values["backdrop_upper"] = backdrop_upper
        if require_backdrop is None:
            require_backdrop = backdrop_hsv is not None
        values["require_backdrop"] = require_backdrop
        if name:
            values["box_name"] = name
            values.setdefault("text_box_name", f"{name}_text")
        values.update(overrides)
        return DEFAULT_BUTTON_THRESHOLDS.with_(**values)

    @classmethod
    def for_dark_button(cls, **overrides) -> ButtonThresholds:
        """深灰 / 黑色底 + 亮色文字（``跳过剧情`` 那类按钮），即默认行为。"""
        return DEFAULT_BUTTON_THRESHOLDS.with_(**overrides)

    @classmethod
    def for_light_button(cls, **overrides) -> ButtonThresholds:
        """亮色 / 白色底 + 深色文字（高亮主按钮那类）。

        文字取 V<=90 的深色，底色取 V>=170 的低饱和亮色（默认校验底色）。
        """
        values: dict[str, object] = {
            "text_lower": (0, 0, 0),
            "text_upper": (180, 255, 90),
            "backdrop_lower": (0, 0, 170),
            "backdrop_upper": (180, 80, 255),
            "require_backdrop": True,
            "box_name": "light_button",
            "text_box_name": "light_button_text",
        }
        values.update(overrides)
        return DEFAULT_BUTTON_THRESHOLDS.with_(**values)

    def with_(self, **overrides) -> ButtonThresholds:
        """返回改写了部分阈值的副本（不修改基线）。

        Raises:
            TypeError: 传入了不存在的阈值名。
        """
        unknown = sorted(set(overrides) - {f.name for f in fields(self)})
        if unknown:
            raise TypeError(f"未知的阈值参数: {unknown}")
        return replace(self, **overrides)

    def with_button_colors(
        self,
        text_hsv: HSVRanges | None = None,
        backdrop_hsv: HSVRanges | None = None,
        require_backdrop: bool | None = None,
    ) -> ButtonThresholds:
        """只改文字 / 底色颜色，其余阈值沿用本实例。

        Args:
            text_hsv: 文字颜色区间；不传则沿用。
            backdrop_hsv: 底色区间；不传则沿用。
            require_backdrop: 是否校验底色；不传时按是否给了 backdrop_hsv 决定。

        Returns:
            ButtonThresholds: 新的阈值实例。
        """
        values: dict[str, object] = {}
        if text_hsv is not None:
            values["text_lower"], values["text_upper"] = text_hsv
        if backdrop_hsv is not None:
            values["backdrop_lower"], values["backdrop_upper"] = backdrop_hsv
        if require_backdrop is not None:
            values["require_backdrop"] = require_backdrop
        elif backdrop_hsv is not None:
            values["require_backdrop"] = True
        return self.with_(**values) if values else self


#: 深灰 / 黑底 + 亮字预设（默认阈值）
DARK_BUTTON_THRESHOLDS = ButtonThresholds()
#: 默认阈值，即深灰按钮预设
DEFAULT_BUTTON_THRESHOLDS = DARK_BUTTON_THRESHOLDS
#: 亮色 / 白底 + 深字预设（默认校验底色）
LIGHT_BUTTON_THRESHOLDS = ButtonThresholds.for_light_button()


@dataclass(frozen=True)
class ButtonDetection:
    """一次检测的完整结果。

    Attributes:
        matched: 是否命中。
        box: 命中时返回的可直接 ``click()`` 的 Box（即裁剪后的搜索框）。
        text_box: 命中的中央文本带（帧坐标），用于调试 / 校准。
        confidence: 0.5~1.0 的置信度。
        failed: 未命中时的原因标识，便于排查阈值。
        metrics: 中间指标（文字像素数量、文本带尺寸、密度、重心等）。
    """

    matched: bool
    box: Box | None = None
    text_box: Box | None = None
    confidence: float = 0.0
    failed: str = ""
    metrics: dict[str, float] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.matched


def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else float(value)


class ButtonDetector:
    """固定 Box 的按钮检测器：用「中央文本带」判断按钮是否存在。

    检测的是「Box 中央存在一条颜色落在 ``text_hsv`` 内的文本带」，
    文字与底色颜色都是阈值参数，因此同一个类可覆盖：

    * 深灰底 + 亮色文字（默认，``ButtonThresholds.for_dark_button()``）
    * 亮色底 + 深色文字（``ButtonThresholds.for_light_button()``）
    * 任意文字 / 底色（``ButtonThresholds.for_button(text_hsv, backdrop_hsv)``）

    实例只持有阈值，不持有帧数据；请复用实例（框架侧由
    ``RuntimeMixin.button_detector()`` 缓存），不要在每次检测时新建。

    Example:
        >>> detector = ButtonDetector()
        >>> box = task.box_of_screen(0.575, 0.61, 0.64, 0.64)
        >>> result = detector.find(task.next_frame(), box)
        >>> if result:
        ...     task.click(result)
    """

    def __init__(self, thresholds: ButtonThresholds | None = None):
        self.thresholds = thresholds or DEFAULT_BUTTON_THRESHOLDS

    # ── 对外 API ─────────────────────────────────────────────

    def find(self, frame, box, name: str | None = None) -> Box | None:
        """检测固定 Box 内是否存在按钮（默认配置 = 深灰底 + 亮色文字）。

        Args:
            frame: BGR 帧。
            box: 按钮所在区域（``box_of_screen`` 等生成），只检测该区域。
            name: 结果 Box 的名称。

        Returns:
            Box | None: 命中返回可直接 ``click()`` 的 Box，未命中返回 None。
        """
        return self.analyze(frame, box, name=name).box

    def analyze(self, frame, box, name: str | None = None) -> ButtonDetection:
        """执行检测并返回完整结果（含中间指标与未命中原因）。"""
        t = self.thresholds
        if frame is None or box is None:
            return ButtonDetection(False, failed="empty_input")

        frame_height, frame_width = frame.shape[:2]
        x, y, width, height = self._clamp_box(box, frame_width, frame_height)
        if width < t.min_roi_size or height < t.min_roi_size:
            return ButtonDetection(
                False,
                failed="roi_too_small",
                metrics={"roi_width": float(width), "roi_height": float(height)},
            )

        roi = frame[y : y + height, x : x + width]
        if roi.size == 0:
            return ButtonDetection(False, failed="empty_roi")

        hsv = cv2.cvtColor(self._to_bgr(roi), cv2.COLOR_BGR2HSV)
        text_mask = self._text_mask(hsv)
        text_mask = self._close(text_mask, width, height)

        text_total = int(np.count_nonzero(text_mask))
        cx0, cx1 = self._window(width, t.center_x_range)
        cy0, cy1 = self._window(height, t.center_y_range)
        center = text_mask[cy0:cy1, cx0:cx1]
        center_w, center_h = center.shape[1], center.shape[0]

        min_text = max(t.min_text_px, round(center_w * center_h * t.min_text_ratio))
        text_center = int(np.count_nonzero(center))
        center_ratio = text_center / text_total if text_total else 0.0

        metrics: dict[str, float] = {
            "roi_width": float(width),
            "roi_height": float(height),
            "text_total": float(text_total),
            "text_center": float(text_center),
            "min_text": float(min_text),
            "center_ratio": float(center_ratio),
        }

        # 1) 数量：中央必须有足够多的文字像素
        if text_center < min_text:
            return ButtonDetection(False, failed="not_enough_text_pixels", metrics=metrics)
        # 2) 位置：文字必须集中在中央，而不是堆在 Box 边缘
        if center_ratio < t.min_center_ratio:
            return ButtonDetection(False, failed="text_not_centered", metrics=metrics)

        # 3) 水平投影：找连续文字行作为文本带
        row_min = max(t.min_row_px, round(center_w * t.min_row_fill_ratio))
        band_top, band_height = self._longest_true_run(np.count_nonzero(center, axis=1) >= row_min)
        if band_height <= 0:
            return ButtonDetection(False, failed="no_text_row", metrics=metrics)
        min_band_height = max(t.min_band_height_px, round(center_h * t.min_band_height_ratio))
        max_band_height = max(min_band_height, round(center_h * t.max_band_height_ratio))
        if band_height < min_band_height:
            metrics["band_height"] = float(band_height)
            return ButtonDetection(False, failed="band_too_short", metrics=metrics)
        if band_height > max_band_height:
            metrics["band_height"] = float(band_height)
            return ButtonDetection(False, failed="band_too_tall", metrics=metrics)

        # 4) 垂直投影：文本带的横向跨度
        band = center[band_top : band_top + band_height]
        col_counts = np.count_nonzero(band, axis=0)
        active_cols = np.flatnonzero(col_counts >= t.min_col_px)
        if active_cols.size == 0:
            return ButtonDetection(False, failed="no_text_column", metrics=metrics)
        band_x0 = int(active_cols[0])
        band_x1 = int(active_cols[-1]) + 1
        band_width = band_x1 - band_x0
        if band_width < max(1, round(center_w * t.min_band_width_ratio)):
            metrics["band_width"] = float(band_width)
            return ButtonDetection(False, failed="band_too_narrow", metrics=metrics)

        # 5) 形状：宽高比 + 填充率（文本是「稀疏但连续的横条」，不是散点也不是实心块）
        band_pixels = int(np.count_nonzero(band[:, band_x0:band_x1]))
        density = band_pixels / max(1, band_width * band_height)
        aspect = band_width / max(1, band_height)
        metrics.update(
            {
                "band_x": float(band_x0),
                "band_y": float(band_top),
                "band_width": float(band_width),
                "band_height": float(band_height),
                "band_pixels": float(band_pixels),
                "band_density": float(density),
                "band_aspect": float(aspect),
            }
        )
        if not t.min_band_density <= density <= t.max_band_density:
            return ButtonDetection(False, failed="bad_band_density", metrics=metrics)
        if not t.min_band_aspect <= aspect <= t.max_band_aspect:
            return ButtonDetection(False, failed="bad_band_aspect", metrics=metrics)

        # 6) 重心：文本带应落在 Box 中央
        rows, cols = np.nonzero(band[:, band_x0:band_x1])
        centroid_x = (cx0 + band_x0 + float(cols.mean())) / width
        centroid_y = (cy0 + band_top + float(rows.mean())) / height
        metrics["centroid_x"] = centroid_x
        metrics["centroid_y"] = centroid_y
        if abs(centroid_x - 0.5) > t.max_center_offset_x or abs(centroid_y - 0.5) > t.max_center_offset_y:
            return ButtonDetection(False, failed="off_center", metrics=metrics)

        # 7) 可选：底色辅助判定
        if t.require_backdrop:
            backdrop_ratio = self._backdrop_ratio(hsv, text_mask)
            metrics["backdrop_ratio"] = backdrop_ratio
            if backdrop_ratio < t.min_backdrop_ratio:
                return ButtonDetection(False, failed="backdrop_mismatch", metrics=metrics)

        confidence = self._confidence(
            band_height / center_h,
            band_width / center_w,
            density,
            centroid_x,
            centroid_y,
        )
        metrics["confidence"] = confidence
        return ButtonDetection(
            matched=True,
            box=Box(x, y, width, height, confidence, name or t.box_name),
            text_box=Box(
                x + cx0 + band_x0,
                y + cy0 + band_top,
                band_width,
                band_height,
                confidence,
                t.text_box_name,
            ),
            confidence=confidence,
            metrics=metrics,
        )

    # ── 内部步骤 ─────────────────────────────────────────────

    def _text_mask(self, hsv: np.ndarray) -> np.ndarray:
        """按文字（前景）颜色范围取 Mask，具体颜色由阈值决定。"""
        t = self.thresholds
        return cv2.inRange(hsv, np.array(t.text_lower, np.uint8), np.array(t.text_upper, np.uint8))

    def _close(self, mask: np.ndarray, width: int, height: int) -> np.ndarray:
        """横向闭运算：把分立的字形笔画连成一条文本带。"""
        t = self.thresholds
        kw = min(t.close_kernel_max_width, self._odd(width * t.close_kernel_width_ratio, 1))
        kh = min(t.close_kernel_max_height, self._odd(height * t.close_kernel_height_ratio, 1))
        if kw <= 1 and kh <= 1:
            return mask
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh))
        return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    def _backdrop_ratio(self, hsv: np.ndarray, text_mask: np.ndarray) -> float:
        """非文字像素中落入底色区间的比例。"""
        t = self.thresholds
        backdrop = cv2.inRange(hsv, np.array(t.backdrop_lower, np.uint8), np.array(t.backdrop_upper, np.uint8))
        backdrop_only = cv2.bitwise_and(backdrop, cv2.bitwise_not(text_mask))
        non_text = max(1, int(hsv.shape[0] * hsv.shape[1]) - int(np.count_nonzero(text_mask)))
        return int(np.count_nonzero(backdrop_only)) / non_text

    def _confidence(
        self,
        band_height_ratio: float,
        band_width_ratio: float,
        density: float,
        centroid_x: float,
        centroid_y: float,
    ) -> float:
        """按各指标与「理想文本带」的接近程度给出 0.5~1.0 的置信度。"""
        t = self.thresholds
        height_score = _clamp01(band_height_ratio / t.good_band_height_ratio)
        width_score = _clamp01(band_width_ratio / t.good_band_width_ratio)
        density_score = _clamp01(1 - abs(density - t.good_band_density) / t.good_band_density)
        offset = max(abs(centroid_x - 0.5), abs(centroid_y - 0.5)) / 0.5
        center_score = _clamp01(1 - offset)
        score = (height_score + width_score + density_score + center_score) / 4
        return round(0.5 + 0.5 * score, 4)

    # ── 工具 ─────────────────────────────────────────────────

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

    @staticmethod
    def _odd(value: float, minimum: int) -> int:
        size = max(minimum, round(value))
        return size if size % 2 == 1 else size + 1

    @staticmethod
    def _window(size: int, ratio_range: tuple[float, float]) -> tuple[int, int]:
        """按相对区间计算中央窗口的 [start, end)。"""
        start = max(0, min(round(size * ratio_range[0]), size - 1))
        end = max(start + 1, min(round(size * ratio_range[1]), size))
        return start, end

    @staticmethod
    def _longest_true_run(flags: np.ndarray) -> tuple[int, int]:
        """返回布尔数组中最长连续 True 段的 (起始下标, 长度)。"""
        padded = np.concatenate(([0], flags.astype(np.uint8), [0]))
        edges = np.diff(padded)
        starts = np.flatnonzero(edges == 1)
        if starts.size == 0:
            return 0, 0
        lengths = np.flatnonzero(edges == -1) - starts
        best = int(np.argmax(lengths))
        return int(starts[best]), int(lengths[best])
