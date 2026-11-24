"""失败帧库测试：落盘、索引、修剪、容错。"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.core.failure_frame_store import (
    INDEX_NAME,
    FailureFrameRecord,
    FailureFrameStore,
)


def _frame(value: int = 128, width: int = 64, height: int = 36) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


class FailureFrameStoreTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "failure_frames"

    def tearDown(self):
        self._tmp.cleanup()

    def store(self, limit: int = 20) -> FailureFrameStore:
        return FailureFrameStore(root=self.root, limit=limit)


class TestSaveAndList(FailureFrameStoreTestBase):
    def test_save_writes_image_and_index(self):
        store = self.store()
        record = store.save(_frame(), task="DailyTask", message="找不到委托按钮")

        self.assertIsNotNone(record)
        self.assertEqual(record.task, "DailyTask")
        self.assertEqual(record.message, "找不到委托按钮")
        self.assertEqual((record.width, record.height), (64, 36))
        self.assertTrue(store.path_of(record).exists())
        self.assertTrue((self.root / INDEX_NAME).exists())

        records = store.list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, record.id)

    def test_list_is_newest_first(self):
        store = self.store()
        first = store.save(_frame(10), task="A", created_at=1000.0)
        second = store.save(_frame(20), task="B", created_at=2000.0)

        records = store.list()
        self.assertEqual([r.task for r in records], ["B", "A"])
        self.assertEqual(store.latest().id, second.id)
        self.assertEqual(store.get(first.id).task, "A")

    def test_load_image_roundtrip(self):
        store = self.store()
        record = store.save(_frame(200), task="T")
        image = store.load_image(record)

        self.assertIsNotNone(image)
        self.assertEqual(image.shape[:2], (36, 64))
        self.assertAlmostEqual(float(image[0, 0].mean()), 200, delta=2)

    def test_get_unknown_id_returns_none(self):
        store = self.store()
        store.save(_frame(), task="T")
        self.assertIsNone(store.get("does-not-exist"))

    def test_label_contains_time_task_and_size(self):
        store = self.store()
        record = store.save(_frame(), task="DailyTask", message="失败原因")
        label = record.label()
        self.assertIn("DailyTask", label)
        self.assertIn("64×36", label)
        self.assertIn("失败原因", label)


class TestInvalidInput(FailureFrameStoreTestBase):
    def test_none_and_empty_frames_are_ignored(self):
        store = self.store()
        self.assertIsNone(store.save(None, task="T"))
        self.assertIsNone(store.save(np.empty((0, 0, 3), dtype=np.uint8), task="T"))
        self.assertIsNone(store.save("not-a-frame", task="T"))
        self.assertEqual(store.list(), [])

    def test_missing_image_is_filtered_out(self):
        store = self.store()
        record = store.save(_frame(), task="T")
        store.path_of(record).unlink()
        self.assertEqual(store.list(), [])

    def test_corrupt_index_is_tolerated(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / INDEX_NAME).write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.store().list(), [])

    def test_legacy_list_payload_is_accepted(self):
        self.root.mkdir(parents=True, exist_ok=True)
        record = FailureFrameRecord(
            id="1", file="a.png", created_at=1.0, task="T", message="", width=1, height=1
        )
        (self.root / INDEX_NAME).write_text(json.dumps([record.to_dict()]), encoding="utf-8")
        (self.root / "a.png").write_bytes(b"x")
        records = self.store().list()
        self.assertEqual(len(records), 1)


class TestPruning(FailureFrameStoreTestBase):
    def test_old_records_are_pruned_with_files(self):
        store = self.store(limit=3)
        records = [store.save(_frame(i), task="T", created_at=float(i)) for i in range(5)]

        kept = store.list()
        self.assertEqual(len(kept), 3)
        # 最新的三帧留下
        self.assertEqual([r.created_at for r in kept], [4.0, 3.0, 2.0])
        self.assertTrue(store.path_of(records[4]).exists())
        self.assertFalse(store.path_of(records[0]).exists())
        self.assertFalse(store.path_of(records[1]).exists())

    def test_clear_removes_everything(self):
        store = self.store()
        for i in range(3):
            store.save(_frame(), task="T", created_at=float(i))

        removed = store.clear()
        self.assertEqual(removed, 3)
        self.assertEqual(store.list(), [])
        self.assertFalse((self.root / INDEX_NAME).exists())
        self.assertEqual(list(self.root.glob("*.png")), [])

    def test_total_size_counts_kept_frames(self):
        store = self.store(limit=2)
        for i in range(4):
            store.save(_frame(), task="T", created_at=float(i))
        self.assertGreater(store.total_size(), 0)
        self.assertEqual(len(store.list()), 2)


class TestMasking(unittest.TestCase):
    """失败帧落盘前必须过隐私遮挡（失败帧会长期留在磁盘上）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _processor(frame):
        out = frame.copy()
        out[0:8, 0:8] = 0
        return out

    def test_frame_is_masked_before_saving(self):
        store = FailureFrameStore(root=self.root, processor=self._processor)
        original = _frame(200)
        record = store.save(original, task="t")
        self.assertTrue(record.masked)
        self.assertIn("已遮挡", record.label())

        saved = store.load_image(record)
        self.assertEqual(int(saved[2, 2, 0]), 0)          # 遮挡区被涂掉
        self.assertEqual(int(saved[20, 40, 0]), 200)      # 其余区域不动
        self.assertEqual(int(original[2, 2, 0]), 200)     # 传进来的帧不被改

    def test_masked_flag_survives_index_roundtrip(self):
        FailureFrameStore(root=self.root, processor=self._processor).save(_frame(), task="t")
        self.assertTrue(FailureFrameStore(root=self.root).list()[0].masked)

    def test_without_processor_nothing_changes(self):
        store = FailureFrameStore(root=self.root)
        record = store.save(_frame(), task="t")
        self.assertFalse(record.masked)
        self.assertEqual(int(store.load_image(record)[2, 2, 0]), 128)

    def test_processor_failure_falls_back_to_unmasked(self):
        def boom(_frame):
            raise RuntimeError("遮罩坏了")

        logs = []
        store = FailureFrameStore(root=self.root, processor=boom, log=logs.append)
        record = store.save(_frame(), task="t")
        self.assertIsNotNone(record, "遮罩失败不该把失败帧丢掉")
        self.assertFalse(record.masked)
        self.assertTrue(any("遮挡失败" in log for log in logs))

    def test_processor_returning_none_falls_back(self):
        logs = []
        store = FailureFrameStore(root=self.root, processor=lambda _f: None, log=logs.append)
        record = store.save(_frame(), task="t")
        self.assertIsNotNone(record)
        self.assertFalse(record.masked)
        self.assertTrue(any("空结果" in log for log in logs))


if __name__ == "__main__":
    unittest.main()
