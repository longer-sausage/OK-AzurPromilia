"""素材包热更新测试：清单、逐文件校验、原子安装、备份、回滚。

核心断言是「坏包一个文件都不动」：任何校验失败都必须让磁盘保持原样。
"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.core.asset_pack import (
    MANIFEST_NAME,
    AssetPackError,
    build_manifest,
    build_pack,
    install_pack,
    read_manifest,
    sha256_file,
    verify_pack,
)

TEMPLATE_BYTES = b"\x89PNG\r\n\x1a\nfirst-version"
NEW_TEMPLATE_BYTES = b"\x89PNG\r\n\x1a\nsecond-version"


def _write(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _zip_files(zip_path: Path, files: dict[str, bytes]) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return zip_path


class AssetPackTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.source = self.base / "source"
        self.app = self.base / "app"

    def tearDown(self):
        self._tmp.cleanup()

    def make_pack(self, name: str = "pack.zip", files=None) -> Path:
        """用 source 目录里已有的素材打一个合法包，返回 zip 路径。"""
        if files is not None:
            for relative, data in files.items():
                _write(self.source, relative, data)
        target = self.base / name
        build_pack(target, self.source, name="测试素材包", version="1.0")
        return target


class TestManifest(AssetPackTestBase):
    def test_build_and_read_roundtrip(self):
        _write(self.source, "assets/images/1.png", TEMPLATE_BYTES)
        _write(self.source, "assets/coco_annotations.json", b"{}")

        manifest = build_manifest(self.source, name="包", version="2.0")
        self.assertEqual(len(manifest.files), 2)
        self.assertEqual({entry.path for entry in manifest.files},
                         {"assets/images/1.png", "assets/coco_annotations.json"})

        pack = self.make_pack()
        loaded = read_manifest(pack)
        self.assertEqual(loaded.name, "测试素材包")
        self.assertEqual(loaded.version, "1.0")
        self.assertEqual(set(loaded.paths()), {entry.path for entry in manifest.files})

    def test_manifest_records_real_sha256(self):
        target = _write(self.source, "assets/images/1.png", TEMPLATE_BYTES)
        manifest = build_manifest(self.source)
        self.assertEqual(manifest.files[0].sha256, sha256_file(target))

    def test_zip_without_manifest_is_rejected(self):
        pack = _zip_files(self.base / "broken.zip", {"assets/a.png": b"x"})
        with self.assertRaises(AssetPackError):
            read_manifest(pack)

    def test_non_zip_is_rejected(self):
        path = self.base / "not_a_zip.zip"
        path.write_text("hello", encoding="utf-8")
        with self.assertRaises(AssetPackError):
            read_manifest(path)

    def test_manifest_with_short_digest_is_rejected(self):
        pack = _zip_files(
            self.base / "short.zip",
            {MANIFEST_NAME: json.dumps({"files": {"assets/a.png": "abc"}}).encode()},
        )
        with self.assertRaises(AssetPackError):
            read_manifest(pack)

    def test_simplified_string_digest_is_accepted(self):
        _write(self.source, "assets/images/1.png", TEMPLATE_BYTES)
        digest = sha256_file(self.source / "assets/images/1.png")
        pack = _zip_files(
            self.base / "simple.zip",
            {
                MANIFEST_NAME: json.dumps({"files": {"assets/images/1.png": digest}}).encode(),
                "assets/images/1.png": TEMPLATE_BYTES,
            },
        )
        manifest = read_manifest(pack)
        self.assertEqual(manifest.files[0].sha256, digest)


class TestVerify(AssetPackTestBase):
    def test_valid_pack_passes(self):
        self.make_pack(files={"assets/images/1.png": TEMPLATE_BYTES})
        pack = self.base / "pack.zip"
        report = verify_pack(pack)
        self.assertTrue(report.ok)
        self.assertEqual(report.checked, 1)

    def test_tampered_payload_is_detected(self):
        self.make_pack(files={"assets/images/1.png": TEMPLATE_BYTES})
        good = self.base / "pack.zip"
        manifest = read_manifest(good)
        tampered = _zip_files(
            self.base / "tampered.zip",
            {
                MANIFEST_NAME: json.dumps(manifest.to_dict(), ensure_ascii=False).encode(),
                "assets/images/1.png": NEW_TEMPLATE_BYTES,
            },
        )
        report = verify_pack(tampered)
        self.assertFalse(report.ok)
        self.assertEqual(report.mismatched, ["assets/images/1.png"])

    def test_missing_payload_is_detected(self):
        self.make_pack(files={"assets/images/1.png": TEMPLATE_BYTES})
        manifest = read_manifest(self.base / "pack.zip")
        incomplete = _zip_files(
            self.base / "incomplete.zip",
            {MANIFEST_NAME: json.dumps(manifest.to_dict(), ensure_ascii=False).encode()},
        )
        report = verify_pack(incomplete)
        self.assertFalse(report.ok)
        self.assertEqual(report.missing, ["assets/images/1.png"])

    def test_path_traversal_is_rejected(self):
        pack = _zip_files(
            self.base / "slip.zip",
            {
                MANIFEST_NAME: json.dumps(
                    {"files": {"assets/../../evil.png": "0" * 64}}
                ).encode(),
                "assets/../../evil.png": b"x",
            },
        )
        with self.assertRaises(AssetPackError):
            read_manifest(pack)

    def test_disallowed_prefix_is_rejected(self):
        pack = _zip_files(
            self.base / "code.zip",
            {
                MANIFEST_NAME: json.dumps({"files": {"src/config.py": "0" * 64}}).encode(),
                "src/config.py": b"print('hacked')",
            },
        )
        report = verify_pack(pack)
        self.assertFalse(report.ok)
        self.assertEqual(report.unsupported, ["src/config.py"])


class TestInstall(AssetPackTestBase):
    def _existing_app(self) -> Path:
        _write(self.app, "assets/images/1.png", TEMPLATE_BYTES)
        return self.app

    def test_install_replaces_assets_and_backs_up(self):
        self._existing_app()
        pack = self.make_pack(files={"assets/images/1.png": NEW_TEMPLATE_BYTES})

        report = install_pack(pack, self.app)

        self.assertTrue(report.ok, report.message)
        self.assertEqual((self.app / "assets/images/1.png").read_bytes(), NEW_TEMPLATE_BYTES)
        self.assertEqual(report.installed, ["assets/images/1.png"])
        self.assertEqual(report.backed_up, ["assets/images/1.png"])
        self.assertIsNotNone(report.backup_dir)
        backup = report.backup_dir / "assets/images/1.png"
        self.assertEqual(backup.read_bytes(), TEMPLATE_BYTES)
        self.assertTrue((report.backup_dir / "install.json").exists())

    def test_install_adds_new_files_without_backup(self):
        self._existing_app()
        pack = self.make_pack(files={"assets/images/2.png": b"new"})

        report = install_pack(pack, self.app)

        self.assertTrue(report.ok, report.message)
        self.assertEqual((self.app / "assets/images/2.png").read_bytes(), b"new")
        self.assertEqual(report.backed_up, [])
        self.assertIsNone(report.backup_dir)

    def test_bad_pack_touches_nothing(self):
        self._existing_app()
        before = (self.app / "assets/images/1.png").read_bytes()

        self.make_pack(files={"assets/images/1.png": TEMPLATE_BYTES})
        manifest = read_manifest(self.base / "pack.zip")
        bad = _zip_files(
            self.base / "bad.zip",
            {
                MANIFEST_NAME: json.dumps(manifest.to_dict(), ensure_ascii=False).encode(),
                "assets/images/1.png": b"corrupted",
            },
        )

        report = install_pack(bad, self.app)

        self.assertFalse(report.ok)
        self.assertEqual(report.installed, [])
        self.assertEqual((self.app / "assets/images/1.png").read_bytes(), before)
        self.assertFalse((self.app / "backups").exists())

    def test_zip_slip_pack_touches_nothing(self):
        self._existing_app()
        pack = self.make_pack(files={"assets/images/1.png": NEW_TEMPLATE_BYTES})
        manifest = read_manifest(pack)
        payload = dict(manifest.to_dict())
        payload["files"]["assets/../escape.txt"] = {"sha256": "0" * 64, "size": 1}
        slip = _zip_files(
            self.base / "slip2.zip",
            {
                MANIFEST_NAME: json.dumps(payload, ensure_ascii=False).encode(),
                "assets/../escape.txt": b"evil",
            },
        )

        report = install_pack(slip, self.app)

        self.assertFalse(report.ok)
        self.assertFalse((self.app / "escape.txt").exists())
        self.assertFalse((self.base / "escape.txt").exists())

    def test_dry_run_changes_nothing(self):
        self._existing_app()
        pack = self.make_pack(files={"assets/images/1.png": NEW_TEMPLATE_BYTES})

        report = install_pack(pack, self.app, dry_run=True)

        self.assertTrue(report.ok, report.message)
        self.assertEqual(report.installed, ["assets/images/1.png"])
        self.assertEqual((self.app / "assets/images/1.png").read_bytes(), TEMPLATE_BYTES)
        self.assertFalse((self.app / "backups").exists())

    def test_rollback_restores_previous_files(self):
        self._existing_app()
        pack = self.make_pack(
            files={
                "assets/images/1.png": NEW_TEMPLATE_BYTES,
                "assets/images/2.png": b"second",
            }
        )

        real_replace = __import__("os").replace
        calls = {"count": 0}

        def flaky_replace(src, dst, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("模拟磁盘故障")
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch("src.core.asset_pack.os.replace", side_effect=flaky_replace):
            report = install_pack(pack, self.app)

        self.assertFalse(report.ok)
        self.assertIn("回滚", report.message)
        self.assertEqual((self.app / "assets/images/1.png").read_bytes(), TEMPLATE_BYTES)
        self.assertFalse((self.app / "assets/images/2.png").exists())

    def test_on_installed_callback_runs(self):
        self._existing_app()
        pack = self.make_pack(files={"assets/images/1.png": NEW_TEMPLATE_BYTES})
        seen = []

        report = install_pack(pack, self.app, on_installed=lambda r, m: seen.append((r.ok, m.version)))

        self.assertTrue(report.ok)
        self.assertEqual(seen, [(True, "1.0")])

    def test_download_pack_accepts_local_path(self):
        from src.core.asset_pack import download_pack

        pack = self.make_pack(files={"assets/images/1.png": TEMPLATE_BYTES})
        self.assertEqual(download_pack(str(pack), self.base).resolve(), pack.resolve())

    def test_download_pack_rejects_unknown_scheme(self):
        from src.core.asset_pack import download_pack

        with self.assertRaises(AssetPackError):
            download_pack("ftp://example.com/pack.zip", self.base)


if __name__ == "__main__":
    unittest.main()
