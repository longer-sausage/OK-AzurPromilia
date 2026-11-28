import json
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from ok.util.config import Config

from src.config import config
from src.core.user_data import ACCOUNT_OVERRIDES_FILE
from src.data.char_templates import CharTemplateStore
from src.tasks.account import account_scope_store as accounts
from src.tasks.onetime.daily_task import DailyTask


class TestUserData(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(chdir(root))
        self.legacy = root / "configs" / "account_scoped_overrides.json"
        self.stored = root / ACCOUNT_OVERRIDES_FILE
        self.enterContext(patch.multiple(accounts, _STORE_PATH=str(self.stored), _LEGACY_STORE_PATH=str(self.legacy),
                                         _CACHE_MTIME=object(), _CACHE_DATA={}))

    def test_framework_locations_and_project_feature_writes(self):
        self.assertEqual(config["config_folder"], "configs")
        self.assertEqual(config["screenshots_folder"], "screenshots")
        folders = ("configs", "logs", "screenshots", "ok_tasks", "ok_import")
        for folder in folders:
            Path(folder).mkdir()
            (Path(folder) / "existing").write_bytes(b"keep")
        with patch.object(Config, "config_folder", config["config_folder"]):
            Config("DailyTask", {"setting": 1})
        accounts.load_overrides()
        templates = CharTemplateStore()
        self.assertEqual(templates.templates(), ())
        self.assertFalse(Path("user_data").exists())

        accounts.sync_account_list_text("demo")
        key, = templates.add([(np.zeros((56, 56, 3), dtype=np.uint8), "Starborn")])
        runner = Mock(final_summary={"status": "完成"})
        task = SimpleNamespace(name="DailyTask", config={}, daily_runner=runner, log_info=Mock())
        self.assertTrue(DailyTask.run_daily_finally(task))

        data = Path("user_data")
        self.assertEqual({p.name for p in data.iterdir()}, {"char", "configs", "reports"})
        self.assertEqual({p.name for p in (data / "configs").iterdir()}, {"account_scoped_overrides.json"})
        self.assertTrue((data / "char" / f"{key}.png").is_file())
        self.assertEqual(json.loads((data / "char/bindings.json").read_bytes()), {key: "Starborn"})
        self.assertEqual(len(list((data / "reports").rglob("*.txt"))), 1)
        self.assertTrue(Path("configs/DailyTask.json").is_file())
        for folder in folders:
            self.assertFalse(Path(folder).is_symlink() or Path(folder).is_junction())
            self.assertEqual((Path(folder) / "existing").read_bytes(), b"keep")

    def test_first_account_update_preserves_legacy_overrides_and_sibling_files(self):
        self.legacy.parent.mkdir()
        self.legacy.write_text('{"accounts":{"alice":{"DailyTask":{"size":3}}}}', encoding="utf-8")
        sibling = self.legacy.with_name("devices.json")
        sibling.write_bytes(b"original")
        accounts.sync_account_list_text("alice\nbob")
        self.assertEqual(accounts.get_account_task_overrides("alice", "DailyTask"), {"size": 3})
        self.assertEqual(accounts.load_overrides(force=True)["account_list_text"], "alice\nbob")
        self.assertTrue(self.stored.is_file())
        self.assertFalse(self.legacy.exists())
        self.assertEqual(sibling.read_bytes(), b"original")

    def test_default_char_store_migrates_portraits_and_bindings_together(self):
        old = CharTemplateStore(Path("user_data/chara"))
        portrait = np.zeros((56, 56, 3), dtype=np.uint8)
        named, default = old.add([(portrait, "Starborn"), (portrait, None)])
        old.index.write_text(json.dumps({named: "Starborn", default: "BaseChara"}), encoding="utf-8")
        files = {path.name: path.read_bytes() for path in old.directory.iterdir()}

        current = CharTemplateStore()
        with patch.object(Path, "rename", side_effect=PermissionError), self.assertRaises(PermissionError):
            current.templates()
        self.assertFalse(current.directory.exists())
        self.assertTrue(old.index.is_file())

        self.assertEqual([(t.uuid, t.char_name) for t in current.templates()],
                         [(named, "Starborn"), (default, "BaseChar")])
        self.assertFalse(old.directory.exists())
        self.assertEqual({path.name: path.read_bytes() for path in current.directory.iterdir()}, files)
        current.bind(named, "Starborn")
        self.assertEqual(json.loads(current.index.read_bytes()), {named: "Starborn", default: "BaseChar"})

    def test_existing_char_store_takes_precedence_when_both_directories_exist(self):
        portrait = np.zeros((56, 56, 3), dtype=np.uint8)
        current = CharTemplateStore()
        new_key, = current.add([(portrait, "Starborn")])
        old = CharTemplateStore(Path("user_data/chara"))
        old_key, = old.add([(portrait, None)])
        self.assertEqual([t.uuid for t in CharTemplateStore().templates()], [new_key])
        self.assertEqual([t.uuid for t in old.templates()], [old_key])

    def test_existing_project_file_takes_precedence_and_preserves_legacy_copy(self):
        self.legacy.parent.mkdir()
        self.legacy.write_bytes(b'{"account_list_text":"old"}')
        self.stored.parent.mkdir(parents=True)
        self.stored.write_bytes(b'{"account_list_text":"new"}')
        self.assertEqual(accounts.get_account_list_text(), "new")
        self.assertEqual(self.legacy.read_bytes(), b'{"account_list_text":"old"}')
