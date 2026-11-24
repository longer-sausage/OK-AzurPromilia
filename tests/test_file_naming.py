"""文件名补全与导出路径的测试。

「另存为」对话框不会替用户加扩展名：手输 `养成材料单` 写出来的文件没有 `.csv`，
双击打不开，用户会以为导出失败了。补在导出函数里，所有调用方都受益。
"""

import tempfile
import unittest
from pathlib import Path

from src.core.file_naming import ensure_suffix
from src.core.growth import Dataset, Target, compute_plan, export_csv
from src.core.ledger import Ledger, LedgerEntry, RunLog


class TestEnsureSuffix(unittest.TestCase):
    def test_appends_when_missing(self):
        self.assertEqual(ensure_suffix("材料单", ".csv").name, "材料单.csv")
        self.assertEqual(ensure_suffix(Path("out/材料单"), ".png").name, "材料单.png")

    def test_keeps_existing(self):
        self.assertEqual(ensure_suffix("a.csv", ".csv").name, "a.csv")
        self.assertEqual(ensure_suffix("a.CSV", ".csv").name, "a.CSV")

    def test_replaces_wrong_suffix(self):
        self.assertEqual(ensure_suffix("a.png", ".csv").name, "a.csv")

    def test_suffix_without_dot(self):
        self.assertEqual(ensure_suffix("a", "csv").name, "a.csv")

    def test_name_with_spaces_is_not_mistaken_for_suffix(self):
        self.assertEqual(ensure_suffix("养成 材料单", ".csv").name, "养成 材料单.csv")


class TestExportSuffixes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_growth_csv(self):
        plan = compute_plan(Dataset.load_default(), [Target("char_amber", to_level=20)])
        written = export_csv(plan, self.root / "材料单")
        self.assertEqual(written.suffix, ".csv")
        self.assertTrue(written.is_file())

    def test_growth_png(self):
        from src.core.growth import export_png

        plan = compute_plan(Dataset.load_default(), [Target("char_amber", to_level=20)])
        written = export_png(plan, self.root / "材料单")
        self.assertEqual(written.suffix, ".png")
        self.assertTrue(written.is_file())

    def test_ledger_csv(self):
        ledger = Ledger(self.root / "ledger.jsonl")
        ledger.record([LedgerEntry(item="尤尼", amount=10)])
        written = ledger.export_csv(self.root / "账单")
        self.assertEqual(written.suffix, ".csv")
        self.assertTrue(written.is_file())

    def test_run_log_csv(self):
        run_log = RunLog(self.root / "runs.jsonl")
        run_log.record("日常任务", True, 5.0)
        written = run_log.export_csv(self.root / "报表")
        self.assertEqual(written.suffix, ".csv")
        self.assertTrue(written.is_file())


if __name__ == "__main__":
    unittest.main()
