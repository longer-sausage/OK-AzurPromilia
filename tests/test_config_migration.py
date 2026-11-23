import json
import tempfile
import unittest
from pathlib import Path

from src.core.config_migration import apply_value_migrations, migrate_config_file_keys


class TestConfigMigration(unittest.TestCase):

    def test_migrate_config_file_keys_copies_value(self):
        from unittest import mock

        configs_dir = tempfile.mkdtemp()
        config_dir = Path(configs_dir) / "configs"
        config_dir.mkdir()
        config_file = config_dir / "TestTask.json"
        config_file.write_text(json.dumps({"旧键": "旧值"}), encoding="utf-8")

        migrations = {"旧键": "新键"}

        with mock.patch(
            "src.core.config_migration.get_relative_path",
            side_effect=lambda *parts: str(Path(configs_dir, *parts)),
        ):
            migrate_config_file_keys("TestTask", migrations)

        data = json.loads(config_file.read_text(encoding="utf-8"))
        self.assertEqual(data["旧键"], "旧值")
        self.assertEqual(data["新键"], "旧值")

    def test_apply_value_migrations_no_marker(self):
        from src.core.config_migration import _NO_MIGRATION

        def transform(config, new_key):
            return _NO_MIGRATION

        config = {"已存在": True}
        modified_config, modified = apply_value_migrations(config, {"新键": transform})
        self.assertFalse(modified)
        self.assertNotIn("新键", modified_config)


if __name__ == "__main__":
    unittest.main()