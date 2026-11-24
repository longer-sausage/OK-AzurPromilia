"""子任务配置摘要测试：读文件容错、与默认值对比、值格式化、截断。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.core.task_config_summary import (
    MAX_VALUE_LENGTH,
    config_path,
    has_own_config,
    load_task_config,
    summarize_config,
    summary_line,
)


class TestConfigPath(unittest.TestCase):
    def test_default_folder(self):
        self.assertEqual(config_path("家园每日"), Path("configs") / "家园每日.json")

    def test_custom_folder(self):
        self.assertEqual(config_path(" A ", "tmp"), Path("tmp") / "A.json")

    def test_has_own_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(has_own_config("家园每日", tmp))
            (Path(tmp) / "家园每日.json").write_text("{}", encoding="utf-8")
            self.assertTrue(has_own_config("家园每日", tmp))


class TestLoadTaskConfig(unittest.TestCase):
    def test_loads_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "A.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
            self.assertEqual(load_task_config("A", tmp), {"x": 1})

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_task_config("nope", tmp), {})

    def test_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "A.json").write_text("{ oops", encoding="utf-8")
            self.assertEqual(load_task_config("A", tmp), {})

    def test_non_dict_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "A.json").write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(load_task_config("A", tmp), {})


class TestSummarizeConfig(unittest.TestCase):
    def test_only_non_default_keys(self):
        config = {"每日委托": "金币本", "战斗次数": 3, "开关": True}
        defaults = {"每日委托": "经验本", "战斗次数": 3}
        items = summarize_config(config, defaults)
        self.assertEqual(items, ["每日委托=金币本", "开关=开"])

    def test_hidden_keys_skipped(self):
        self.assertEqual(summarize_config({"_内部": 1, "可见": "x"}), ["可见=x"])

    def test_value_formatting(self):
        items = summarize_config(
            {"布尔": False, "整数": 5, "小数": 1.5, "整浮点": 2.0, "列表": ["a", "b"], "空串": "", "字典": {"a": 1}},
            limit=10,
        )
        self.assertEqual(items, ["布尔=关", "整数=5", "小数=1.5", "整浮点=2", "列表=a、b"])

    def test_long_value_truncated(self):
        items = summarize_config({"说明": "很长的值" * 20})
        self.assertEqual(len(items), 1)
        value = items[0].split("=", 1)[1]
        self.assertEqual(len(value), MAX_VALUE_LENGTH)
        self.assertTrue(value.endswith("…"))

    def test_limit(self):
        config = {f"k{index}": index for index in range(10)}
        self.assertEqual(len(summarize_config(config, limit=3)), 3)
        self.assertEqual(len(summarize_config(config, limit=0)), 1)  # 至少留一项

    def test_empty(self):
        self.assertEqual(summarize_config({}), [])
        self.assertEqual(summarize_config(None), [])


class TestSummaryLine(unittest.TestCase):
    def test_without_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("还没有独立配置", summary_line("家园每日", folder=tmp))

    def test_all_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "A.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
            self.assertEqual(summary_line("A", defaults={"x": 1}, folder=tmp), "全部是默认值")

    def test_joined_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "A.json").write_text(json.dumps({"a": 1, "b": "二"}), encoding="utf-8")
            self.assertEqual(summary_line("A", folder=tmp), "a=1，b=二")

    def test_limit_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {f"k{index}": index for index in range(6)}
            (Path(tmp) / "A.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(len(summary_line("A", folder=tmp, limit=2).split("，")), 2)


if __name__ == "__main__":
    unittest.main()
