"""维护脚本 scripts/lang_fill_missing.py 的健壮性测试。

真实事故：往 `assets/lang/home.json` 里加了一个 `_note` 说明键（字符串），
`python scripts/lang_fill_missing.py --dry-run` 立刻崩在 `node.get("zh_CN")`——
脚本假设每个 key 的值都是语言节点字典。文档里推荐这条命令，崩了就等于工具不可用。

所以这里锁两件事：
1. 文件里的说明键（`_note`）和写坏的节点必须被跳过，脚本不能崩；
2. 正常的「按 zh_CN 值去索引里找唯一译文补全」仍然有效。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _lang_sync_common import (  # noqa: E402
    build_lang_value_index,
    fill_missing_cross_files,
    fill_missing_from_index,
    node_have_langs,
)

LANGS = ("zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")


class TestFillMissingIsRobust(unittest.TestCase):
    def test_note_keys_are_skipped(self):
        data = {
            "_note": "这只是说明文字，不是语言节点",
            "item": {"zh_CN": {"pattern": "小份波波饼$"}},
        }
        index = build_lang_value_index([{"zh_CN": "小份波波饼$", "en_US": "Small Bobo Pie"}])
        touched = fill_missing_from_index(data, index, LANGS)
        self.assertEqual(len(touched), 1)
        self.assertEqual(data["item"]["en_US"], {"pattern": "Small Bobo Pie"})
        self.assertEqual(data["_note"], "这只是说明文字，不是语言节点")

    def test_broken_node_is_skipped(self):
        data = {"broken": "oops", "fine": {"zh_CN": {"string": "甲"}, "en_US": {"string": "A"}}}
        index = build_lang_value_index([])
        self.assertEqual(fill_missing_from_index(data, index, LANGS), [])
        self.assertIsInstance(fill_missing_cross_files({"a.json": data}, LANGS), tuple)

    def test_ambiguous_candidate_is_not_filled(self):
        """同一 zh 值对应多个不同英文值时不猜（保守），避免填错。"""
        data = {"item": {"zh_CN": {"string": "苹果"}}}
        index = build_lang_value_index(
            [{"zh_CN": "苹果", "en_US": "Apple"}, {"zh_CN": "苹果", "en_US": "Malus"}]
        )
        touched = fill_missing_from_index(data, index, LANGS)
        self.assertEqual(touched, [])
        self.assertNotIn("en_US", data["item"])

    def test_cross_file_fill(self):
        # 真实调用方传的是 Path（脚本内部按 path.name 统计），这里保持一致
        data_map = {
            Path("a.json"): {"item": {"zh_CN": {"string": "苹果"}, "en_US": {"string": "Apple"}}},
            Path("b.json"): {"item2": {"zh_CN": {"string": "苹果"}}},
        }
        _stats, touched = fill_missing_cross_files(data_map, LANGS)
        self.assertTrue(any(item[1] == "item2" for item in touched))
        self.assertIn("en_US", data_map[Path("b.json")]["item2"])

    def test_node_have_langs_ignores_empty(self):
        node = {"zh_CN": {"string": "苹果"}, "en_US": {"string": "  "}, "ja_JP": {"pattern": "りんご"}}
        have = node_have_langs(node, ("zh_CN", "en_US", "ja_JP"))
        self.assertIn("zh_CN", have)
        self.assertNotIn("en_US", have)
        self.assertIn("ja_JP", have)


if __name__ == "__main__":
    unittest.main()
