"""OCR 判据：包装 ``ocr``。

实测确认（``ok/task/task.py:818``）：``ocr()`` 返回 **``list[Box]``（按 y 坐标排序）**，
未命中返回 ``[]``。因此本适配器无需做坐标提取。

另注：``ocr()`` 内部会自动画调试框（``emit_draw_box("ocr"+name, ...)``），
所以 ``wait_action_result(draw=True)`` 对 OCR 判据不应重复绘制。
"""

from __future__ import annotations

import re

from ok import Box

from src.core.detector.hit import SOURCE_OCR, Hit

# 多候选选择策略（与 YoloDetector 保持同一套语义）
PICK_FIRST = "first"
PICK_MAX_CONF = "max_conf"
PICK_CENTER = "center"
PICK_LEFTMOST = "leftmost"
PICK_TOPMOST = "topmost"

PICK_STRATEGIES = (PICK_FIRST, PICK_MAX_CONF, PICK_CENTER, PICK_LEFTMOST, PICK_TOPMOST)


class OcrDetector:
    """用 OCR 文本匹配判断目标是否出现。

    区域可以整体用 ``box`` 给出，也可以用相对坐标 ``x/y/to_x/to_y``
    （与 ``ocr`` 的参数一致）。

    Args:
        match: 文本、文本列表、正则或其列表。
        box: 识别区域；与 x/y/to_x/to_y 二选一。
        x, y, to_x, to_y: 相对区域坐标。
        threshold: OCR 置信度阈值；0 用框架默认。
        lib: OCR 引擎名。
        target_height: 识别前缩放高度。
        pick: 多候选选择策略。
        name: 判据名称；缺省用 match 的可读形式。

    Example:
        >>> OcrDetector("确认", box=confirm_area, threshold=0.8)
        >>> OcrDetector(re.compile(r"第\\d+页"), box=page_area, pick=PICK_TOPMOST)
    """

    def __init__(
        self,
        match,
        box: Box | None = None,
        x: float = 0,
        y: float = 0,
        to_x: float = 1,
        to_y: float = 1,
        threshold: float = 0,
        lib: str = "default",
        target_height: int = 0,
        pick: str = PICK_FIRST,
        name: str | None = None,
    ):
        if pick not in PICK_STRATEGIES:
            raise ValueError(f"未知的 pick 策略 {pick!r}，可选：{PICK_STRATEGIES}")
        self._match = match
        self._box = box
        self._region = {"x": x, "y": y, "to_x": to_x, "to_y": to_y}
        self._threshold = threshold
        self._lib = lib
        self._target_height = target_height
        self._pick = pick
        self._name = name or self._match_label(match)
        self._task = None

    @staticmethod
    def _match_label(match) -> str:
        if isinstance(match, (list, tuple)):
            return "|".join(str(getattr(m, "pattern", m)) for m in match)
        return str(getattr(match, "pattern", match))

    @property
    def name(self) -> str:
        return self._name

    def detect(self, frame) -> Hit | None:
        if self._task is None:
            raise RuntimeError(
                f"{self.__class__.__name__} 未绑定任务宿主，"
                "请先调用 attach(task) 或通过任务辅助方法（如 wait_action_result / detect_with_scroll）调用"
            )
        boxes = self._task.ocr(
            box=self._box,
            match=self._match,
            threshold=self._threshold,
            frame=frame,
            target_height=self._target_height,
            lib=self._lib,
            **self._region,
        )
        if not boxes:
            return None
        picked = self._pick_box(boxes)
        if picked is None:
            return None
        return Hit(
            box=picked,
            confidence=float(getattr(picked, "confidence", 1.0) or 1.0),
            source=SOURCE_OCR,
            text=str(getattr(picked, "name", "") or ""),
            raw=boxes,
        )

    def _pick_box(self, boxes: list[Box]) -> Box | None:
        if self._pick == PICK_FIRST:
            return boxes[0]
        if self._pick == PICK_MAX_CONF:
            return max(boxes, key=lambda b: float(getattr(b, "confidence", 0.0) or 0.0))
        if self._pick == PICK_LEFTMOST:
            return min(boxes, key=lambda b: b.x)
        if self._pick == PICK_TOPMOST:
            return min(boxes, key=lambda b: b.y)
        anchor_x, anchor_y = self._anchor_center()
        return min(
            boxes,
            key=lambda b: (b.center()[0] - anchor_x) ** 2 + (b.center()[1] - anchor_y) ** 2,
        )

    def _anchor_center(self) -> tuple[float, float]:
        if self._box is not None:
            return self._box.x + self._box.width / 2.0, self._box.y + self._box.height / 2.0
        width = getattr(self._task, "width", 0) or 0
        height = getattr(self._task, "height", 0) or 0
        if callable(width):
            width = width()
        if callable(height):
            height = height()
        if width and height:
            return (
                (self._region["x"] + self._region["to_x"]) / 2.0 * width,
                (self._region["y"] + self._region["to_y"]) / 2.0 * height,
            )
        return 0.0, 0.0

    def attach(self, task) -> "OcrDetector":
        """绑定宿主任务（识别需要调用任务上的 ``ocr``）。"""
        self._task = task
        return self
