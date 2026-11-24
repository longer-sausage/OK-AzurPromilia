"""抽卡模型测试：概率表、期望、预算内概率、最坏情况、自定义概率表。

数值基准：内置参数下 71 抽 2.8%、81 抽 28.8%、90 抽 100%；
目标概率 50% 时 180 抽内完成 82.85%（与社区公开值一致，作为回归基准）。
"""

import itertools
import unittest

from src.core.gacha import GachaParams, PityBernoulliModel, parse_rate_table


class TestRateTable(unittest.TestCase):
    def setUp(self):
        self.model = PityBernoulliModel()

    def test_length_and_bounds(self):
        rates = self.model.rates
        self.assertEqual(len(rates), 90)
        self.assertTrue(all(0.0 <= rate <= 1.0 for rate in rates))

    def test_documented_rates(self):
        self.assertAlmostEqual(self.model.rate_at(1), 0.008)
        self.assertAlmostEqual(self.model.rate_at(70), 0.008)
        self.assertAlmostEqual(self.model.rate_at(71), 0.028)
        self.assertAlmostEqual(self.model.rate_at(80), 0.208)
        self.assertAlmostEqual(self.model.rate_at(81), 0.288)
        self.assertAlmostEqual(self.model.rate_at(90), 1.0)

    def test_rates_are_monotonic(self):
        rates = self.model.rates
        for previous, current in itertools.pairwise(rates):
            self.assertLessEqual(previous, current)

    def test_hard_pity_is_certain(self):
        self.assertEqual(self.model.rate_at(90), 1.0)
        self.assertEqual(self.model.rate_at(95), 1.0)

    def test_invalid_pull_index(self):
        with self.assertRaises(ValueError):
            self.model.rate_at(0)


class TestFirstHit(unittest.TestCase):
    def test_distribution_sums_to_one(self):
        distribution = PityBernoulliModel().first_hit_distribution()
        self.assertAlmostEqual(sum(distribution.values()), 1.0, places=6)
        self.assertEqual(max(distribution), 90)

    def test_expected_between_base_and_hard_pity(self):
        expected = PityBernoulliModel().expected_first_hit()
        self.assertGreater(expected, 50)
        self.assertLess(expected, 65)

    def test_pity_shrinks_expected(self):
        model = PityBernoulliModel()
        self.assertLess(model.expected_first_hit(pity=80), 12)
        self.assertAlmostEqual(model.expected_first_hit(pity=89), 1.0, places=6)

    def test_pity_beyond_hard_pity(self):
        self.assertEqual(PityBernoulliModel().first_hit_distribution(pity=90), {1: 1.0})

    def test_negative_pity_rejected(self):
        with self.assertRaises(ValueError):
            PityBernoulliModel().first_hit_distribution(pity=-1)


class TestProbabilityAndExpectation(unittest.TestCase):
    def test_certain_within_hard_pity(self):
        model = PityBernoulliModel()
        self.assertAlmostEqual(model.probability_within(90, copies=1), 1.0, places=6)
        self.assertAlmostEqual(model.probability_within(200, copies=1), 1.0, places=6)

    def test_probability_is_monotonic_in_budget(self):
        model = PityBernoulliModel(target_rate=0.5)
        values = [model.probability_within(budget, copies=1) for budget in (10, 50, 90, 180, 360)]
        for previous, current in itertools.pairwise(values):
            self.assertLessEqual(previous, current)

    def test_half_rate_matches_community_figure(self):
        model = PityBernoulliModel(target_rate=0.5)
        self.assertAlmostEqual(model.probability_within(180, copies=1), 0.8285, delta=0.01)

    def test_guaranteed_is_better_than_half(self):
        guaranteed = PityBernoulliModel(target_rate=1.0)
        half = PityBernoulliModel(target_rate=0.5)
        self.assertGreater(
            guaranteed.probability_within(90, copies=1), half.probability_within(90, copies=1)
        )

    def test_two_copies_take_longer(self):
        model = PityBernoulliModel()
        one = model.expected_pulls(copies=1)
        two = model.expected_pulls(copies=2)
        self.assertGreater(two, one)
        self.assertLess(two, one * 2 + 30)

    def test_finish_distribution_mass(self):
        distribution, unfinished = PityBernoulliModel().finish_distribution(copies=1, max_pulls=90)
        self.assertAlmostEqual(sum(distribution.values()) + unfinished, 1.0, places=6)
        self.assertLess(unfinished, 1e-6)


class TestWorstCase(unittest.TestCase):
    def test_guaranteed_has_finite_worst_case(self):
        model = PityBernoulliModel(target_rate=1.0)
        self.assertEqual(model.worst_case(1), 90)
        self.assertEqual(model.worst_case(3), 270)

    def test_half_rate_has_no_worst_case(self):
        self.assertIsNone(PityBernoulliModel(target_rate=0.5).worst_case(1))


class TestPlan(unittest.TestCase):
    def test_plan_fields_and_summary(self):
        result = PityBernoulliModel().plan(copies=1, pity=0, budget=180)
        self.assertAlmostEqual(result.expected_pulls, 58.26, delta=1.0)
        self.assertAlmostEqual(result.probability, 1.0, places=6)
        self.assertEqual(result.worst_case, 90)
        self.assertFalse(result.truncated)
        summary = result.summary()
        self.assertIn("期望", summary)
        self.assertIn("180 抽内完成", summary)

    def test_plan_with_pity(self):
        model = PityBernoulliModel()
        fresh = model.plan(copies=1, pity=0, budget=90)
        ahead = model.plan(copies=1, pity=85, budget=90)
        self.assertLess(ahead.expected_pulls, fresh.expected_pulls)

    def test_plan_truncation_flag(self):
        result = PityBernoulliModel(target_rate=0.5).plan(copies=1, pity=0, budget=10)
        self.assertTrue(result.truncated)
        self.assertIn("不确定", result.summary())

    def test_plan_summary_for_half_rate_mentions_uncertainty(self):
        summary = PityBernoulliModel(target_rate=0.5).plan(copies=1, budget=180).summary()
        self.assertIn("最坏情况不确定", summary)


class TestCustomParams(unittest.TestCase):
    def test_custom_rates_are_used(self):
        rates = [0.5] * 89 + [1.0]
        model = PityBernoulliModel(GachaParams(custom_rates=rates))
        self.assertAlmostEqual(model.rate_at(1), 0.5)
        self.assertAlmostEqual(model.expected_first_hit(), sum(
            (0.5 ** (n - 1)) * 0.5 * n for n in range(1, 90)
        ) + 90 * 0.5 ** 89, places=4)

    def test_custom_rates_length_mismatch(self):
        with self.assertRaises(ValueError):
            GachaParams(custom_rates=[0.5, 1.0])

    def test_custom_rates_must_end_at_one(self):
        with self.assertRaises(ValueError):
            GachaParams(custom_rates=[0.5] * 90)

    def test_bad_soft_pity_order(self):
        with self.assertRaises(ValueError):
            GachaParams(soft_start_1=85, soft_start_2=80)

    def test_bad_target_rate(self):
        for value in (0, -0.1, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PityBernoulliModel(target_rate=value)

    def test_parse_rate_table(self):
        parsed = parse_rate_table("2.8%, 28.8%, 100%")
        self.assertEqual(len(parsed), 3)
        for actual, expected in zip(parsed, (0.028, 0.288, 1.0), strict=True):
            self.assertAlmostEqual(actual, expected, places=9)
        parsed = parse_rate_table("0.008\n0.2\n1")
        for actual, expected in zip(parsed, (0.008, 0.2, 1.0), strict=True):
            self.assertAlmostEqual(actual, expected, places=9)
        with self.assertRaises(ValueError):
            parse_rate_table("abc")
        with self.assertRaises(ValueError):
            parse_rate_table("   ")

    def test_rate_table_rejects_out_of_range(self):
        """概率表是用户从 wiki 抄的，抄错一个数就会得到荒谬模型。"""
        for text in ("150%", "-5%"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_rate_table(text)

    def test_rate_table_rejects_non_monotonic(self):
        with self.assertRaises(ValueError) as ctx:
            parse_rate_table("50%, 20%, 100%")
        self.assertIn("单调不减", str(ctx.exception))

    def test_rate_table_requires_hard_pity(self):
        with self.assertRaises(ValueError) as ctx:
            parse_rate_table("50%, 60%")
        self.assertIn("100%", str(ctx.exception))

    def test_rate_table_single_hundred_percent(self):
        self.assertEqual(parse_rate_table("100%"), [1.0])


if __name__ == "__main__":
    unittest.main()
