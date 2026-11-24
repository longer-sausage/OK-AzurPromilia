"""记账与运行报表测试：结算文本解析、JSONL 容错、汇总、CSV 导出、用时估算。"""

import tempfile
import unittest
from pathlib import Path

from src.core.ledger import (
    Ledger,
    LedgerEntry,
    RunLog,
    SettlementFormatError,
    parse_settlement_text,
)


class TestSettlementParsing(unittest.TestCase):
    def test_name_first(self):
        entries = parse_settlement_text("获得的奖励：尤尼 ×1,500")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].item, "尤尼")
        self.assertAlmostEqual(entries[0].amount, 1500)
        self.assertEqual(entries[0].kind, "income")

    def test_count_first(self):
        entries = parse_settlement_text("12 × 波力果")
        self.assertEqual([(e.item, e.amount) for e in entries], [("波力果", 12)])

    def test_star_separator(self):
        entries = parse_settlement_text("普通的灵粹*24")
        self.assertEqual([(e.item, e.amount) for e in entries], [("普通的灵粹", 24)])

    def test_expense_line(self):
        entries = parse_settlement_text("消耗：星脉结晶 ×3")
        self.assertEqual(entries[0].kind, "expense")

    def test_mixed_lines(self):
        text = "获得的奖励：尤尼 ×1,500\n12 × 波力果\n消耗：星脉结晶 ×3"
        entries = parse_settlement_text(text)
        self.assertEqual(len(entries), 3)
        self.assertEqual([entry.kind for entry in entries], ["income", "income", "expense"])

    def test_dedupes_same_pattern_hit(self):
        entries = parse_settlement_text("12 × 波力果")
        self.assertEqual(len(entries), 1)

    def test_blank_lines_ignored(self):
        entries = parse_settlement_text("\n\n尤尼 × 100\n\n")
        self.assertEqual(len(entries), 1)

    def test_source_preserved(self):
        entries = parse_settlement_text("尤尼 ×1", source="结算界面")
        self.assertEqual(entries[0].source, "结算界面")

    def test_no_match_raises(self):
        with self.assertRaises(SettlementFormatError):
            parse_settlement_text("今天天气不错")

    def test_zero_amount_skipped(self):
        with self.assertRaises(SettlementFormatError):
            parse_settlement_text("尤尼 ×0")

    def test_numeric_name_suffix_does_not_create_phantom_entry(self):
        """「材料2 ×3」里的 2 是名字的一部分，不能又被「数量在前」匹配成一条。"""
        entries = parse_settlement_text("材料2 ×3")
        self.assertEqual([(e.item, e.amount) for e in entries], [("材料2", 3)])

    def test_numbered_material_list(self):
        text = "\n".join(f"材料{i} ×{i + 1}" for i in range(8))
        entries = parse_settlement_text(text)
        self.assertEqual(len(entries), 8)
        self.assertEqual(entries[0].item, "材料0")
        self.assertEqual(entries[7].amount, 8)

    def test_pure_number_name_rejected(self):
        with self.assertRaises(SettlementFormatError):
            parse_settlement_text("12 × 34")


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestLedger(StoreTestBase):
    def test_record_and_totals(self):
        ledger = Ledger(self.root / "ledger.jsonl")
        written = ledger.record_text("获得的奖励：尤尼 ×1,500\n消耗：星脉结晶 ×3", ts=1000.0)
        self.assertEqual(written, 2)

        totals = ledger.totals()
        self.assertAlmostEqual(totals["income"], 1500)
        self.assertAlmostEqual(totals["expense"], 3)
        self.assertAlmostEqual(totals["net"], 1497)

    def test_summary_by_item(self):
        ledger = Ledger(self.root / "ledger.jsonl")
        ledger.record_text("尤尼 ×100\n尤尼 ×50\n消耗：尤尼 ×30", ts=1000.0)
        summary = ledger.summary()
        self.assertAlmostEqual(summary["尤尼"]["income"], 150)
        self.assertAlmostEqual(summary["尤尼"]["expense"], 30)
        self.assertAlmostEqual(summary["尤尼"]["net"], 120)

    def test_since_filter(self):
        ledger = Ledger(self.root / "ledger.jsonl")
        ledger.record([LedgerEntry("尤尼", 10)], ts=1000.0)
        ledger.record([LedgerEntry("尤尼", 20)], ts=2000.0)
        self.assertAlmostEqual(ledger.totals(since=1500.0)["income"], 20)

    def test_today_summary_exists(self):
        ledger = Ledger(self.root / "ledger.jsonl")
        ledger.record_text("尤尼 ×5")
        self.assertIn("尤尼", ledger.today_summary())

    def test_bad_rows_are_skipped(self):
        path = self.root / "ledger.jsonl"
        ledger = Ledger(path)
        ledger.record([LedgerEntry("尤尼", 10)], ts=1000.0)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{ broken json\n")
            handle.write('{"item": "尤尼", "amount": "not-a-number"}\n')
        self.assertEqual(len(ledger.entries()), 1)

    def test_missing_file_is_empty(self):
        ledger = Ledger(self.root / "nothing.jsonl")
        self.assertEqual(ledger.entries(), [])
        self.assertEqual(ledger.totals()["income"], 0)

    def test_export_csv(self):
        ledger = Ledger(self.root / "ledger.jsonl")
        ledger.record_text("尤尼 ×1,500", ts=1000.0)
        path = ledger.export_csv(self.root / "out" / "ledger.csv")
        text = path.read_text(encoding="utf-8-sig")
        self.assertIn("时间,类型,物品,数量,来源", text)
        self.assertIn("尤尼", text)
        self.assertIn("收入", text)


class TestRunLog(StoreTestBase):
    def test_record_and_stats(self):
        log = RunLog(self.root / "runs.jsonl")
        log.record("DailyTask", True, 100.0)
        log.record("DailyTask", False, 50.0)
        log.record("FlowTask", True, 30.0)

        stats = log.stats("DailyTask")
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["success"], 1)
        self.assertAlmostEqual(stats["success_rate"], 0.5)
        self.assertAlmostEqual(stats["avg_seconds"], 75.0)

    def test_stats_all_tasks(self):
        log = RunLog(self.root / "runs.jsonl")
        log.record("A", True, 10.0)
        log.record("B", True, 20.0)
        self.assertEqual(log.stats()["count"], 2)
        self.assertAlmostEqual(log.stats()["avg_seconds"], 15.0)

    def test_estimate_seconds(self):
        log = RunLog(self.root / "runs.jsonl")
        self.assertEqual(log.estimate_seconds("A", default=42.0), 42.0)
        log.record("A", True, 60.0)
        log.record("A", True, 80.0)
        self.assertAlmostEqual(log.estimate_seconds("A"), 70.0)

    def test_corrupt_rows_skipped(self):
        path = self.root / "runs.jsonl"
        log = RunLog(path)
        log.record("A", True, 10.0)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("garbage\n")
            handle.write('{"task": "A", "ok": true, "seconds": "x"}\n')
        self.assertEqual(len(log.rows()), 1)

    def test_export_csv(self):
        log = RunLog(self.root / "runs.jsonl")
        log.record("DailyTask", True, 12.5)
        path = log.export_csv(self.root / "runs.csv")
        text = path.read_text(encoding="utf-8-sig")
        self.assertIn("时间,任务,结果,用时(秒)", text)
        self.assertIn("成功", text)


class TestRunLogReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = RunLog(self.root / "runs.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_note_is_persisted(self):
        self.log.record("A", False, 3.0, note="找不到主界面")
        self.assertEqual(self.log.rows()[0]["note"], "找不到主界面")

    def test_rows_without_note_are_compatible(self):
        self.log.record("A", True, 3.0)
        self.assertEqual(self.log.rows()[0]["note"], "")

    def test_by_task_breakdown(self):
        for ok, seconds in ((True, 10.0), (True, 20.0), (False, 30.0)):
            self.log.record("A", ok, seconds)
        self.log.record("B", True, 5.0)
        breakdown = self.log.by_task()
        self.assertEqual(breakdown["A"]["count"], 3)
        self.assertEqual(breakdown["A"]["success"], 2)
        self.assertEqual(breakdown["A"]["failed"], 1)
        self.assertAlmostEqual(breakdown["A"]["avg_seconds"], 20.0)
        self.assertAlmostEqual(breakdown["A"]["total_seconds"], 60.0)
        self.assertAlmostEqual(breakdown["B"]["success_rate"], 1.0)

    def test_failure_notes_grouped_and_ordered(self):
        self.log.record("A", False, 1.0, note="超时")
        self.log.record("A", False, 1.0, note="超时")
        self.log.record("A", False, 1.0, note="识别不到")
        self.log.record("A", False, 1.0)
        notes = self.log.failure_notes()
        self.assertEqual(notes[0], ("超时", 2))
        self.assertIn(("未记录原因", 1), notes)
        self.assertEqual(len(self.log.failure_notes(limit=1)), 1)

    def test_failure_notes_ignore_success(self):
        self.log.record("A", True, 1.0)
        self.assertEqual(self.log.failure_notes(), [])

    def test_report_lines_empty(self):
        self.assertEqual(self.log.report_lines(), ["还没有运行记录"])

    def test_report_lines_single_task(self):
        self.log.record("A", True, 60.0)
        lines = self.log.report_lines()
        self.assertIn("共 1 次", lines[0])
        self.assertNotIn("按任务：", lines)

    def test_report_lines_multiple_tasks(self):
        self.log.record("A", True, 60.0)
        self.log.record("B", False, 30.0, note="卡住")
        lines = self.log.report_lines()
        self.assertIn("按任务：", lines)
        self.assertIn("失败原因：", lines)
        self.assertTrue(any("卡住" in line for line in lines))

    def test_stats_total_seconds(self):
        self.log.record("A", True, 10.0)
        self.log.record("A", True, 20.0)
        self.assertAlmostEqual(self.log.stats()["total_seconds"], 30.0)

    def test_negative_or_bad_seconds_are_clamped(self):
        """系统时间跳变会算出负数时长，不能污染平均用时（任务栏/HUD 都靠它）。"""
        self.log.record("A", True, -5)
        self.log.record("A", True, "oops")
        self.log.record("A", True, 10)
        stats = self.log.stats()
        self.assertEqual(stats["count"], 3)
        self.assertAlmostEqual(stats["total_seconds"], 10.0)
        self.assertAlmostEqual(stats["avg_seconds"], 10.0 / 3)


if __name__ == "__main__":
    unittest.main()
