"""隐私遮挡、坐标拾取、语言包、诊断包与崩溃守卫的测试。

这些模块都不碰游戏、不截图真实画面：遮挡用合成帧、坐标用等比缩放、
语言包与诊断包用临时目录、崩溃守卫用合成异常。
"""

import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import numpy as np

from src.core.coord_picker import (
    client_to_reference,
    color_hex,
    format_snippet,
    pick,
    reference_to_client,
)
from src.core.crash_guard import CrashGuard
from src.core.diagnostics_bundle import MANIFEST_NAME, SYSTEM_NAME, build_bundle
from src.core.lang_pack import (
    PACK_PREFIX,
    apply_pack,
    list_backups,
    restore_backup,
    scan_packs,
)
from src.core.privacy import (
    MAX_BLOCKS,
    PrivacyBlock,
    PrivacyConfig,
    PrivacyConfigError,
    apply_privacy_masks,
    blocks_as_reference,
    default_config,
    format_color,
    load_config,
    normalize_blocks,
    parse_color,
    save_config,
)


def make_frame(width=1920, height=1080, color=200):
    return np.full((height, width, 3), color, dtype=np.uint8)


class TestPrivacyColors(unittest.TestCase):
    def test_parse_rgb_string(self):
        self.assertEqual(parse_color("#FF0000"), (0, 0, 255))
        self.assertEqual(parse_color("00FF00"), (0, 255, 0))
        self.assertEqual(parse_color("#0000ff"), (255, 0, 0))

    def test_parse_invalid_falls_back(self):
        self.assertEqual(parse_color("不是颜色", "#FFFFFF"), (255, 255, 255))
        self.assertEqual(parse_color(None), (0, 0, 0))

    def test_format_color(self):
        self.assertEqual(format_color((0, 0, 255)), "#FF0000")
        self.assertEqual(format_color((255, 0, 0)), "#0000FF")


class TestPrivacyBlock(unittest.TestCase):
    def test_scale_to_pixels(self):
        block = PrivacyBlock(x=0, y=0, width=96, height=54)
        self.assertEqual(block.to_pixels(1920, 1080), (0, 0, 96, 54))
        self.assertEqual(block.to_pixels(3840, 2160), (0, 0, 192, 108))

    def test_clamped_to_frame(self):
        block = PrivacyBlock(x=1900, y=1070, width=100, height=100)
        rect = block.to_pixels(1920, 1080)
        self.assertEqual(rect, (1900, 1070, 1920, 1080))

    def test_fully_outside_returns_empty(self):
        block = PrivacyBlock(x=5000, y=5000, width=10, height=10)
        self.assertEqual(block.to_pixels(1920, 1080), (0, 0, 0, 0))

    def test_invalid_size(self):
        with self.assertRaises(PrivacyConfigError):
            PrivacyBlock(x=0, y=0, width=0, height=10)

    def test_invalid_mode(self):
        with self.assertRaises(PrivacyConfigError):
            PrivacyBlock(x=0, y=0, width=10, height=10, mode="blur")

    def test_roundtrip(self):
        block = PrivacyBlock(x=1, y=2, width=3, height=4, color="#123456", mode="mosaic", label="昵称")
        self.assertEqual(PrivacyBlock.from_dict(block.to_dict()), block)


class TestPrivacyConfig(unittest.TestCase):
    def test_default_has_uid_block(self):
        config = default_config()
        self.assertTrue(config.enabled)
        self.assertEqual(config.count, 1)
        self.assertEqual(config.blocks[0].label, "UID")

    def test_max_blocks(self):
        config = PrivacyConfig(blocks=())
        for index in range(MAX_BLOCKS):
            config = config.with_block(PrivacyBlock(x=index * 10, y=0, width=10, height=10))
        self.assertEqual(config.count, MAX_BLOCKS)
        with self.assertRaises(PrivacyConfigError):
            config.with_block(PrivacyBlock(x=0, y=0, width=10, height=10))

    def test_too_many_in_dict(self):
        data = {"blocks": [{"x": 0, "y": 0, "width": 10, "height": 10}] * (MAX_BLOCKS + 1)}
        with self.assertRaises(PrivacyConfigError):
            PrivacyConfig.from_dict(data)

    def test_remove_and_replace(self):
        config = default_config()
        self.assertEqual(config.without_block(0).count, 0)
        replaced = config.replace_block(0, PrivacyBlock(x=10, y=10, width=20, height=20))
        self.assertEqual(replaced.blocks[0].x, 10)
        with self.assertRaises(PrivacyConfigError):
            config.without_block(5)

    def test_roundtrip_dict(self):
        config = default_config()
        self.assertEqual(PrivacyConfig.from_dict(config.to_dict()).to_dict(), config.to_dict())

    def test_blocks_as_reference(self):
        rows = blocks_as_reference(default_config())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "UID")

    def test_normalize_clamps(self):
        blocks = normalize_blocks([PrivacyBlock(x=-10, y=-10, width=50, height=50)])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].x, 0)


class TestPrivacyMasking(unittest.TestCase):
    def test_solid_block_is_painted(self):
        frame = make_frame()
        config = PrivacyConfig(blocks=(PrivacyBlock(x=0, y=0, width=100, height=100, color="#FF0000"),))
        apply_privacy_masks(frame, config)
        self.assertEqual(tuple(frame[10, 10]), (0, 0, 255))
        self.assertEqual(tuple(frame[900, 900]), (200, 200, 200))

    def test_mosaic_changes_region_but_keeps_pattern(self):
        frame = np.zeros((108, 192, 3), dtype=np.uint8)
        frame[:, ::12] = 255  # 竖条纹，马赛克后应变成块状
        config = PrivacyConfig(blocks=(PrivacyBlock(x=0, y=0, width=96, height=108, mode="mosaic"),))
        apply_privacy_masks(frame, config)
        self.assertFalse(np.array_equal(frame[:, :96], frame[:, 96:]))

    def test_disabled_skips_when_not_forced(self):
        frame = make_frame()
        config = PrivacyConfig(enabled=False, blocks=(PrivacyBlock(x=0, y=0, width=100, height=100),))
        apply_privacy_masks(frame, config, force=False)
        self.assertEqual(tuple(frame[10, 10]), (200, 200, 200))

    def test_invalid_frame_is_returned(self):
        self.assertIsNone(apply_privacy_masks(None, default_config()))
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        self.assertIs(apply_privacy_masks(empty, default_config()), empty)

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configs" / "privacy.json"
            config = PrivacyConfig.from_dict(
                {
                    "enabled": True,
                    "only_foreground": False,
                    "blocks": [
                        {"x": 10, "y": 20, "width": 30, "height": 40, "color": "#00FF00", "label": "昵称"},
                        {"x": 100, "y": 1050, "width": 80, "height": 20, "mode": "mosaic"},
                    ],
                }
            )
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.count, 2)
            self.assertFalse(loaded.only_foreground)
            self.assertEqual(loaded.blocks[0].label, "昵称")
            self.assertEqual(loaded.blocks[1].mode, "mosaic")

    def test_load_missing_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = load_config(Path(tmp) / "none.json")
            self.assertEqual(loaded.count, 1)
            self.assertTrue(loaded.enabled)

    def test_load_corrupt_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "privacy.json"
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(load_config(path).count, 1)

    def test_screenshot_processor_applies_config(self):
        from src.config import make_privacy_processor

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "privacy.json"
            save_config(
                PrivacyConfig(blocks=(PrivacyBlock(x=0, y=0, width=100, height=100, color="#FFFFFF"),)),
                path,
            )
            from unittest import mock

            with mock.patch("src.core.privacy.PRIVACY_FILE", path):
                frame = make_privacy_processor()(make_frame())
            self.assertEqual(tuple(frame[5, 5]), (255, 255, 255))


class TestCoordPicker(unittest.TestCase):
    def test_identity_at_reference_size(self):
        self.assertEqual(client_to_reference(960, 540, 1920, 1080), (960.0, 540.0))
        self.assertEqual(reference_to_client(960, 540, 1920, 1080), (960, 540))

    def test_scales_up(self):
        self.assertEqual(client_to_reference(1280, 720, 2560, 1440), (960.0, 540.0))
        self.assertEqual(reference_to_client(960, 540, 2560, 1440), (1280, 720))

    def test_zero_size_safe(self):
        self.assertEqual(client_to_reference(10, 10, 0, 0), (0.0, 0.0))
        self.assertEqual(reference_to_client(10, 10, 0, 0), (0, 0))

    def test_snippet(self):
        self.assertEqual(format_snippet(960.4, 540.6), '"x": 960, "y": 541')

    def test_color_hex(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        frame[5, 5] = (0, 0, 255)
        self.assertEqual(color_hex(frame, 5, 5), "#FF0000")

    def test_color_out_of_bounds(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        self.assertEqual(len(color_hex(frame, 100, 100)), 7)
        self.assertEqual(color_hex(None, 0, 0), "#000000")

    def test_pick_result(self):
        frame = make_frame(2560, 1440, color=10)
        result = pick(frame, 1280, 720)
        self.assertEqual((result.client_x, result.client_y), (1280, 720))
        self.assertEqual((result.ref_x, result.ref_y), (960.0, 540.0))
        self.assertIn('"x": 960', result.snippet)
        self.assertIn("客户区", result.summary())


class TestLangPack(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.app = self.root / "app"
        (self.app / "i18n" / "zh_CN" / "LC_MESSAGES").mkdir(parents=True)
        (self.app / "i18n" / "zh_CN" / "LC_MESSAGES" / "ok.po").write_text("原始", encoding="utf-8")
        (self.app / "assets" / "lang").mkdir(parents=True)
        (self.app / "assets" / "lang" / "home.json").write_text("{}", encoding="utf-8")
        self.backups = self.root / "backups"

    def tearDown(self):
        self._tmp.cleanup()

    def _make_pack(self, name="语言包EN", files=None):
        pack = self.root / name
        (pack / "i18n" / "en_US" / "LC_MESSAGES").mkdir(parents=True)
        (pack / "i18n" / "en_US" / "LC_MESSAGES" / "ok.po").write_text("英文", encoding="utf-8")
        (pack / "assets" / "lang").mkdir(parents=True)
        (pack / "assets" / "lang" / "home.json").write_text('{"k":1}', encoding="utf-8")
        return pack

    def test_scan(self):
        self._make_pack()
        (self.root / "别的目录").mkdir()
        packs = scan_packs(self.root)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0].code, "EN")
        self.assertEqual(len(packs[0].files), 2)
        self.assertIn(PACK_PREFIX, packs[0].name)

    def test_apply_and_backup(self):
        pack = self._make_pack()
        result = apply_pack(pack, self.app, self.backups)
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(len(result.applied), 2)
        self.assertEqual((self.app / "i18n" / "en_US" / "LC_MESSAGES" / "ok.po").read_text(encoding="utf-8"), "英文")
        self.assertIsNotNone(result.backup_dir)
        self.assertTrue((result.backup_dir / "i18n" / "zh_CN" / "LC_MESSAGES" / "ok.po").exists())
        self.assertIn("已应用语言包", result.summary())

    def test_apply_rejects_disallowed_path(self):
        pack = self.root / "语言包坏"
        (pack / "src").mkdir(parents=True)
        (pack / "src" / "config.py").write_text("print(1)", encoding="utf-8")
        result = apply_pack(pack, self.app, self.backups)
        self.assertFalse(result.ok)
        self.assertIn("不允许的路径", result.message)

    def test_apply_rejects_wrong_suffix(self):
        pack = self.root / "语言包exe"
        target = pack / "assets" / "lang"
        target.mkdir(parents=True)
        (target / "payload.exe").write_bytes(b"MZ")
        result = apply_pack(pack, self.app, self.backups)
        self.assertFalse(result.ok)
        self.assertIn("不允许的文件类型", result.message)

    def test_apply_rejects_oversized_file(self):
        pack = self.root / "语言包大"
        target = pack / "assets" / "lang"
        target.mkdir(parents=True)
        (target / "big.json").write_text("x" * 2048, encoding="utf-8")
        result = apply_pack(pack, self.app, self.backups, max_file_bytes=1024)
        self.assertFalse(result.ok)
        self.assertIn("文件太大", result.message)

    def test_apply_rejects_oversized_total(self):
        pack = self.root / "语言包总计"
        target = pack / "assets" / "lang"
        target.mkdir(parents=True)
        for index in range(3):
            (target / f"f{index}.json").write_text("y" * 600, encoding="utf-8")
        result = apply_pack(pack, self.app, self.backups, max_total_bytes=1000)
        self.assertFalse(result.ok)
        self.assertIn("总体积", result.message)

    def test_apply_missing_dir(self):
        result = apply_pack(self.root / "没有", self.app, self.backups)
        self.assertFalse(result.ok)

    def test_apply_empty_pack(self):
        pack = self.root / "语言包空"
        pack.mkdir()
        self.assertFalse(apply_pack(pack, self.app, self.backups).ok)

    def test_restore(self):
        pack = self._make_pack()
        result = apply_pack(pack, self.app, self.backups)
        restore = restore_backup(result.backup_dir, self.app, self.backups)
        self.assertTrue(restore.ok, restore.summary())
        original = self.app / "i18n" / "zh_CN" / "LC_MESSAGES" / "ok.po"
        self.assertEqual(original.read_text(encoding="utf-8"), "原始")

    def test_list_backups(self):
        pack = self._make_pack()
        apply_pack(pack, self.app, self.backups)
        apply_pack(pack, self.app, self.backups)
        self.assertGreaterEqual(len(list_backups(self.backups)), 1)


class TestDiagnosticsBundle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "app"
        (self.root / "configs").mkdir(parents=True)
        (self.root / "configs" / "DailyTask.json").write_text('{"任务顺序": "a,b"}', encoding="utf-8")
        (self.root / "logs").mkdir()
        (self.root / "logs" / "ok-script.log").write_text("line\n" * 100, encoding="utf-8")
        (self.root / "assets").mkdir()
        (self.root / "assets" / "coco_annotations.json").write_text(
            json.dumps({"categories": [{"name": "a"}, {"name": "b"}]}), encoding="utf-8"
        )
        (self.root / "failure_frames").mkdir()
        (self.root / "failure_frames" / "index.json").write_text(
            json.dumps({"frames": [{"id": "1"}]}), encoding="utf-8"
        )
        (self.root / "failure_frames" / "20261124_010101_001_T.png").write_bytes(b"pngdata")

    def tearDown(self):
        self._tmp.cleanup()

    def test_bundle_contents(self):
        result = build_bundle(dest_dir=self.root / "out", app_root=self.root)
        self.assertTrue(result.ok, result.summary())
        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())
            self.assertIn(MANIFEST_NAME, names)
            self.assertIn(SYSTEM_NAME, names)
            self.assertIn("configs/DailyTask.json", names)
            self.assertIn("logs/ok-script.log", names)
            self.assertIn("templates.json", names)
            self.assertIn("failure_frames/index.json", names)
            self.assertIn("failure_frames/20261124_010101_001_T.png", names)
            system = json.loads(archive.read(SYSTEM_NAME))
            self.assertIn("platform", system)
            templates = json.loads(archive.read("templates.json"))
            self.assertEqual(templates["count"], 2)
            manifest = json.loads(archive.read(MANIFEST_NAME))
            self.assertIn("configs/DailyTask.json", manifest["files"])

    def test_screenshots_excluded_by_default(self):
        (self.root / "screenshots").mkdir()
        (self.root / "screenshots" / "a.png").write_bytes(b"x")
        result = build_bundle(dest_dir=self.root / "out", app_root=self.root)
        with zipfile.ZipFile(result.path) as archive:
            self.assertFalse(any(name.startswith("screenshots/") for name in archive.namelist()))

    def test_screenshots_included_on_request(self):
        (self.root / "screenshots").mkdir()
        (self.root / "screenshots" / "a.png").write_bytes(b"x")
        result = build_bundle(dest_dir=self.root / "out", app_root=self.root, include_screenshots=True)
        with zipfile.ZipFile(result.path) as archive:
            self.assertTrue(any(name.startswith("screenshots/") for name in archive.namelist()))

    def test_missing_everything_still_builds(self):
        with tempfile.TemporaryDirectory() as empty:
            result = build_bundle(dest_dir=Path(empty) / "out", app_root=Path(empty))
            self.assertTrue(result.ok, result.summary())

    def test_summary_text(self):
        result = build_bundle(dest_dir=self.root / "out", app_root=self.root)
        self.assertIn("诊断包已生成", result.summary())


class TestCrashGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_install_is_idempotent(self):
        guard = CrashGuard(app_root=self.root, make_bundle=False)
        self.addCleanup(guard.close)
        self.assertTrue(guard.install())
        self.assertTrue(guard.install())
        guard.close()
        self.assertFalse(guard.installed)

    def test_record_writes_traceback(self):
        guard = CrashGuard(app_root=self.root, make_bundle=False)
        try:
            raise ValueError("炸了")
        except ValueError as exc:
            record = guard.record(ValueError, exc, exc.__traceback__, thread_name="TestThread")

        self.assertTrue(record.ok, record.error)
        text = record.path.read_text(encoding="utf-8")
        self.assertIn("ValueError", text)
        self.assertIn("炸了", text)
        self.assertIn("TestThread", text)
        self.assertIn("crash_", record.path.name)

    def test_record_with_text_only(self):
        guard = CrashGuard(app_root=self.root, make_bundle=False)
        record = guard.record(text="手工记录")
        self.assertTrue(record.ok)
        self.assertIn("手工记录", record.path.read_text(encoding="utf-8"))

    def test_record_builds_bundle(self):
        guard = CrashGuard(app_root=self.root, make_bundle=True)
        record = guard.record(text="崩溃")
        self.assertTrue(record.ok)
        self.assertIsNotNone(record.bundle)
        self.assertTrue(record.bundle.exists())

    def test_thread_hook_records(self):
        guard = CrashGuard(app_root=self.root, make_bundle=False)
        captured = {}

        def boom():
            try:
                raise RuntimeError("线程炸了")
            except RuntimeError as exc:
                captured["type"] = RuntimeError
                captured["exc"] = exc
                captured["tb"] = exc.__traceback__

        thread = threading.Thread(target=boom, name="worker-1")
        thread.start()
        thread.join()

        record = guard.record(captured["type"], captured["exc"], captured["tb"], thread_name="worker-1")
        self.assertIn("worker-1", record.path.read_text(encoding="utf-8"))

    def test_two_records_do_not_collide(self):
        guard = CrashGuard(app_root=self.root, make_bundle=False)
        first = guard.record(text="一")
        second = guard.record(text="二")
        self.assertNotEqual(first.path, second.path)

    def test_notify_callback(self):
        messages = []
        guard = CrashGuard(app_root=self.root, make_bundle=False, notify=messages.append)
        guard._sys_hook(ValueError, ValueError("x"), None)
        self.assertTrue(messages)
        self.assertIn("ValueError", messages[0])


if __name__ == "__main__":
    unittest.main()
