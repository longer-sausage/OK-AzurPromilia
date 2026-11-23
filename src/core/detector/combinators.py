"""判据组合器：用组合表达「多区域回退」等策略，避免参数膨胀。

``click_feature`` 曾把「多个候选 box 依次尝试」做进签名的 ``boxes`` 参数，
导致无法混用不同识别源。组合器解决了这个问题。
"""

from __future__ import annotations

from src.core.detector.hit import Hit


class MultiBoxDetector:
    """按顺序尝试多个判据，返回首个命中。

    与 ``click_feature(boxes=[...])`` 的线性回退等价，但**可以混用不同识别源**：

    Example:
        >>> MultiBoxDetector([
        ...     TemplateDetector(FeatureList.confirm_button, box=box1),
        ...     ButtonDetectorAdapter(box=box2),
        ...     OcrDetector("确认", box=box3),
        ... ])

    Args:
        detectors: 判据序列。顺序即优先级。
        name: 判据名称；缺省用各判据名以 ``|`` 连接。
    """

    def __init__(self, detectors, name: str | None = None):
        if not detectors:
            raise ValueError("MultiBoxDetector 至少需要一个判据")
        self._detectors = list(detectors)
        self._name = name or "|".join(
            str(getattr(d, "name", d)) for d in self._detectors
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def detectors(self) -> list:
        """内部判据序列（只读用途，如逐项 attach）。"""
        return self._detectors

    def detect(self, frame) -> Hit | None:
        for detector in self._detectors:
            hit = detector.detect(frame)
            if hit:
                return hit
        return None


class FirstHitDetector(MultiBoxDetector):
    """``MultiBoxDetector`` 的语义别名：取首个命中。

    存在意义是可读性——当判据不是「同一目标的多个区域」而是
    「多个不同目标的优先级选择」时，用这个名字更贴切。
    """


class BlindPointDetector:
    """恒命中判据：永远返回指定坐标的 Hit。

    用于表达「兜底盲点击」，替代 ``click_feature`` 混在主流程里的 ``blind_point`` 参数。
    应放在 ``MultiBoxDetector`` 的最后一项。

    Example:
        >>> MultiBoxDetector([
        ...     TemplateDetector(FeatureList.treasure_key_icon, box=kbox),
        ...     BlindPointDetector(900, 540),      # 都搜不到时盲点一次
        ... ])

    Args:
        x: 点击 X。
        y: 点击 Y。
        name: 判据名称。
    """

    def __init__(self, x: int, y: int, name: str = "blind_point"):
        from ok import Box

        self._box = Box(x, y, 1, 1)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def detect(self, frame) -> Hit | None:
        return Hit(box=self._box, source="blind", raw=None)
