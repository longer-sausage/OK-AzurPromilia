"""队列预计耗时的纯逻辑测试：最近 5 次平均、未知项、格式化、队列拼行。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.core import queue_eta as qe


class TestFormat(unittest.TestCase):
    def test_short(self):
        self.assertEqual(qe.format_duration(45), "约 45 秒")

    def test_minutes(self):
        self.assertEqual(qe.format_duration(600), "约 10 分钟")
        self.assertEqual(qe.format_duration(750), "约 12 分 30 秒")

    def test_hours(self):
        self.assertEqual(qe.format_duration(3600), "约 1 小时")
        self.assertEqual(qe.format_duration(3900), "约 1 小时 5 分")

    def test_unknown(self):
        self.assertEqual(qe.format_duration(None), "未知")
        self.assertEqual(qe.format_duration("abc"), "未知")
        self.assertEqual(qe.format_duration(-1), "未知")
        self.assertEqual(qe.format_duration(float("nan")), "未知")


class TestAverage(unittest.TestCase):
    def test_average_of_available_runs(self):
        self.assertAlmostEqual(qe.average_seconds([10, 20, 30]), 20.0)

    def test_only_last_five_count(self):
        self.assertAlmostEqual(qe.average_seconds([100, 100, 100, 10, 20, 30, 40, 50]), 30.0)

    def test_no_history(self):
        self.assertIsNone(qe.average_seconds([]))
        self.assertIsNone(qe.average_seconds(None))

    def test_drops_noise(self):
        # 1 秒的误触、NaN、非法值都不该拉低平均值
        self.assertAlmostEqual(qe.average_seconds([1.0, float("nan"), "x", 60, 120]), 90.0)


class TestRecord(unittest.TestCase):
    def test_records_and_trims(self):
        history = {}
        for seconds in (10, 20, 30, 40, 50, 60):
            qe.record_run(history, "日常", seconds)
        self.assertEqual(len(history["日常"]), qe.KEEP_RUNS)
        self.assertEqual(history["日常"], [20.0, 30.0, 40.0, 50.0, 60.0])

    def test_ignores_garbage(self):
        history = {}
        qe.record_run(history, "日常", 1)  # 太短
        qe.record_run(history, "日常", None)
        qe.record_run(history, "日常", "x")
        qe.record_run(history, "", 100)  # 没名字
        self.assertEqual(history, {})

    def test_nan_ignored(self):
        history = {}
        qe.record_run(history, "日常", float("nan"))
        self.assertEqual(history, {})


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "task_times.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        history = {"日常": [10.0, 20.0], "收菜": [5.0]}
        qe.save_history(history, self.path)
        self.assertEqual(qe.load_history(self.path), history)

    def test_corrupt_file_is_empty(self):
        self.path.write_text("{不是 json", encoding="utf-8")
        self.assertEqual(qe.load_history(self.path), {})

    def test_missing_file_is_empty(self):
        self.assertEqual(qe.load_history(self.path), {})

    def test_dirty_entries_are_cleaned(self):
        self.path.write_text(
            json.dumps({"日常": [10, "x", float("nan"), 1], "坏任务": "不是列表"}), encoding="utf-8"
        )
        loaded = qe.load_history(self.path)
        self.assertEqual(loaded, {"日常": [10.0]})

    def test_save_skips_empty(self):
        qe.save_history({"空任务": []}, self.path)
        self.assertEqual(qe.load_history(self.path), {})


class TestQueueRows(unittest.TestCase):
    def test_rows_put_running_first_and_dedupe(self):
        history = {"日常": [60, 60], "收菜": [30, 30]}
        rows = qe.build_rows(history, "日常", ["收菜", "日常", ""])
        self.assertEqual([row["name"] for row in rows], ["日常", "收菜"])
        self.assertEqual(rows[0]["state"], "running")
        self.assertEqual(rows[1]["state"], "queued")
        self.assertEqual(rows[0]["eta_text"], "约 1 分钟")

    def test_unknown_task_shows_unknown(self):
        rows = qe.build_rows({}, None, ["没跑过的"])
        self.assertEqual(rows[0]["eta_text"], "未知")
        self.assertIsNone(rows[0]["seconds"])

    def test_empty_queue(self):
        self.assertEqual(qe.build_rows({}, None, []), [])


class TestTotal(unittest.TestCase):
    def test_total_sums_known(self):
        history = {"日常": [60], "收菜": [30]}
        rows = qe.build_rows(history, None, ["日常", "收菜"])
        self.assertEqual(qe.total_text(history, rows), "约 1 分 30 秒")

    def test_total_marks_unknown(self):
        history = {"日常": [60]}
        rows = qe.build_rows(history, None, ["日常", "没跑过的"])
        text = qe.total_text(history, rows)
        self.assertIn("约 1 分钟", text)
        self.assertIn("1 项未知", text)

    def test_total_without_any_known(self):
        rows = qe.build_rows({}, None, ["没跑过的"])
        self.assertEqual(qe.total_text({}, rows), "未知")

    def test_total_without_rows(self):
        self.assertEqual(qe.total_text({}, []), "未知")


if __name__ == "__main__":
    unittest.main()
