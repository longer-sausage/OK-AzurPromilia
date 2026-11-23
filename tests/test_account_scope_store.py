import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.tasks.account import account_scope_store as store


class TestAccountScopeStore(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._store_path = str(Path(self._tmpdir) / "account_scoped_overrides.json")
        self._patcher = mock.patch.object(store, "_STORE_PATH", self._store_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_store(self, data):
        Path(self._store_path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_load_overrides_empty(self):
        result = store.load_overrides()
        self.assertEqual(result["account_list_text"], "")
        self.assertEqual(result["account_registry"], {})
        self.assertEqual(result["accounts"], {})

    def test_sync_account_list_text_creates_accounts(self):
        summary = store.sync_account_list_text("user1\nuser2\nuser3")
        self.assertEqual(summary["created_count"], 3)
        self.assertEqual(summary["reused_count"], 0)

    def test_sync_account_list_text_reuses_existing(self):
        store.sync_account_list_text("user1\nuser2")
        summary = store.sync_account_list_text("user1\nuser2\nuser3")
        self.assertEqual(summary["created_count"], 1)
        self.assertEqual(summary["reused_count"], 2)

    def test_resolve_account_id(self):
        store.sync_account_list_text("testuser")
        account_id = store.resolve_account_id("testuser")
        self.assertTrue(account_id)
        self.assertTrue(account_id.startswith("acc_"))

    def test_resolve_account_id_not_found(self):
        self.assertEqual(store.resolve_account_id("nonexistent"), "")

    def test_set_and_get_task_overrides(self):
        store.sync_account_list_text("user1")
        account_id = store.resolve_account_id("user1")
        store.set_account_task_overrides(account_id, "TestTask", {"key1": "value1"})
        overrides = store.get_account_task_overrides(account_id, "TestTask")
        self.assertEqual(overrides["key1"], "value1")

    def test_remove_task_overrides(self):
        store.sync_account_list_text("user1")
        account_id = store.resolve_account_id("user1")
        store.set_account_task_overrides(account_id, "TestTask", {"key1": "value1"})
        store.remove_account_task_overrides(account_id, "TestTask")
        overrides = store.get_account_task_overrides(account_id, "TestTask")
        self.assertEqual(overrides, {})

    def test_atomic_write(self):
        store.sync_account_list_text("user1")
        self.assertTrue(Path(self._store_path).exists())
        data = json.loads(Path(self._store_path).read_text(encoding="utf-8"))
        self.assertIn("account_registry", data)

    def test_corrupt_file_backup(self):
        Path(self._store_path).write_text("NOT JSON {{{", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            store.load_overrides(force=True)
        self.assertTrue(Path(f"{self._store_path}.corrupt").exists())


if __name__ == "__main__":
    unittest.main()
