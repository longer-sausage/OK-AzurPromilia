import json
import unittest
from pathlib import Path


class TestTemplateSources(unittest.TestCase):
    def test_annotated_screenshots_are_present(self):
        templates = Path(__file__).resolve().parents[1] / "ok_templates"
        coco = json.loads((templates / "coco_annotations.json").read_text(encoding="utf-8"))
        self.assertTrue(coco["images"])
        for image in coco["images"]:
            with self.subTest(file_name=image["file_name"]):
                self.assertTrue((templates / image["file_name"]).is_file())
