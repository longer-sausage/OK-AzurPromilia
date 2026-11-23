"""识别层：把四类识别源归一成统一判据，供 Action 生命周期编排使用。

用法与设计见 ``docs/dev/action_lifecycle.md``：
识别源与生命周期是两个正交维度，本包只负责「识别源 → 统一判据」的归一化，
不参与「何时执行动作」的编排。

四类识别源与对应适配器：

| 识别源 | 适配器 | 包装的底层原语 |
|---|---|---|
| 模板匹配 | ``TemplateDetector`` | ``find_feature`` / ``find_one`` |
| YOLO | ``YoloDetector`` | ``yolo_detect`` |
| OCR | ``OcrDetector`` | ``ocr`` |
| 按钮检测 | ``ButtonDetectorAdapter`` | ``find_button`` |

兜底与组合：``PredicateDetector``、``InvertedDetector``、``MultiBoxDetector``、
``FirstHitDetector``、``BlindPointDetector``。

Example:
    >>> from src.core.detector import TemplateDetector
    >>> self.wait_action_result(
    ...     condition=TemplateDetector(FeatureList.treasure_icon),
    ...     action=lambda hit: self.click(hit.box),
    ...     expect=TemplateDetector(FeatureList.unlock_ui),
    ...     time_out=5,
    ... )
"""

from src.core.detector.base import Detector, InvertedDetector, PredicateDetector
from src.core.detector.button_adapter import ButtonDetectorAdapter
from src.core.detector.combinators import (
    BlindPointDetector,
    FirstHitDetector,
    MultiBoxDetector,
)
from src.core.detector.hit import (
    SOURCE_BUTTON,
    SOURCE_OCR,
    SOURCE_PREDICATE,
    SOURCE_TEMPLATE,
    SOURCE_YOLO,
    Hit,
)
from src.core.detector.ocr_detector import OcrDetector
from src.core.detector.template_detector import TemplateDetector
from src.core.detector.yolo_detector import YoloDetector

__all__ = [
    "Hit",
    "Detector",
    "PredicateDetector",
    "InvertedDetector",
    "TemplateDetector",
    "YoloDetector",
    "OcrDetector",
    "ButtonDetectorAdapter",
    "MultiBoxDetector",
    "FirstHitDetector",
    "BlindPointDetector",
    "SOURCE_TEMPLATE",
    "SOURCE_YOLO",
    "SOURCE_OCR",
    "SOURCE_BUTTON",
    "SOURCE_PREDICATE",
]
