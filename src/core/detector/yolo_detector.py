"""YOLO 判据：包装 ``yolo_detect``，并提供多候选选择策略。

``yolo_detect`` 返回 ``list[Box]``（按置信度降序），选哪个是业务决策，
因此 ``pick`` 是本适配器的核心参数。
"""

from __future__ import annotations

from ok import Box

from src.core.detector.hit import SOURCE_YOLO, Hit

# 多候选选择策略
PICK_MAX_CONF = "max_conf"
PICK_FIRST = "first"
PICK_CENTER = "center"
PICK_LEFTMOST = "leftmost"
PICK_TOPMOST = "topmost"

PICK_STRATEGIES = (PICK_MAX_CONF, PICK_FIRST, PICK_CENTER, PICK_LEFTMOST, PICK_TOPMOST)


class YoloDetector:
    """用 YOLO 检测目标。

    Args:
        name: 目标名称或名称列表（对应 ``yolo_detect`` 的 ``name``）。
        box: 裁剪检测区域；同时作为 ``pick="center"`` 的中心基准。
        conf: 置信度阈值。
        model_key: 指定模型键；None 时由框架按首个 name 解析。
        pick: 多候选选择策略，见本模块 ``PICK_*`` 常量。
        task_name: 判据名称；缺省由 name 推导（多个 name 时用 ``_`` 连接）。

    Example:
        >>> YoloDetector("target", box=roi, conf=0.7)               # 置信度最高
        >>> YoloDetector(["a", "b"], box=roi, pick=PICK_LEFTMOST)  # 最左
    """

    def __init__(
        self,
        name,
        box: Box | None = None,
        conf: float = 0.7,
        model_key: str | None = None,
        pick: str = PICK_MAX_CONF,
        task_name: str | None = None,
    ):
        if pick not in PICK_STRATEGIES:
            raise ValueError(f"未知的 pick 策略 {pick!r}，可选：{PICK_STRATEGIES}")
        self._name = name
        self._box = box
        self._conf = conf
        self._model_key = model_key
        self._pick = pick
        names = [name] if isinstance(name, str) else list(name)
        self._label = task_name or "_".join(str(n) for n in names)
        self._task = None

    @property
    def name(self) -> str:
        return self._label

    def detect(self, frame) -> Hit | None:
        if self._task is None:
            raise RuntimeError(
                f"{self.__class__.__name__} 未绑定任务宿主，"
                "请先调用 attach(task) 或通过任务辅助方法（如 wait_action_result / detect_with_scroll）调用"
            )
        boxes = self._task.yolo_detect(
            name=self._name,
            frame=frame,
            box=self._box,
            conf=self._conf,
            model_key=self._model_key,
        )
        if not boxes:
            return None
        picked = self._pick_box(boxes)
        if picked is None:
            return None
        return Hit.from_box(picked, source=SOURCE_YOLO)

    def _pick_box(self, boxes: list[Box]) -> Box | None:
        """按策略从候选框中选一个。"""
        if self._pick == PICK_MAX_CONF:
            return max(boxes, key=lambda b: float(getattr(b, "confidence", 0.0) or 0.0))
        if self._pick == PICK_FIRST:
            return boxes[0]
        if self._pick == PICK_LEFTMOST:
            return min(boxes, key=lambda b: b.x)
        if self._pick == PICK_TOPMOST:
            return min(boxes, key=lambda b: b.y)
        # PICK_CENTER：离搜索区域中心最近；无 box 时退化为离画面中心最近
        anchor_x, anchor_y = self._anchor_center()
        return min(
            boxes,
            key=lambda b: (b.center()[0] - anchor_x) ** 2 + (b.center()[1] - anchor_y) ** 2,
        )

    def _anchor_center(self) -> tuple[float, float]:
        if self._box is None:
            width = getattr(self._task, "width", 0) or 0
            height = getattr(self._task, "height", 0) or 0
            if callable(width):
                width = width()
            if callable(height):
                height = height()
            return width / 2.0, height / 2.0
        return self._box.x + self._box.width / 2.0, self._box.y + self._box.height / 2.0

    def attach(self, task) -> "YoloDetector":
        """绑定宿主任务（识别需要调用任务上的 ``yolo_detect``）。"""
        self._task = task
        return self
