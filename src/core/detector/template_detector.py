"""模板匹配判据：包装 ``find_feature`` / ``find_one``。

注意事项（来自项目既有踩坑）：

* ``find_feature`` **不传 box 时只搜 coco 标注位置 ±variance（默认 0.002 ≈ 4px）**。
  会移动的图标必须显式传 ``box``，否则永远搜不到。
* ``boxes`` 多区域回退不在这里做——用 ``MultiBoxDetector`` 组合多个判据，
  这样还能混用不同识别源。
"""

from __future__ import annotations

from ok import Box

from src.core.detector.hit import SOURCE_TEMPLATE, Hit

# 与框架一致的默认 variance
DEFAULT_VARIANCE = 0.05


class TemplateDetector:
    """用模板匹配判断特征是否出现。

    Args:
        feature: 特征名（``FeatureList`` 成员或字符串）。对应 ``find_feature``
            的 ``feature_name``。
        box: 搜索区域。``None`` 时沿用 coco 标注位置 ±variance（只适合静止图标）。
        horizontal_variance: 水平方差。
        vertical_variance: 垂直方差。
        threshold: 匹配阈值；0 表示用框架默认。
        target_height: 目标缩放高度；0 表示不缩放。
        mask_function: 掩膜函数（如 ``make_hsv_isolator(...)`` 的结果）。
        use_gray_scale: 是否转灰度匹配。
        canny_lower / canny_higher: 边缘检测阈值。
        name: 判据名称；缺省用特征名。
        use_find_one: True 走 ``find_one``，False 走 ``find_feature``。
            ``find_feature`` 不传 box 时只搜标注位置，但支持 ``Frame`` 对象缓存；
            ``find_one`` 支持 ``feature`` 列表。按需选择。

    Example:
        >>> TemplateDetector(FeatureList.treasure_icon)
        >>> TemplateDetector(FeatureList.treasure_key_icon, box=self._key_box())
    """

    def __init__(
        self,
        feature,
        box: Box | None = None,
        horizontal_variance: float = 0.0,
        vertical_variance: float = 0.0,
        threshold: float = 0,
        target_height: int = 0,
        mask_function=None,
        use_gray_scale: bool = False,
        canny_lower: float = 0,
        canny_higher: float = 0,
        name: str | None = None,
        use_find_one: bool = False,
    ):
        self._feature = feature
        self._box = box
        self._horizontal_variance = horizontal_variance
        self._vertical_variance = vertical_variance
        self._threshold = threshold
        self._target_height = target_height
        self._mask_function = mask_function
        self._use_gray_scale = use_gray_scale
        self._canny_lower = canny_lower
        self._canny_higher = canny_higher
        self._name = name or str(getattr(feature, "value", feature))
        self._use_find_one = use_find_one
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
        # task 由 RuntimeMixin 在调用前注入（见 attach）
        if self._use_find_one:
            result = self._task.find_one(
                feature=self._feature,
                horizontal_variance=self._horizontal_variance,
                vertical_variance=self._vertical_variance,
                threshold=self._threshold,
                box=self._box,
                frame=frame,
            )
        else:
            result = self._task.find_feature(
                feature_name=self._feature,
                box=self._box,
                frame=frame,
                horizontal_variance=self._horizontal_variance or DEFAULT_VARIANCE,
                vertical_variance=self._vertical_variance or DEFAULT_VARIANCE,
                threshold=self._threshold,
                target_height=self._target_height,
                mask_function=self._mask_function,
            )
        if not result:
            return None
        return Hit.from_box(result, source=SOURCE_TEMPLATE)

    def attach(self, task) -> "TemplateDetector":
        """绑定宿主任务（识别需要调用任务上的 ``find_feature`` / ``find_one``）。

        由 ``RuntimeMixin`` 在解析判据时自动调用；手动构造后直接传给
        ``wait_action_result`` 也可以，编排层会代劳。

        Args:
            task: 宿主任务对象。

        Returns:
            TemplateDetector: self，便于链式调用。
        """
        self._task = task
        return self
