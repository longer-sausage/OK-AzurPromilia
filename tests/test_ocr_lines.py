"""OCR 文本行测试：按 y 聚类成行、行内按 x 排序、置信度过滤。

`ocr_lines` 是结算记账的底座：框架返回的文本框是散装的，
「尤尼」和「×1,500」是两个框，不聚类就会被拆成两行、解析不到数量。
"""

import unittest

from src.core.base_game_task import BaseGameTask


class FakeTextBox:
    def __init__(self, name, x, y, width=80, height=20, confidence=0.95):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.confidence = confidence


class OcrStub:
    ocr_lines = BaseGameTask.ocr_lines

    def __init__(self, boxes):
        self._boxes = boxes
        self._log_failure_store = lambda message: None
        self.ocr_calls = []

    def ocr(self, *args, **kwargs):
        self.ocr_calls.append((args, kwargs))
        return list(self._boxes)


class TestOcrLines(unittest.TestCase):
    def test_same_row_is_merged_in_x_order(self):
        stub = OcrStub([
            FakeTextBox("×1,500", 400, 302),
            FakeTextBox("尤尼", 300, 300),
        ])
        self.assertEqual(stub.ocr_lines(), ["尤尼  ×1,500"])

    def test_different_rows_are_separate(self):
        stub = OcrStub([
            FakeTextBox("尤尼", 300, 300),
            FakeTextBox("波力果", 300, 400),
        ])
        self.assertEqual(stub.ocr_lines(), ["尤尼", "波力果"])

    def test_rows_are_sorted_top_to_bottom(self):
        stub = OcrStub([
            FakeTextBox("第二行", 300, 500),
            FakeTextBox("第一行", 300, 300),
        ])
        self.assertEqual(stub.ocr_lines(), ["第一行", "第二行"])

    def test_empty_text_is_dropped(self):
        stub = OcrStub([FakeTextBox("  ", 300, 300), FakeTextBox("有字", 300, 400)])
        self.assertEqual(stub.ocr_lines(), ["有字"])

    def test_confidence_filter(self):
        stub = OcrStub([
            FakeTextBox("低置信", 300, 300, confidence=0.2),
            FakeTextBox("高置信", 300, 400, confidence=0.9),
        ])
        self.assertEqual(stub.ocr_lines(min_confidence=0.5), ["高置信"])

    def test_empty_result(self):
        self.assertEqual(OcrStub([]).ocr_lines(), [])

    def test_ocr_exception_is_swallowed(self):
        class Broken(OcrStub):
            def ocr(self, *args, **kwargs):
                raise RuntimeError("ocr down")

        self.assertEqual(Broken([]).ocr_lines(), [])

    def test_box_argument_is_forwarded(self):
        stub = OcrStub([FakeTextBox("x", 1, 1)])
        stub.ocr_lines(box="ROI")
        self.assertEqual(stub.ocr_calls[0][1], {"box": "ROI"})

    def test_settlement_style_lines_parse(self):
        """聚类后的文本要能被结算解析器吃下去。"""
        from src.core.ledger import parse_settlement_text

        stub = OcrStub([
            FakeTextBox("尤尼", 300, 300),
            FakeTextBox("×1,500", 430, 302),
            FakeTextBox("波力果", 300, 400),
            FakeTextBox("×12", 430, 401),
        ])
        text = "\n".join(stub.ocr_lines())
        entries = parse_settlement_text(text)
        self.assertEqual([(entry.item, entry.amount) for entry in entries], [("尤尼", 1500), ("波力果", 12)])


if __name__ == "__main__":
    unittest.main()
