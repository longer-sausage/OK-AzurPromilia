"""模板自检测试：纹理判据、SHA-256 重复检测、COCO 批量自检。"""

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.core.template_audit import (
    MIN_SIDE,
    VERDICT_FLAT,
    VERDICT_OK,
    VERDICT_WEAK,
    TemplateAuditError,
    analyze_region,
    audit_coco,
    audit_directory,
    rebuild_duplicates,
    sha256_of_bytes,
    sha256_of_region,
)


def flat_region(size=24, color=40):
    return np.full((size, size, 3), color, dtype=np.uint8)


def textured_region(size=48):
    """棋盘 + 文字状线条 + 抗锯齿：对比度、颜色数、边缘都够。"""
    region = np.full((size, size, 3), 200, dtype=np.uint8)
    region[::4, :] = 20
    region[:, ::4] = 20
    cv2.rectangle(region, (6, 6), (size - 8, size - 8), (60, 120, 220), 2)
    cv2.line(region, (8, size // 2), (size - 10, size // 2), (10, 10, 10), 2)
    # 轻微模糊制造抗锯齿边缘，让颜色数接近真实图标
    return cv2.GaussianBlur(region, (3, 3), 0)


def weak_region(size=48):
    """只有一点点对比度：不该算可用，但也不该算纯色块。"""
    region = np.full((size, size, 3), 128, dtype=np.uint8)
    region[10:14, 10:40] = 150
    return region


class TestAnalyzeRegion(unittest.TestCase):
    def test_flat_is_rejected(self):
        quality = analyze_region(flat_region(), "纯色")
        self.assertEqual(quality.verdict, VERDICT_FLAT)
        self.assertFalse(quality.ok)
        self.assertEqual(len(quality.reasons), 3)
        self.assertIn("对比度", quality.reasons[0])

    def test_textured_is_ok(self):
        quality = analyze_region(textured_region(), "图标")
        self.assertEqual(quality.verdict, VERDICT_OK, quality.reasons)
        self.assertGreater(quality.stddev, 6)
        self.assertGreaterEqual(quality.unique_colors, 8)
        self.assertTrue(quality.sha256)

    def test_weak_region(self):
        quality = analyze_region(weak_region(), "弱纹理")
        self.assertIn(quality.verdict, (VERDICT_WEAK, VERDICT_FLAT))
        self.assertFalse(quality.ok)

    def test_too_small(self):
        quality = analyze_region(np.full((MIN_SIDE - 2, MIN_SIDE - 2, 3), 0, dtype=np.uint8))
        self.assertEqual(quality.verdict, VERDICT_FLAT)
        self.assertTrue(any("尺寸太小" in reason for reason in quality.reasons))

    def test_empty_region(self):
        self.assertEqual(analyze_region(None).verdict, VERDICT_FLAT)
        self.assertEqual(analyze_region(np.empty((0, 0, 3), dtype=np.uint8)).verdict, VERDICT_FLAT)

    def test_grayscale_supported(self):
        gray = np.full((24, 24), 40, dtype=np.uint8)
        self.assertEqual(analyze_region(gray).verdict, VERDICT_FLAT)

    def test_sha256_is_stable(self):
        region = textured_region()
        self.assertEqual(sha256_of_region(region), sha256_of_region(region.copy()))
        self.assertNotEqual(sha256_of_region(region), sha256_of_region(flat_region()))
        self.assertEqual(len(sha256_of_region(region)), 64)

    def test_score_ordering(self):
        flat = analyze_region(flat_region())
        textured = analyze_region(textured_region())
        self.assertLess(flat.score(), textured.score())

    def test_label_and_dict(self):
        quality = analyze_region(textured_region(), "图标")
        self.assertIn("图标", quality.label())
        payload = quality.to_dict()
        self.assertEqual(payload["name"], "图标")
        self.assertIn("score", payload)

    def test_sha256_of_bytes(self):
        self.assertEqual(len(sha256_of_bytes(b"abc")), 64)
        self.assertEqual(sha256_of_bytes(b"abc"), sha256_of_bytes(b"abc"))


class TestAuditCoco(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.images = self.root / "images"
        self.images.mkdir()

        atlas = np.full((80, 120, 3), 255, dtype=np.uint8)
        atlas[0:40, 0:40] = textured_region(40)
        atlas[0:40, 50:90] = textured_region(40)  # 与上一块完全相同 → 重复
        atlas[45:75, 0:30] = 40  # 纯色块
        cv2.imwrite(str(self.images / "1.png"), atlas)

        self.coco = {
            "images": [{"id": 1, "file_name": "images/1.png", "width": 120, "height": 80}],
            "categories": [
                {"id": 1, "name": "good"},
                {"id": 2, "name": "duplicate"},
                {"id": 3, "name": "flat"},
            ],
            "annotations": [
                {"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 40, 40]},
                {"id": 2, "image_id": 1, "category_id": 2, "bbox": [50, 0, 40, 40]},
                {"id": 3, "image_id": 1, "category_id": 3, "bbox": [0, 45, 30, 30]},
            ],
        }
        self.coco_path = self.root / "coco_annotations.json"
        self.coco_path.write_text(json.dumps(self.coco), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_audit(self):
        report = audit_coco(self.coco_path)
        self.assertEqual(report.count, 3)
        by_name = {item.name: item for item in report.templates}
        self.assertEqual(by_name["good"].verdict, VERDICT_OK)
        self.assertEqual(by_name["flat"].verdict, VERDICT_FLAT)
        self.assertEqual(by_name["duplicate"].verdict, VERDICT_OK)

    def test_duplicates_detected(self):
        report = audit_coco(self.coco_path)
        self.assertEqual(len(report.duplicates), 1)
        names = next(iter(report.duplicates.values()))
        self.assertEqual(sorted(names), ["duplicate", "good"])

    def test_summary_mentions_flat_and_duplicates(self):
        summary = audit_coco(self.coco_path).summary()
        self.assertIn("纯色块", summary)
        self.assertIn("重复", summary)

    def test_worst_sorted(self):
        report = audit_coco(self.coco_path)
        self.assertEqual(report.worst(1)[0].name, "flat")

    def test_missing_coco(self):
        with self.assertRaises(TemplateAuditError):
            audit_coco(self.root / "nope.json")

    def test_bad_json(self):
        path = self.root / "bad.json"
        path.write_text("{ oops", encoding="utf-8")
        with self.assertRaises(TemplateAuditError):
            audit_coco(path)

    def test_missing_atlas(self):
        self.coco["images"][0]["file_name"] = "images/none.png"
        self.coco_path.write_text(json.dumps(self.coco), encoding="utf-8")
        with self.assertRaises(TemplateAuditError):
            audit_coco(self.coco_path)

    def test_audit_directory_finds_coco(self):
        report = audit_directory(self.root)
        self.assertEqual(report.count, 3)

    def test_audit_directory_missing(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(TemplateAuditError):
            audit_directory(tmp)

    def test_crop_out_of_bounds_is_safe(self):
        self.coco["annotations"][0]["bbox"] = [1000, 1000, 10, 10]
        self.coco_path.write_text(json.dumps(self.coco), encoding="utf-8")
        report = audit_coco(self.coco_path)
        self.assertEqual(report.count, 3)  # 越界裁成空块，判 flat 但不崩

    def test_rebuild_duplicates_groups(self):
        report = audit_coco(self.coco_path)
        report.duplicates = {}
        rebuild_duplicates(report)
        self.assertEqual(len(report.duplicates), 1)


if __name__ == "__main__":
    unittest.main()
