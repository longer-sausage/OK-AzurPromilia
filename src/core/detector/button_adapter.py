"""按钮检测判据：包装 ``find_button``（固定 Box + 中央文本带，无 OCR）。

阈值参数沿用 ``find_button`` 的约定：可以传 ``thresholds``（``ButtonThresholds``），
也可以直接传 ``text_hsv`` / ``backdrop_hsv`` 颜色区间。
"""

from __future__ import annotations

from src.core.detector.hit import SOURCE_BUTTON, Hit


class ButtonDetectorAdapter:
    """在固定 Box 内用「中央文本带」判断按钮是否出现。

    Args:
        box: 按钮所在区域。**必须贴合按钮**——Box 远大于按钮时文本带相对过薄，
            会被形状判定拒绝。
        thresholds: 完整阈值（``ButtonThresholds``）。
        text_hsv: 文字颜色区间。
        backdrop_hsv: 底色区间；传入即默认开启底色校验。
        require_backdrop: 显式指定是否校验底色。
        name: 判据名称。

    Example:
        >>> ButtonDetectorAdapter(box=self.box_of_screen(0.575, 0.61, 0.64, 0.64))
        >>> ButtonDetectorAdapter(box=box, thresholds=SKIP_BUTTON)
    """

    def __init__(
        self,
        box,
        thresholds=None,
        text_hsv=None,
        backdrop_hsv=None,
        require_backdrop: bool | None = None,
        name: str = "button",
    ):
        self._box = box
        self._thresholds = thresholds
        self._text_hsv = text_hsv
        self._backdrop_hsv = backdrop_hsv
        self._require_backdrop = require_backdrop
        self._name = name
        self._task = None

    @property
    def name(self) -> str:
        return self._name

    def detect(self, frame) -> Hit | None:
        if self._task is None:
            raise RuntimeError(
                f"{self.__class__.__name__} 未绑定任务宿主，"
                "请先调用 attach(task) 或通过任务辅助方法（如 wait_action_result / detect_with_scroll）调用"
            )
        result = self._task.find_button(
            self._box,
            frame=frame,
            thresholds=self._thresholds,
            text_hsv=self._text_hsv,
            backdrop_hsv=self._backdrop_hsv,
            require_backdrop=self._require_backdrop,
        )
        if not result:
            return None
        return Hit.from_box(result, source=SOURCE_BUTTON)

    def attach(self, task) -> "ButtonDetectorAdapter":
        """绑定宿主任务（识别需要调用任务上的 ``find_button``）。"""
        self._task = task
        return self
