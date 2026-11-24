"""示例资源测试：流程/队列/路线的解析校验、模板依赖、安装与不覆盖。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.flow import templates, validate_flow
from src.core.flow.model import load_flow_by_name
from src.core.flow.templates import (
    FlowTemplateError,
    example_target,
    feature_names,
    install_all_examples,
    install_examples,
    install_templates,
    list_examples,
    list_templates,
    load_template,
    missing_templates,
    required_templates,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_template(folder: Path, name: str, payload: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestShippedTemplates(unittest.TestCase):
    def test_three_templates_present(self):
        names = list_templates(REPO_ROOT / "assets" / "flow_templates")
        self.assertEqual(len(names), 3)
        self.assertIn("周常活跃奖励", names)

    def test_every_template_is_valid(self):
        for name in list_templates(REPO_ROOT / "assets" / "flow_templates"):
            with self.subTest(template=name):
                flow = load_template(name, REPO_ROOT / "assets" / "flow_templates")
                errors = [issue for issue in validate_flow(flow) if issue.level == "error"]
                self.assertEqual(errors, [], f"{name} 有错误: {errors}")
                self.assertGreater(flow.step_count(), 0)

    def test_weekly_template_needs_no_new_features(self):
        template_dir = REPO_ROOT / "assets" / "flow_templates"
        flow = load_template("周常活跃奖励", template_dir)
        self.assertEqual(missing_templates(flow), [])

    def test_skeletons_report_their_placeholders(self):
        template_dir = REPO_ROOT / "assets" / "flow_templates"
        crisis = missing_templates(load_template("危机讨伐（骨架）", template_dir))
        story = missing_templates(load_template("剧情委托（骨架）", template_dir))
        self.assertIn("crisis_hunt_entry", crisis)
        self.assertEqual(len(crisis), 3)
        self.assertIn("story_commission_settle", story)
        self.assertEqual(len(story), 3)

    def test_feature_names_reads_enum_values(self):
        names = feature_names()
        self.assertIn("loading_check", names)
        self.assertIn("confirm_button", names)
        self.assertNotIn("FeatureList.loading_check", names)


class TestRequiredTemplates(unittest.TestCase):
    def test_collects_nested_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_template(
                folder,
                "A",
                {
                    "name": "A",
                    "steps": [
                        {"type": "click", "template": "entry"},
                        {
                            "type": "loop",
                            "until_not_template": "claim",
                            "max": 3,
                            "steps": [
                                {"type": "if", "template": "confirm", "steps": [{"type": "click", "template": "entry"}]}
                            ],
                        },
                    ],
                },
            )
            flow = load_template("A", folder)
        self.assertEqual(required_templates(flow), ["entry", "claim", "confirm"])

    def test_missing_with_explicit_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_template(
                folder,
                "A",
                {"name": "A", "steps": [{"type": "wait_template", "template": "x"}, {"type": "wait", "seconds": 1}]},
            )
            flow = load_template("A", folder)
        self.assertEqual(missing_templates(flow, available={"x"}), [])
        self.assertEqual(missing_templates(flow, available=set()), ["x"])

    def test_flow_without_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_template(folder, "A", {"name": "A", "steps": [{"type": "wait", "seconds": 0.5}]})
            flow = load_template("A", folder)
        self.assertEqual(required_templates(flow), [])


class TestInstallTemplates(unittest.TestCase):
    def test_install_sanitizes_name_with_full_width_parens(self):
        """示例名带全角括号时，落盘名要被清洗，且装完能用名字读回来。

        真实 bug：直接拼 `target/剧情委托（骨架）.json` 会写出带全角括号的文件，
        而 `load_flow_by_name` 用 `flow_path` 再清洗成 `剧情委托_骨架.json` → 读不到。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates"
            target = root / "flows"
            name = "剧情委托（骨架）"
            write_template(source, name, {"name": name, "steps": [{"type": "wait", "seconds": 1}]})

            report = install_templates(target_folder=target, folder=source)
            self.assertEqual(len(report.installed), 1, report.describe())
            installed = {path.name for path in target.glob("*.json")}
            self.assertEqual(installed, {"剧情委托_骨架.json"})

            flow = load_flow_by_name(name, target)
            self.assertEqual(flow.name, name)

    def test_load_legacy_file_with_raw_name(self):
        """已经有带全角括号的旧文件时也要能读（不要求用户重装）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "危机讨伐（骨架）"
            (root / f"{name}.json").write_text(
                json.dumps({"name": name, "steps": [{"type": "wait", "seconds": 1}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            flow = load_flow_by_name(name, root)
            self.assertEqual(flow.name, name)

    def test_install_copies_and_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates"
            target = root / "flows"
            write_template(source, "A", {"name": "A", "steps": [{"type": "wait", "seconds": 1}]})
            write_template(source, "B", {"name": "B", "steps": [{"type": "wait", "seconds": 1}]})

            report = install_templates(target_folder=target, folder=source)
            self.assertEqual(len(report.installed), 2)
            self.assertEqual(report.skipped, [])
            self.assertTrue((target / "A.json").is_file())

            again = install_templates(target_folder=target, folder=source)
            self.assertEqual(len(again.installed), 0)
            self.assertEqual(len(again.skipped), 2)
            self.assertIn("不覆盖", again.describe())

    def test_install_with_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates"
            target = root / "flows"
            write_template(source, "A", {"name": "A", "steps": [{"type": "wait", "seconds": 1}]})
            (target).mkdir()
            (target / "A.json").write_text("{}", encoding="utf-8")
            report = install_templates(target_folder=target, folder=source, overwrite=True)
            self.assertEqual(len(report.installed), 1)
            self.assertIn("steps", (target / "A.json").read_text(encoding="utf-8"))

    def test_bad_template_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates"
            source.mkdir()
            (source / "bad.json").write_text("{ oops", encoding="utf-8")
            report = install_templates(target_folder=root / "flows", folder=source)
            self.assertEqual(len(report.installed), 0)
            self.assertEqual(len(report.skipped), 1)

    def test_describe_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = install_templates(target_folder=Path(tmp) / "flows", folder=Path(tmp) / "none")
        self.assertEqual(report.describe(), "没有可安装的模板")

    def test_list_missing_folder(self):
        self.assertEqual(list_templates(Path("no-such-folder")), [])

    def test_load_missing_template(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FlowTemplateError):
            load_template("nope", Path(tmp))


class TestOtherExampleKinds(unittest.TestCase):
    def test_queue_example_parses(self):
        from src.core.queue import load_queue

        names = list_examples("queue", REPO_ROOT / "assets" / "queue_templates")
        self.assertEqual(names, ["一条龙"])
        queue = load_queue("一条龙", REPO_ROOT / "assets" / "queue_templates")
        self.assertEqual(len(queue.items), 3)
        self.assertTrue(queue.stop_on_failure)
        self.assertEqual([item.kind for item in queue.items], ["task", "task", "task"])
        self.assertTrue(all(item.budget_minutes > 0 for item in queue.items))

    def test_route_example_is_valid(self):
        from src.core.route import load_route

        names = list_examples("route", REPO_ROOT / "assets" / "route_templates")
        self.assertEqual(names, ["主界面到背包"])
        route = load_route("主界面到背包", REPO_ROOT / "assets" / "route_templates")
        self.assertEqual(route.validate(), [])
        self.assertGreater(len(route.waypoints), 1)

    def test_install_each_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for kind, count in (("flow", 3), ("queue", 1), ("route", 1)):
                with self.subTest(kind=kind):
                    report = install_examples(kind, target_folder=root / kind)
                    self.assertEqual(len(report.installed), count)
                    self.assertEqual(len(list((root / kind).glob("*.json"))), count)

    def test_queue_example_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(len(install_examples("queue", target_folder=root).installed), 1)
            second = install_examples("queue", target_folder=root)
            self.assertEqual(len(second.installed), 0)
            self.assertIn("不覆盖", second.describe())

    def test_bad_queue_example_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "坏队列.json").write_text(
                json.dumps({"items": [{"kind": "nope", "ref": "x"}]}), encoding="utf-8"
            )
            report = install_examples("queue", target_folder=root / "out", folder=source)
            self.assertEqual(len(report.installed), 0)
            self.assertEqual(len(report.skipped), 1)

    def test_bad_route_example_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "坏路线.json").write_text(json.dumps({"waypoints": [{"page": ""}]}), encoding="utf-8")
            report = install_examples("route", target_folder=root / "out", folder=source)
            self.assertEqual(len(report.installed), 0)
            self.assertEqual(len(report.skipped), 1)

    def test_install_all_examples_counts_three_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = {
                "flow": root / "flow_src",
                "queue": root / "queue_src",
                "route": root / "route_src",
            }
            shutil.copytree(REPO_ROOT / "assets" / "flow_templates", sources["flow"])
            shutil.copytree(REPO_ROOT / "assets" / "queue_templates", sources["queue"])
            shutil.copytree(REPO_ROOT / "assets" / "route_templates", sources["route"])
            targets = {kind: root / f"out_{kind}" for kind in sources}
            with mock.patch.dict(templates.EXAMPLE_SOURCES, sources):
                report = install_all_examples(targets=targets)
            self.assertEqual(len(report.installed), 5)  # 3 流程 + 1 队列 + 1 路线
            for kind, target in targets.items():
                self.assertTrue(list(target.glob("*.json")), kind)

    def test_unknown_kind(self):
        with self.assertRaises(FlowTemplateError):
            example_target("exe")


if __name__ == "__main__":
    unittest.main()
