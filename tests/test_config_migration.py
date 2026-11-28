import json
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from ok.util.config import Config

from src.core.config_migration import apply_value_migrations, migrate_config_file_keys


class TestConfigMigration(unittest.TestCase):

    def test_migrate_config_file_keys_copies_value(self):
        with tempfile.TemporaryDirectory() as folder, chdir(folder):
            for config_dir in ("configs", "custom-configs"):
                with self.subTest(config_dir=config_dir), patch.object(Config, "config_folder", config_dir):
                    config_file = Path(Config("TestTask", {"旧键": "旧值"}).config_file)
                    migrate_config_file_keys("TestTask", {"旧键": "新键"})
                    data = json.loads(config_file.read_text(encoding="utf-8"))
                    self.assertEqual(data, {"旧键": "旧值", "新键": "旧值"})
            self.assertFalse(Path("user_data").exists())

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
