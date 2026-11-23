import tempfile
import unittest
from pathlib import Path

from src.patches.log_zip_dedup import (
    build_dedup_info,
    collect_image_duplicates,
    md5_hex,
    read_dedup_info,
    restore_duplicates,
)


class TestLogZipDedup(unittest.TestCase):

    def test_md5_hex_deterministic(self):
        h1 = md5_hex(b"hello world")
        h2 = md5_hex(b"hello world")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 32)

    def test_md5_hex_different_content(self):
        self.assertNotEqual(md5_hex(b"aaa"), md5_hex(b"bbb"))

    def test_collect_image_duplicates_unique(self):
        entries = [("a.png", b"data_a"), ("b.png", b"data_b")]
        unique, dups = collect_image_duplicates(entries)
        self.assertEqual(len(unique), 2)
        self.assertEqual(len(dups), 0)

    def test_collect_image_duplicates_with_dups(self):
        entries = [("a.png", b"same"), ("b.png", b"same"), ("c.png", b"diff")]
        unique, dups = collect_image_duplicates(entries)
        self.assertEqual(len(unique), 2)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["kept"], "a.png")
        self.assertEqual(dups[0]["duplicate"], "b.png")

    def test_build_dedup_info_structure(self):
        info = build_dedup_info([{"hash": "abc", "kept": "a", "duplicate": "b"}], note="test")
        self.assertEqual(info["format"], 1)
        self.assertEqual(info["note"], "test")
        self.assertEqual(len(info["duplicates"]), 1)

    def test_restore_duplicates_from_zip(self):
        import zipfile

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("img1.png", b"content1")
                zf.writestr("img2.png", b"content1")
                zf.writestr("img3.png", b"content2")
                info = build_dedup_info([{"hash": "h", "kept": "img1.png", "duplicate": "img2.png"}])
                zf.writestr("screenshots_dedup_info.json", __import__("json").dumps(info))

            out_dir = Path(tmpdir) / "output"
            restored = restore_duplicates(str(zip_path), str(out_dir))
            self.assertEqual(restored, ["img2.png"])
            self.assertEqual((out_dir / "img2.png").read_bytes(), b"content1")

    def test_read_dedup_info_missing(self):
        import zipfile

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "empty.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("dummy.txt", b"data")
            self.assertIsNone(read_dedup_info(str(zip_path)))


if __name__ == "__main__":
    unittest.main()
