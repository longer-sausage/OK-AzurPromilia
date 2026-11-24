"""全模板打分器测试：排序、判定、去重、缺模板容错、搜索框和阈值约定。

真正的模板匹配由 FakeMatcher 代替，验证的是「怎么把所有模板问一遍」这层协议：
必须传全屏 box、必须把 threshold 压到 -1、必须按分辨率解析名字。
"""

import unittest

import numpy as np
from ok import Box

from src.core.template_rescorer import (
    DEFAULT_UI_THRESHOLD,
    RAW_SCORE_THRESHOLD,
    TemplateRescorer,
    resolution_resolver,
)
from src.data.feature_list import FeatureList


class FakeMatcher:
    """按映射返回固定得分；``find_one_feature`` 的调用参数会被记下来。"""

    def __init__(self, scores: dict, name: str = "fake"):
        self.scores = scores
        self.name = name
        self.calls: list[tuple[str, dict]] = []

    def find_one_feature(self, frame, category_name, **kwargs):
        self.calls.append((category_name, kwargs))
        value = self.scores.get(category_name)
        if value is None:
            return []
        if isinstance(value, Exception):
            raise value
        return [Box(10, 20, 30, 40, float(value), category_name)]


FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)


class TestRescoreOrdering(unittest.TestCase):
    def test_sorted_by_score_desc(self):
        matcher = FakeMatcher({"a": 0.31, "b": 0.92, "c": 0.77})
        result = TemplateRescorer(matcher, names=["a", "b", "c"]).rescore(FRAME)

        self.assertEqual([item.name for item in result.scores], ["b", "c", "a"])
        self.assertAlmostEqual(result.best().score, 0.92)
        self.assertEqual(result.frame_size, (1920, 1080))

    def test_hits_and_verdicts(self):
        matcher = FakeMatcher({"a": 0.95, "b": 0.75, "c": 0.6, "d": 0.1})
        result = TemplateRescorer(matcher, names=["a", "b", "c", "d"], threshold=0.8).rescore(FRAME)

        by_name = {item.name: item for item in result.scores}
        self.assertTrue(by_name["a"].hit)
        self.assertEqual(by_name["a"].verdict(), "命中")
        self.assertEqual(by_name["b"].verdict(), "差一点")  # 0.05 差距
        self.assertEqual(by_name["c"].verdict(), "偏弱")
        self.assertEqual(by_name["d"].verdict(), "不对")

        self.assertEqual([item.name for item in result.hits], ["a"])
        self.assertEqual([item.name for item in result.near_misses], ["b"])

    def test_gap_and_center(self):
        matcher = FakeMatcher({"a": 0.5})
        score = TemplateRescorer(matcher, names=["a"], threshold=0.8).rescore(FRAME).best()

        self.assertAlmostEqual(score.gap, 0.3, places=6)
        self.assertEqual(score.center(), (25, 40))

    def test_default_threshold_used_when_omitted(self):
        matcher = FakeMatcher({"a": 0.9})
        result = TemplateRescorer(matcher, names=["a"]).rescore(FRAME)
        self.assertAlmostEqual(result.threshold, DEFAULT_UI_THRESHOLD)

    def test_result_summary_mentions_counts(self):
        matcher = FakeMatcher({"a": 0.9, "b": 0.2})
        summary = TemplateRescorer(matcher, names=["a", "b"], threshold=0.8).rescore(FRAME).summary()
        self.assertIn("共 2 个模板", summary)
        self.assertIn("命中 1 个", summary)
        self.assertIn("a", summary)


class TestRescoreContract(unittest.TestCase):
    def test_full_frame_box_and_raw_threshold_are_passed(self):
        matcher = FakeMatcher({"a": 0.4})
        TemplateRescorer(matcher, names=["a"]).rescore(FRAME)

        _name, kwargs = matcher.calls[0]
        box = kwargs["box"]
        self.assertEqual((box.x, box.y), (0, 0))
        self.assertEqual((box.width, box.height), (1920, 1080))
        self.assertAlmostEqual(kwargs["threshold"], RAW_SCORE_THRESHOLD)
        self.assertEqual(kwargs["limit"], 1)

    def test_resolver_maps_names_and_dedupes(self):
        matcher = FakeMatcher({"ui_2k": 0.88})
        resolver = lambda name: "ui_2k" if name in ("ui", "ui_2k") else name  # noqa: E731
        result = TemplateRescorer(matcher, names=["ui", "ui_2k"], resolver=resolver).rescore(FRAME)

        self.assertEqual(len(result.scores), 1)
        self.assertEqual(result.scores[0].name, "ui")
        self.assertEqual(result.scores[0].resolved_name, "ui_2k")
        self.assertEqual(len(matcher.calls), 1)

    def test_missing_template_is_reported_not_raised(self):
        matcher = FakeMatcher({"a": 0.5})
        result = TemplateRescorer(matcher, names=["a", "ghost"]).rescore(FRAME)

        self.assertEqual([item.name for item in result.scores], ["a"])
        self.assertEqual(result.missing, ["ghost"])

    def test_exception_in_matcher_is_reported(self):
        matcher = FakeMatcher({"boom": ValueError("template too large")})
        result = TemplateRescorer(matcher, names=["boom"]).rescore(FRAME)

        self.assertEqual(result.scores, [])
        self.assertEqual(result.missing, ["boom"])

    def test_empty_frame_returns_empty_result(self):
        matcher = FakeMatcher({"a": 0.9})
        result = TemplateRescorer(matcher, names=["a"]).rescore(None)
        self.assertEqual(result.scores, [])
        self.assertEqual(matcher.calls, [])

    def test_progress_callback_counts(self):
        matcher = FakeMatcher({"a": 0.1, "b": 0.2, "c": 0.3})
        seen = []
        TemplateRescorer(matcher, names=["a", "b", "c"]).rescore(
            FRAME, progress=lambda done, total, name: seen.append((done, total, name))
        )
        self.assertEqual(seen, [(1, 3, "a"), (2, 3, "b"), (3, 3, "c")])

    def test_elapsed_is_measured(self):
        matcher = FakeMatcher({"a": 0.1})
        result = TemplateRescorer(matcher, names=["a"]).rescore(FRAME)
        self.assertGreaterEqual(result.elapsed, 0.0)


class TestNameSources(unittest.TestCase):
    def test_feature_list_is_default_names(self):
        matcher = FakeMatcher({})
        names = TemplateRescorer(matcher).names()
        self.assertEqual(len(names), len(list(FeatureList)))
        self.assertIn("account_switch", names)

    def test_explicit_names_win(self):
        matcher = FakeMatcher({})
        self.assertEqual(TemplateRescorer(matcher, names=["x"]).names(), ["x"])

    def test_resolution_resolver_matches_runtime_mixin(self):
        from src.core.base_mixin.runtime_mixin import RuntimeMixin

        class _Holder:
            width = 0

        for width in (1920, 2560, 3840):
            holder = _Holder()
            holder.width = width
            mine = resolution_resolver(width)
            for member in FeatureList:
                with self.subTest(width=width, name=member.value):
                    self.assertEqual(mine(member.value), RuntimeMixin.get_feature_by_resolution(holder, member.value))

    def test_resolution_resolver_falls_back_to_base_name(self):
        self.assertEqual(resolution_resolver(1920)("not_a_real_template"), "not_a_real_template")


if __name__ == "__main__":
    unittest.main()
