"""一次识别命中的统一表示。

四类识别源（模板匹配 / YOLO / OCR / 专用检测器）的返回值形状各不相同，
``Hit`` 把它们归一成同一个结构，使 Action 生命周期的编排层只依赖一种形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ok import Box

# 识别源标识（用于日志与调试框分组）
SOURCE_TEMPLATE = "template"
SOURCE_YOLO = "yolo"
SOURCE_OCR = "ocr"
SOURCE_BUTTON = "button"
SOURCE_PREDICATE = "predicate"


@dataclass
class Hit:
    """一次识别命中的统一表示。

    Attributes:
        box: 可直接 ``click()`` 的目标框。唯一必需字段。
        confidence: 0.0~1.0 的置信度；未知时取 1.0。
        source: 识别源标识（见本模块的 ``SOURCE_*`` 常量）。
        text: OCR 命中文本；非 OCR 源为空串。
        raw: 原始结果对象（OCR 的 Box 列表、各 Detection 实例等）。
            需要读取识别器特有字段（如 ``ButtonDetection.metrics``、
            ``GlowDetection.radius``）时从这里取，避免信息在归一化时丢失。

    Example:
        >>> hit = Hit(box=Box(10, 20, 30, 40), confidence=0.9, source=SOURCE_YOLO)
        >>> if hit:            # __bool__ 恒为 True
        ...     task.click(hit.box)
    """

    box: Box
    confidence: float = 1.0
    source: str = ""
    text: str = ""
    raw: object = None
    metrics: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        """命中对象恒为真；``detect()`` 未命中时返回 ``None`` 而非空 Hit。"""
        return True

    def __repr__(self) -> str:
        return (
            f"Hit(source={self.source!r}, box={self.box}, "
            f"confidence={self.confidence:.2f}, text={self.text!r})"
        )

    @classmethod
    def from_box(cls, box, source: str = "", confidence: float | None = None) -> "Hit":
        """从 ``Box`` 构造 Hit，置信度缺省时取 Box 自身的 confidence。

        Args:
            box: 目标框。
            source: 识别源标识。
            confidence: 显式置信度；None 时读 ``box.confidence``。

        Returns:
            Hit: 归一化后的命中。
        """
        if confidence is None:
            confidence = float(getattr(box, "confidence", 1.0) or 1.0)
        return cls(box=box, confidence=confidence, source=source, raw=box)
