"""判据协议：把四类识别源归一成 ``detect(frame) -> Hit | None``。

协议刻意只要求一个方法，因为编排层只需要「给一帧，判断命中与否」。
**判据必须接受外部传入的 frame**：若判据内部自行取帧，同一轮编排会多次截图，
既慢又会出现「条件用的是第 3 帧、动作发生在第 5 帧」的时序错乱。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ok import Box

from src.core.detector.hit import SOURCE_PREDICATE, Hit


@runtime_checkable
class Detector(Protocol):
    """识别判据协议。

    Example:
        >>> class AlwaysHit:
        ...     name = "always"
        ...     def detect(self, frame):
        ...         return Hit(box=Box(0, 0, 1, 1))
    """

    def detect(self, frame) -> Hit | None:
        """在给定帧上做一次识别。

        Args:
            frame: BGR 帧。不得忽略此参数改为自行取帧。

        Returns:
            Hit | None: 命中返回 Hit，未命中返回 None。
        """
        ...

    @property
    def name(self) -> str:
        """判据名称，用于日志与调试框分组。"""
        ...


class PredicateDetector:
    """把任意「帧 → 真值」的可调用对象包装成判据。

    这是适配器的兜底入口：当某个识别逻辑不值得单独写适配器时用它。
    支持两种返回约定：

    * 返回 ``Box`` / ``Hit`` / 其他真值 → 视为命中。``Box`` 会被包装成 Hit，
      其他真值（如 ``True``）需要 ``box`` 参数提供可点击目标。
    * 返回 ``None`` / ``False`` → 未命中。

    Args:
        predicate: ``frame -> Box | Hit | bool | None``。
        box: 当 predicate 只返回布尔值时用于点击的目标框；可为 ``None``
            （此时仅做条件判断，不可用于点击）。
        name: 判据名称。
        source: 识别源标识。

    Example:
        >>> # 判据只关心「有没有」：点击目标由 box 提供
        >>> PredicateDetector(lambda f: f.mean() > 100, box=target_box)
        >>> # 判据直接给出目标
        >>> PredicateDetector(lambda f: self.find_confirm(), name="confirm")
    """

    def __init__(self, predicate, box: Box | None = None, name: str = "predicate", source: str = SOURCE_PREDICATE):
        self._predicate = predicate
        self._box = box
        self._name = name
        self._source = source

    @property
    def name(self) -> str:
        return self._name

    def detect(self, frame) -> Hit | None:
        result = self._predicate(frame)
        if not result:
            return None
        if isinstance(result, Hit):
            return result
        if isinstance(result, Box):
            return Hit.from_box(result, source=self._source)
        # 纯布尔判据：点击目标由构造时的 box 提供
        if self._box is None:
            return None
        return Hit(box=self._box, source=self._source, raw=result)


class InvertedDetector:
    """取反判据：命中条件为「原判据 **未** 命中」。

    用于表达「等待某元素消失」。**点击目标无法从取反判据推出**，
    因此必须显式提供 ``box``（通常就是消失前那个元素的位置）。

    Args:
        detector: 被取反的判据。
        box: 取反命中时可点击的目标框。
        name: 判据名称；缺省为 ``not <原判据名>``。

    Example:
        >>> # 等待确认按钮消失
        >>> InvertedDetector(TemplateDetector(FeatureList.confirm_button, box=box),
        ...                  box=box)
    """

    def __init__(self, detector: Detector, box: Box | None = None, name: str | None = None):
        self._detector = detector
        self._box = box
        self._name = name or f"not_{getattr(detector, 'name', 'detector')}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def detectors(self) -> list:
        """内部判据序列（供宿主任务递归 attach）。"""
        return [self._detector]

    def attach(self, task) -> "InvertedDetector":
        """绑定宿主任务（透传给内部判据）。"""
        attach = getattr(self._detector, "attach", None)
        if callable(attach):
            attach(task)
        return self

    def detect(self, frame) -> Hit | None:
        if self._detector.detect(frame):
            return None
        if self._box is None:
            return None
        # source 只用于日志辨识；取反后的语义是「原判据不成立」，
        # 因此带上原判据名而不是识别源（detector 上没有 source 属性）。
        return Hit(box=self._box, source=self._name)
