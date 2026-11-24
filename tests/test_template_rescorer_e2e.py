"""全模板复测的**端到端**测试：用真实 FeatureSet + 真实模板跑一遍。

单元测试里 matcher 是假的，只能证明「调用参数拼对了」；这里用真模板证明
「把模板贴进画面，它就能拿到高分并排第一」——如果框架的
``find_one_feature`` 签名或阈值语义变了，这个测试会直接红，而不是等用户
在「失败帧复测」页看到一排空表。
"""

import json
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.core.template_rescorer import TemplateRescorer

ASSETS = Path(__file__).resolve().parents[1] / "assets"
COCO = ASSETS / "coco_annotations.json"
ATLAS = ASSETS / "images" / "1.png"


def _load_atlas() -> np.ndarray | None:
    if not ATLAS.is_file():
        return None
    data = np.fromfile(str(ATLAS), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


@unittest.skipUnless(COCO.is_file() and ATLAS.is_file(), "缺少模板素材")
class TestRescoreEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ok.feature.FeatureSet import FeatureSet

        cls.feature_set = FeatureSet(
            debug=False,
            coco_json=str(COCO),
            default_horizontal_variance=0.002,
            default_vertical_variance=0.002,
            default_threshold=0.8,
        )
        cls.coco = json.loads(COCO.read_text(encoding="utf-8"))
        cls.atlas = _load_atlas()

    def _paste_target(self):
        """把第一个标注的模板贴到 1920×1080 的暗背景上。"""
        annotation = self.coco["annotations"][0]
        category = next(
            item["name"] for item in self.coco["categories"] if item["id"] == annotation["category_id"]
        )
        x, y, width, height = (int(value) for value in annotation["bbox"])
        patch = self.atlas[y:y + height, x:x + width].copy()
        frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)
        frame[400:400 + height, 900:900 + width] = patch
        others = [item["name"] for item in self.coco["categories"] if item["name"] != category][:2]
        return category, others, frame

    def test_pasted_template_scores_highest(self):
        category, others, frame = self._paste_target()
        rescorer = TemplateRescorer.from_feature_set(
            self.feature_set, width=1920, names=[category, *others], threshold=0.8
        )
        result = rescorer.rescore(frame)

        self.assertEqual(result.missing, [])
        self.assertEqual(len(result.scores), 1 + len(others))
        best = result.best()
        self.assertIsNotNone(best)
        self.assertEqual(best.name, category)
        self.assertGreaterEqual(best.score, 0.95)
        self.assertTrue(best.hit)
        self.assertEqual(best.verdict(), "命中")

        # 没贴进去的模板应该明显更低
        for score in result.scores[1:]:
            self.assertLess(score.score, 0.9, f"{score.name} 不该有这么高的分")
        self.assertEqual(result.hits, [best])

    def test_empty_frame_matches_nothing_high(self):
        """全暗画面里不应有任何模板命中（阈值 0.8）。"""
        _, others, _ = self._paste_target()
        frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)
        rescorer = TemplateRescorer.from_feature_set(
            self.feature_set, width=1920, names=others, threshold=0.8
        )
        result = rescorer.rescore(frame)
        self.assertEqual(result.hits, [])

    def test_summary_mentions_counts(self):
        category, others, frame = self._paste_target()
        rescorer = TemplateRescorer.from_feature_set(
            self.feature_set, width=1920, names=[category, *others], threshold=0.8
        )
        summary = rescorer.rescore(frame).summary()
        self.assertIn("命中", summary)
        self.assertIn("耗时", summary)


if __name__ == "__main__":
    unittest.main()
