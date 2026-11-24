"""一条龙「执行情况汇总」的测试。

这个模块之前没有任何测试，而它生成的是用户**直接阅读**的东西（日志 + txt 报告），
最典型的漏法是：文案没翻译（英文用户看到中文）、多轮/多账号的分组显示错乱、
报告文件同秒重名被覆盖、旧报告不清理。
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from src.tasks.daily.daily_summary import (
    build_summary_lines,
    create_task_summary_report,
    format_failure_details_by_account,
    get_software_name,
    iter_summary_candidates,
)


class FakeTask:
    name = "一键日常"

    def tr(self, text):
        return text


def summary_info(**overrides):
    base = {
        "status": "完成",
        "exception": "",
        "current_task": "",
        "all_fail_tasks": [],
        "per_round": [
            {
                "round": 1,
                "success": ["家园日常", "每日委托"],
                "failed": [],
                "skipped": ["周常"],
                "all": [],
            }
        ],
        "failure_details": {},
    }
    base.update(overrides)
    return base


class TestBuildSummaryLines(unittest.TestCase):
    def test_all_success_shows_confirmation(self):
        lines = build_summary_lines(FakeTask(), summary_info())
        text = "\n".join(lines)
        self.assertIn("所有任务执行成功！", text)
        self.assertIn("总任务数: 3 | 成功: 2 | 失败: 0 | 跳过: 1", text)

    def test_partial_failure_lists_failed_tasks(self):
        info = summary_info(
            status="部分失败",
            all_fail_tasks=[(1, ["每日委托"])],
        )
        text = "\n".join(build_summary_lines(FakeTask(), info))
        self.assertIn("失败任务统计:", text)
        self.assertIn("第1轮: 每日委托", text)
        self.assertNotIn("所有任务执行成功！", text)

    def test_legacy_bare_list_all_fail_tasks(self):
        info = summary_info(status="部分失败", all_fail_tasks=["每日委托"])
        text = "\n".join(build_summary_lines(FakeTask(), info))
        self.assertIn("  每日委托", text)

    def test_exception_and_current_task_sections(self):
        info = summary_info(status="异常结束", exception="连接断开", current_task="家园日常")
        text = "\n".join(build_summary_lines(FakeTask(), info))
        self.assertIn("异常信息:", text)
        self.assertIn("连接断开", text)
        self.assertIn("当前正在执行的任务:", text)

    def test_unknown_status_falls_back(self):
        text = "\n".join(build_summary_lines(FakeTask(), summary_info(status="")))
        self.assertIn("未知", text)

    def test_multi_round_prints_headers_and_accounts(self):
        info = summary_info(
            per_round=[
                {"round": 1, "success": ["A"], "failed": [], "skipped": [], "account_user": "小明"},
                {"round": 2, "success": [], "failed": ["B"], "skipped": [], "account_id": "42"},
            ]
        )
        text = "\n".join(build_summary_lines(FakeTask(), info))
        self.assertIn("第 1 轮 (账号: 小明)", text)
        self.assertIn("第 2 轮 (账号: id:42)", text)

    def test_single_round_single_account_stays_compact(self):
        text = "\n".join(build_summary_lines(FakeTask(), summary_info()))
        self.assertNotIn("轮 (账号", text)

    def test_empty_lists_show_none(self):
        info = summary_info(
            per_round=[{"round": 1, "success": [], "failed": [], "skipped": [], "all": []}]
        )
        text = "\n".join(build_summary_lines(FakeTask(), info))
        self.assertIn("无", text)


class TestFailureDetails(unittest.TestCase):
    def test_groups_by_account(self):
        per_round = [{"account_id": "7", "account_user": "小明"}]
        details = {"7": {"每日委托": "没找到按钮"}, "": {"结算": "OCR 失败"}}
        lines = format_failure_details_by_account(per_round, details)
        text = "\n".join(lines)
        self.assertIn("账号: 小明", text)
        self.assertIn("没找到按钮", text)
        self.assertIn("OCR 失败", text)

    def test_unknown_account_id_shows_raw_id(self):
        lines = format_failure_details_by_account([], {"9": {"A": "boom"}})
        self.assertIn("id:9", "\n".join(lines))

    def test_empty_details_returns_nothing(self):
        self.assertEqual(format_failure_details_by_account([], {}), [])
        self.assertEqual(format_failure_details_by_account([], None), [])

    def test_blank_message_gets_placeholder(self):
        lines = format_failure_details_by_account([], {"": {"A": ""}})
        self.assertIn("未设置失败消息", "\n".join(lines))


class TestReportFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_report_contains_summary(self):
        path = create_task_summary_report(FakeTask(), self.base, summary_info())
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".txt")
        self.assertIn("所有任务执行成功！", path.read_text(encoding="utf-8"))
        self.assertIn(FakeTask.name, path.parent.name)

    def test_same_second_does_not_overwrite(self):
        first = create_task_summary_report(FakeTask(), self.base, summary_info())
        second = create_task_summary_report(FakeTask(), self.base, summary_info())
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file() and second.is_file())

    def test_old_reports_are_pruned(self):
        target_dir = self.base / get_software_name() / FakeTask.name
        target_dir.mkdir(parents=True, exist_ok=True)
        stale = target_dir / "一键日常_20000101_000000.txt"
        stale.write_text("旧报告", encoding="utf-8")
        old = time.time() - 30 * 24 * 3600
        os.utime(stale, (old, old))

        fresh = create_task_summary_report(FakeTask(), self.base, summary_info(), keep_days=7)
        self.assertFalse(stale.exists(), "超过保留天数的旧报告应被清掉")
        self.assertTrue(fresh.exists())

    def test_candidates_generate_unique_names(self):
        names = iter_summary_candidates("一键日常_20260101_000000.txt")
        generated = [next(names) for _ in range(3)]
        self.assertEqual(generated[0], "一键日常_20260101_000000.txt")
        self.assertEqual(len(set(generated)), 3)

    def test_software_name_has_fallback(self):
        self.assertTrue(get_software_name())


if __name__ == "__main__":
    unittest.main()
