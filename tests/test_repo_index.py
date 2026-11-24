"""在线仓库索引与进度估算测试。

索引全部用本地文件模拟「http 下载」之外的所有环节：解析、筛选、同名不覆盖、
SHA-256 校验、四类条目的落盘位置，以及模板包走 asset_pack 的安装链路。
"""

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.core.ledger import RunLog
from src.core.repo_index import (
    INDEX_KINDS,
    RepoClient,
    RepoEntry,
    RepoError,
    RepoIndex,
    load_index,
)


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestRepoEntry(unittest.TestCase):
    def test_valid_entry(self):
        entry = RepoEntry(kind="flow", name="深渊", url="https://x/y.json")
        self.assertIn("flow", entry.label())
        self.assertEqual(RepoEntry.from_dict(entry.to_dict()), entry)

    def test_unknown_kind(self):
        with self.assertRaises(RepoError):
            RepoEntry(kind="exe", name="x", url="u")

    def test_missing_name_or_url(self):
        with self.assertRaises(RepoError):
            RepoEntry(kind="flow", name="", url="u")
        with self.assertRaises(RepoError):
            RepoEntry(kind="flow", name="n", url="")

    def test_bad_sha_length(self):
        with self.assertRaises(RepoError):
            RepoEntry(kind="flow", name="n", url="u", sha256="abc")

    def test_tags_type(self):
        with self.assertRaises(RepoError):
            RepoEntry.from_dict({"kind": "flow", "name": "n", "url": "u", "tags": "x"})


class TestRepoIndex(unittest.TestCase):
    def _index(self):
        return RepoIndex.from_dict(
            {
                "name": "社区",
                "description": "分享",
                "entries": [
                    {"kind": "flow", "name": "深渊速刷", "url": "a.json", "tags": ["刷图"], "description": "两分钟"},
                    {"kind": "queue", "name": "早上", "url": "b.json"},
                    {"kind": "template", "name": "2K素材", "url": "c.zip"},
                ],
            }
        )

    def test_parse(self):
        index = self._index()
        self.assertEqual(index.name, "社区")
        self.assertEqual(len(index.entries), 3)
        self.assertEqual(index.kinds()["flow"], 1)
        self.assertEqual(sum(index.kinds().values()), 3)
        for kind in INDEX_KINDS:
            self.assertIn(kind, index.kinds())

    def test_missing_entries(self):
        with self.assertRaises(RepoError):
            RepoIndex.from_dict({"name": "x"})

    def test_filter_by_kind(self):
        index = self._index()
        self.assertEqual([entry.name for entry in index.filter("flow")], ["深渊速刷"])
        self.assertEqual(len(index.filter()), 3)

    def test_filter_by_keyword(self):
        index = self._index()
        self.assertEqual([entry.name for entry in index.filter(keyword="两分钟")], ["深渊速刷"])
        self.assertEqual([entry.name for entry in index.filter(keyword="早上")], ["早上"])

    def test_load_local_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp) / "index.json", {"entries": [{"kind": "flow", "name": "a", "url": "u"}]})
            index = load_index(str(path))
            self.assertEqual(index.name, "")
            self.assertEqual(len(index.entries), 1)

    def test_load_missing(self):
        with self.assertRaises(RepoError):
            load_index("no-such-file.json")

    def test_load_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            path.write_text("{ nope", encoding="utf-8")
            with self.assertRaises(RepoError):
                load_index(str(path))


class TestRepoInstall(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.app = self.root / "app"
        self.app.mkdir()
        self.source = self.root / "remote"
        self.source.mkdir()
        self.client = RepoClient(app_root=self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def _flow_file(self, name="深渊速刷", payload=None):
        payload = payload if payload is not None else {"name": name, "steps": [{"type": "wait", "seconds": 0}]}
        return write_json(self.source / f"{name}.json", payload)

    def test_install_flow(self):
        path = self._flow_file()
        entry = RepoEntry(kind="flow", name="深渊速刷", url=str(path), sha256=sha256_of(path))
        outcome = self.client.install(entry)
        self.assertTrue(outcome.ok, outcome.message)
        target = self.app / "configs" / "flows" / "深渊速刷.json"
        self.assertTrue(target.exists())
        self.assertIn("已安装", outcome.summary())

    def test_install_queue_and_scheme(self):
        queue_file = write_json(self.source / "q.json", {"name": "早上", "items": []})
        scheme_file = write_json(self.source / "s.json", {"name": "轮椅", "segments": [{"steps": []}]})
        for kind, name, path in (("queue", "早上", queue_file), ("scheme", "轮椅", scheme_file)):
            with self.subTest(kind=kind):
                outcome = self.client.install(RepoEntry(kind=kind, name=name, url=str(path)))
                self.assertTrue(outcome.ok, outcome.message)
                self.assertTrue((self.app / "configs" / f"{kind}s" / f"{name}.json").exists())

    def test_same_name_is_not_overwritten(self):
        path = self._flow_file()
        entry = RepoEntry(kind="flow", name="深渊速刷", url=str(path))
        self.assertTrue(self.client.install(entry).ok)
        second = self.client.install(entry)
        self.assertFalse(second.ok)
        self.assertIn("同名已存在", second.message)

    def test_allow_overwrite(self):
        path = self._flow_file()
        entry = RepoEntry(kind="flow", name="深渊速刷", url=str(path))
        self.assertTrue(self.client.install(entry).ok)
        client = RepoClient(app_root=self.app, allow_overwrite=True)
        self.assertTrue(client.install(entry).ok)

    def test_sha_mismatch_blocks_install(self):
        path = self._flow_file()
        entry = RepoEntry(kind="flow", name="深渊速刷", url=str(path), sha256="0" * 64)
        outcome = self.client.install(entry)
        self.assertFalse(outcome.ok)
        self.assertIn("SHA-256", outcome.message)
        self.assertFalse((self.app / "configs" / "flows" / "深渊速刷.json").exists())

    def test_invalid_json_content(self):
        path = self.source / "bad.json"
        path.write_text("not json", encoding="utf-8")
        outcome = self.client.install(RepoEntry(kind="flow", name="坏的", url=str(path)))
        self.assertFalse(outcome.ok)
        self.assertIn("不是合法 JSON", outcome.message)

    def test_missing_source(self):
        outcome = self.client.install(RepoEntry(kind="flow", name="无", url=str(self.source / "nope.json")))
        self.assertFalse(outcome.ok)
        self.assertIn("不存在", outcome.message)

    def test_install_template_pack(self):
        # 造一个合法素材包：assets/images/1.png + 清单
        payload_dir = self.source / "pack" / "assets" / "images"
        payload_dir.mkdir(parents=True)
        image = payload_dir / "1.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        digest = sha256_of(image)
        manifest = {"name": "素材", "version": "1", "files": {"assets/images/1.png": {"sha256": digest,
                                                                                   "size": image.stat().st_size}}}
        pack_path = self.source / "pack.zip"
        with zipfile.ZipFile(pack_path, "w") as archive:
            archive.writestr("asset_pack.json", json.dumps(manifest, ensure_ascii=False))
            archive.write(image, "assets/images/1.png")

        outcome = self.client.install(RepoEntry(kind="template", name="素材", url=str(pack_path)))
        self.assertTrue(outcome.ok, outcome.message)
        self.assertTrue((self.app / "assets" / "images" / "1.png").exists())


class TestProgressRatio(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = RunLog(Path(self._tmp.name) / "runs.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_history_is_zero(self):
        self.assertEqual(self.log.progress_ratio("A", 10.0), 0.0)

    def test_fallback_used(self):
        self.assertAlmostEqual(self.log.progress_ratio("A", 30.0, fallback_seconds=60.0), 0.5)

    def test_ratio_from_average(self):
        self.log.record("A", True, 100.0)
        self.assertAlmostEqual(self.log.progress_ratio("A", 25.0), 0.25)

    def test_capped(self):
        self.log.record("A", True, 100.0)
        self.assertAlmostEqual(self.log.progress_ratio("A", 1000.0), 0.97)
        self.assertAlmostEqual(self.log.progress_ratio("A", 1000.0, cap=0.5), 0.5)

    def test_cap_cannot_reach_one(self):
        self.log.record("A", True, 10.0)
        self.assertLess(self.log.progress_ratio("A", 999.0, cap=1.0), 1.0)

    def test_negative_elapsed_clamped(self):
        self.log.record("A", True, 100.0)
        self.assertEqual(self.log.progress_ratio("A", -5.0), 0.0)


class TestRepoPathSafety(unittest.TestCase):
    """在线仓库的条目名字是别人给的：不能借名字跳出配置目录，也不能把安装搞崩。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.app = self.root / "app"
        self.app.mkdir()
        self.payload = write_json(
            self.root / "src" / "p.json", {"name": "x", "steps": [{"type": "wait", "seconds": 1}]}
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_traversal_is_neutralized(self):
        outcome = RepoClient(app_root=self.app).install(
            RepoEntry(kind="flow", name="../../evil", url=str(self.payload))
        )
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(outcome.path.parent, self.app / "configs" / "flows")
        self.assertEqual(outcome.path.name, "evil.json")
        # 除了下载缓存，没有任何文件写到 configs/flows 之外
        written = {
            path.relative_to(self.app).as_posix()
            for path in self.app.rglob("*.json")
            if "downloads" not in path.as_posix()
        }
        self.assertEqual(written, {"configs/flows/evil.json"})

    def test_separators_become_underscores(self):
        outcome = RepoClient(app_root=self.app).install(
            RepoEntry(kind="flow", name="a/b", url=str(self.payload))
        )
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(outcome.path.name, "a_b.json")

    def test_empty_name_fails_gracefully(self):
        for name in ("..", "   ", "."):
            with self.subTest(name=name):
                outcome = RepoClient(app_root=self.app).install(
                    RepoEntry(kind="flow", name=name, url=str(self.payload))
                )
                self.assertFalse(outcome.ok)
                self.assertIn("名字不能为空", outcome.message)


if __name__ == "__main__":
    unittest.main()
