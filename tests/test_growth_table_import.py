"""养成数据表格导入的测试：格式、校验、往返、真实写盘。

真实数值只能从 promilia.wiki 手工导出（站点不可达），所以导入器的报错必须写到
「第几行、哪一列错了」，用户照着改一次就能过。
"""

import tempfile
import unittest
from pathlib import Path

from src.core.growth import Target, compute_plan
from src.core.growth.model import Dataset
from src.core.growth.table_import import (
    TableImportError,
    display_rows,
    import_table,
    parse_table,
    sample_table,
)

HEADER = "key,name,kind,max_level,from,to,exp,credits,materials\n"


class TestTableParsing(unittest.TestCase):
    def test_sample_table_roundtrip(self):
        dataset = parse_table(sample_table())
        self.assertEqual(len(dataset.entries), 1)
        entry = dataset.entries["char_amber"]
        self.assertEqual(entry.name, "琥珀")
        self.assertEqual(entry.max_level, 80)
        self.assertEqual(len(entry.levels), 2)
        self.assertEqual(list(dataset.materials), ["风之碎晶", "星辉粉尘"])

    def test_tab_separated(self):
        text = (
            "key\tkind\tmax_level\tfrom\tto\texp\n"
            "pet_gale\tpet\t60\t1\t30\t9000\n"
        )
        dataset = parse_table(text)
        self.assertEqual(dataset.entries["pet_gale"].kind, "pet")

    def test_multiple_material_separators(self):
        text = HEADER + "char_a,甲,character,80,1,20,10,5,风之碎晶=3|星辉粉尘=2\n"
        materials = parse_table(text).entries["char_a"].levels[0].materials
        self.assertEqual(dict(materials), {"风之碎晶": 3.0, "星辉粉尘": 2.0})

    def test_extra_columns_ignored(self):
        text = "key,kind,max_level,from,to,exp,备注\nchar_a,character,80,1,20,10,随便写\n"
        self.assertEqual(len(parse_table(text).entries), 1)

    def test_plan_can_be_computed_from_imported_data(self):
        dataset = parse_table(sample_table())
        plan = compute_plan(dataset, [Target("char_amber", to_level=40)])
        self.assertEqual(plan.materials["风之碎晶"], 9.0)  # 3 + 6
        self.assertEqual(plan.materials["星辉粉尘"], 1.0)

    def test_rows_sorted_by_level(self):
        text = (
            HEADER
            + "char_a,甲,character,80,21,40,200,100,\n"
            + "char_a,甲,character,80,1,20,100,50,\n"
        )
        spans = parse_table(text).entries["char_a"].levels
        self.assertEqual([(s.start, s.end) for s in spans], [(1, 20), (21, 40)])


class TestTableErrors(unittest.TestCase):
    def assert_error_mentions(self, text: str, *fragments: str):
        with self.assertRaises(TableImportError) as ctx:
            parse_table(text)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_empty(self):
        self.assert_error_mentions("   ", "空")

    def test_missing_columns(self):
        self.assert_error_mentions("key,from\nchar_a,1\n", "缺少必需的列")

    def test_bad_kind_points_to_row(self):
        self.assert_error_mentions(
            HEADER + "char_a,甲,hero,80,1,20,10,5,\n", "第 2 行", "kind"
        )

    def test_bad_number_points_to_row_and_column(self):
        self.assert_error_mentions(
            HEADER + "char_a,甲,character,80,1,20,很多,5,\n", "第 2 行", "exp"
        )

    def test_invalid_range(self):
        self.assert_error_mentions(
            HEADER + "char_a,甲,character,80,20,20,10,5,\n", "区间无效"
        )

    def test_bad_material_syntax(self):
        self.assert_error_mentions(
            HEADER + "char_a,甲,character,80,1,20,10,5,风之碎晶x3\n", "名字=数量"
        )

    def test_missing_key(self):
        self.assert_error_mentions(HEADER + ",甲,character,80,1,20,10,5,\n", "缺少 key")

    def test_max_level_must_be_positive(self):
        self.assert_error_mentions(
            HEADER + "char_a,甲,character,0,1,20,10,5,\n", "max_level"
        )


class TestImportWrites(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.table = self.root / "table.csv"
        self.table.write_text(sample_table(), encoding="utf-8")
        self.target = self.root / "growth.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_import_writes_and_backs_up(self):
        self.target.write_text('{"entries": []}', encoding="utf-8")
        result = import_table(self.table, target=self.target)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.path, self.target)
        self.assertIsNotNone(result.backup)
        # 备份跟目标放一起：导入到别的路径时不该往仓库的 assets/data/backups 里写
        self.assertEqual(result.backup.parent, self.root / "backups")
        loaded = Dataset.from_file(self.target)
        self.assertIn("char_amber", loaded.entries)

    def test_bad_table_writes_nothing(self):
        self.target.write_text("原样", encoding="utf-8")
        bad = self.root / "bad.csv"
        bad.write_text(HEADER + "char_a,甲,hero,80,1,20,10,5,\n", encoding="utf-8")
        result = import_table(bad, target=self.target)
        self.assertFalse(result.ok)
        self.assertIn("kind", result.message)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "原样")

    def test_missing_source(self):
        result = import_table(self.root / "nope.csv", target=self.target)
        self.assertFalse(result.ok)
        self.assertIn("读不了表格", result.message)

    def test_display_rows_mentions_spans(self):
        rows = display_rows(parse_table(sample_table()))
        self.assertTrue(any("1-20" in row for row in rows))


class TestDatasetSerialization(unittest.TestCase):
    """``to_dict`` / ``to_json`` 要能原样读回来（导入器依赖它写盘）。"""

    def test_bundled_dataset_roundtrips(self):
        dataset = Dataset.load_default()
        again = Dataset.from_json(dataset.to_json())
        self.assertEqual(set(dataset.entries), set(again.entries))
        self.assertEqual(dataset.materials, again.materials)
        self.assertEqual(dataset.exp_items, again.exp_items)
        self.assertEqual(dataset.aliases, again.aliases)
        self.assertEqual(
            [(d.name, d.stamina, dict(d.drops)) for d in dataset.dungeons],
            [(d.name, d.stamina, dict(d.drops)) for d in again.dungeons],
        )

    def test_potentials_and_star_stages_survive(self):
        dataset = Dataset.load_default()
        again = Dataset.from_json(dataset.to_json())
        for key, entry in dataset.entries.items():
            self.assertEqual(len(entry.potentials), len(again.entries[key].potentials), key)
            self.assertEqual(len(entry.star_stages), len(again.entries[key].star_stages), key)


if __name__ == "__main__":
    unittest.main()
