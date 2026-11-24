"""脱敏测试：诊断包里的 token / webhook / 账号名必须被抹掉。

真实场景：用户把诊断包发到群里，里面却带着自己的 Discord Webhook 与 Telegram Bot Token
（框架的 Notification 配置），日志里 DEBUG 级别还会把整份配置打出来。
"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.core.diagnostics_bundle import build_bundle
from src.core.redaction import (
    PLACEHOLDER,
    RedactionReport,
    is_sensitive_key,
    redact_config,
    redact_config_with_report,
    redact_text,
)


class TestSensitiveKeys(unittest.TestCase):
    def test_known_secrets(self):
        for key in (
            "Discord Webhook",
            "Telegram Bot Token",
            "QQ Bot API Token",
            "Telegram Chat ID",
            "User Password",
            "api_key",
            "api-key",
            "Cookie",
            "账号列表",
            "密码",
            "QQ Desktop Nickname",
        ):
            with self.subTest(key=key):
                self.assertTrue(is_sensitive_key(key))

    def test_normal_keys_untouched(self):
        for key in ("单步超时(秒)", "System Notification", "战 斗 次 数", "低配模式", ""):
            with self.subTest(key=key):
                self.assertFalse(is_sensitive_key(key))


class TestRedactConfig(unittest.TestCase):
    def test_nested_and_lists(self):
        data = {
            "Discord Webhook": "https://discord.com/api/webhooks/1/abc",
            "账号列表": ["user1", "user2"],
            "nested": {"password": "pw", "keep": 1},
            "普通": "值",
        }
        redacted, report = redact_config_with_report(data)
        self.assertEqual(redacted["Discord Webhook"], PLACEHOLDER)
        self.assertEqual(redacted["账号列表"], [PLACEHOLDER, PLACEHOLDER])  # 长度保留
        self.assertEqual(redacted["nested"]["password"], PLACEHOLDER)
        self.assertEqual(redacted["nested"]["keep"], 1)
        self.assertEqual(redacted["普通"], "值")
        self.assertGreaterEqual(report.count, 3)
        self.assertTrue(any("nested.password" in key for key in report.keys))

    def test_does_not_mutate_original(self):
        data = {"Telegram Bot Token": "abc"}
        redact_config(data)
        self.assertEqual(data["Telegram Bot Token"], "abc")

    def test_report_counts_and_dict(self):
        report = RedactionReport()
        redact_config({"密码": "x", "Telegram Bot Token": "y"}, report=report)
        self.assertEqual(report.count, 2)
        payload = report.to_dict()
        self.assertEqual(payload["count"], 2)
        self.assertIn("脱敏", report.summary())

    def test_placeholders_keep_booleans_readable(self):
        redacted = redact_config({"System Notification": True, "Telegram Bot Token": ""})
        self.assertTrue(redacted["System Notification"])
        self.assertEqual(redacted["Telegram Bot Token"], "")


class TestRedactText(unittest.TestCase):
    def test_json_style_line(self):
        line = "INFO Config:init self.config = {'Discord Webhook': 'https://x/y', '正常键': 'y'}"
        out, changed = redact_text(line)
        self.assertNotIn("https://x/y", out)
        self.assertIn("'正常键': 'y'", out)
        self.assertEqual(changed, 1)

    def test_plain_style_line(self):
        out, changed = redact_text("INFO webhook = https://hooks.example.com/secret")
        self.assertNotIn("hooks.example.com", out)
        self.assertEqual(changed, 1)

    def test_unrelated_lines_untouched(self):
        text = "INFO 一切正常\nWARN 识别超时"
        out, changed = redact_text(text)
        self.assertEqual(out, text)
        self.assertEqual(changed, 0)

    def test_multiline_counts(self):
        text = "token=abc123456\n正常\npassword: hunter2"
        out, changed = redact_text(text)
        self.assertEqual(changed, 2)
        self.assertNotIn("hunter2", out)


class TestBundleRedaction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "app"
        (self.root / "configs").mkdir(parents=True)
        (self.root / "logs").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _build(self):
        result = build_bundle(dest_dir=Path(self._tmp.name) / "out", app_root=self.root)
        self.assertTrue(result.ok, result.summary())
        return zipfile.ZipFile(result.path)

    def test_config_and_logs_are_redacted(self):
        (self.root / "configs" / "Notification.json").write_text(
            json.dumps(
                {
                    "Discord Webhook": "https://discord.com/api/webhooks/1/secret",
                    "Telegram Bot Token": "123:AA",
                    "System Notification": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.root / "logs" / "ok-script.log").write_text(
            "INFO Config:init self.config = {'Telegram Bot Token': '123:AA'}\nINFO 正常\n",
            encoding="utf-8",
        )
        with self._build() as archive:
            config = archive.read("configs/Notification.json").decode("utf-8")
            log = archive.read("logs/ok-script.log").decode("utf-8")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

        self.assertNotIn("secret", config)
        self.assertNotIn("123:AA", config)
        self.assertIn("System Notification", config)  # 非敏感项保留，方便排查
        self.assertNotIn("123:AA", log)
        self.assertIn("正常", log)
        self.assertGreaterEqual(manifest["redacted"]["count"], 2)
        self.assertEqual(manifest["redacted"]["log_lines"], 1)

    def test_broken_json_is_copied_as_is(self):
        (self.root / "configs" / "Broken.json").write_text("{ not json", encoding="utf-8")
        with self._build() as archive:
            self.assertEqual(archive.read("configs/Broken.json").decode("utf-8"), "{ not json")


if __name__ == "__main__":
    unittest.main()
