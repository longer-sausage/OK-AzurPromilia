"""安装自检测试：每类文件的好/坏两种情形，以及聚合与退出码。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.core.health_check import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARNING,
    CheckResult,
    HealthReport,
    check_config_files,
    check_flows,
    check_queues,
    check_routes,
    check_schemes,
    run_all,
)

GOOD_FLOW = {
    "name": "好的",
    "steps": [
        {"type": "wait", "seconds": 1},
        {"type": "click", "x": 0.5, "y": 0.5},
    ],
}
BAD_FLOW = {"name": "坏的", "steps": [{"type": "click", "x": 5, "y": 0.5}]}  # 比例越界
GOOD_QUEUE = {"name": "队列", "items": [{"kind": "flow", "ref": "好的"}]}
EMPTY_QUEUE = {"name": "空队列", "items": []}
GOOD_SCHEME = {"name": "方案", "segments": [{"steps": [{"type": "wait", "seconds": 1}]}]}
GOOD_ROUTE = {"name": "路线", "waypoints": [{"page": "主界面", "click": [0.5, 0.5]}]}


def write(folder: Path, name: str, payload) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestSingleChecks(unittest.TestCase):
    def test_flows_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "好的", GOOD_FLOW)
            self.assertEqual(check_flows(tmp).level, LEVEL_INFO)

    def test_flows_bad(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "坏的", BAD_FLOW)
            result = check_flows(tmp)
            self.assertEqual(result.level, LEVEL_ERROR)
            self.assertFalse(result.ok)
            self.assertTrue(any("坏的" in detail for detail in result.details))

    def test_flows_unparsable(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            folder.mkdir(exist_ok=True)
            (folder / "坏文件.json").write_text("{ oops", encoding="utf-8")
            result = check_flows(folder)
            self.assertFalse(result.ok)

    def test_flows_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = check_flows(Path(tmp) / "none")
            self.assertTrue(result.ok)
            self.assertIn("没有文件", result.message)

    def test_queues(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "队列", GOOD_QUEUE)
            self.assertTrue(check_queues(tmp).ok)
            write(Path(tmp), "空队列", EMPTY_QUEUE)
            result = check_queues(tmp)
            self.assertFalse(result.ok)
            self.assertTrue(any("空" in detail for detail in result.details))

    def test_schemes(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "方案", GOOD_SCHEME)
            self.assertTrue(check_schemes(tmp).ok)
            write(Path(tmp), "空方案", {"name": "空方案", "segments": []})
            self.assertFalse(check_schemes(tmp).ok)

    def test_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "路线", GOOD_ROUTE)
            self.assertTrue(check_routes(tmp).ok)
            write(Path(tmp), "坏路线", {"name": "坏路线", "waypoints": [{"page": ""}]})
            self.assertFalse(check_routes(tmp).ok)

    def test_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.json").write_text("{}", encoding="utf-8")
            self.assertTrue(check_config_files(folder).ok)
            (folder / "b.json").write_text("{ oops", encoding="utf-8")
            result = check_config_files(folder)
            self.assertFalse(result.ok)
            self.assertIn("b.json", " ".join(result.details))


class TestReport(unittest.TestCase):
    def test_ok_when_no_errors(self):
        report = HealthReport(results=[CheckResult(name="a"), CheckResult(name="b", level=LEVEL_WARNING)])
        self.assertTrue(report.ok)
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("提示", report.summary())

    def test_error_makes_report_not_ok(self):
        report = HealthReport(results=[CheckResult(name="a", ok=False, level=LEVEL_ERROR, message="坏了")])
        self.assertFalse(report.ok)
        self.assertIn("1 项有问题", report.summary())
        self.assertTrue(any("❌" in line for line in report.lines()))

    def test_lines_include_details(self):
        report = HealthReport(results=[CheckResult(name="a", details=("细节一",))])
        self.assertTrue(any("细节一" in line for line in report.lines()))

    def test_run_all_aggregates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configs = root / "configs"
            write(configs / "flows", "好的", GOOD_FLOW)
            write(configs / "flows", "坏的", BAD_FLOW)
            write(configs, "app", {"x": 1})
            report = run_all(root)
            names = [result.name for result in report.results]
            self.assertIn("流程", names)
            self.assertIn("配置", names)
            self.assertFalse(report.ok)
            self.assertTrue(report.summary())

    def test_run_all_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_all(tmp)
            self.assertGreaterEqual(len(report.results), 5)


if __name__ == "__main__":
    unittest.main()
