"""诊断包脱敏：把配置与日志里的 token / webhook / 账号名抹掉再打包。

诊断包是发出去给别人看的（QQ 群、issue）。框架的 ``Notification`` 配置里有
**Discord Webhook、Telegram Bot Token、QQ Bot API Token**，日志里还会在 DEBUG 级别
把整份配置打出来（``Config:init self.config = {...}``）——原样打包等于把
「往你的 Discord 频道发消息」「用你的 bot 发消息」的权限一起送出去。

所以打包前过一层脱敏（:func:`redact_config` / :func:`redact_text`）：

- 键名命中敏感词 → 值替换成 ``***``（保留键名与结构，方便看「有没有配」）；
- 列表里的账号项**逐项替换**，长度保留（``3 个账号`` 这个信息对排查有用）；
- 日志里 ``'Discord Webhook': 'https://…'`` 与 ``webhook = https://…`` 两种写法都抹；
- 只做「值替换」，不会删键——删了排查时就不知道用户到底配没配。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PLACEHOLDER = "***"

#: 键名包含这些片段（小写、把 ``_``/``-`` 当空格比较）就脱敏
SENSITIVE_KEY_PATTERNS = (
    "password",
    "passwd",
    "token",
    "secret",
    "webhook",
    "api id",
    "api key",
    "apikey",
    "cookie",
    "session",
    "chat id",
    "chatid",
    "nickname",
    "user id",
    "userid",
    "账号",
    "帐号",
    "密码",
    "昵称",
    "手机",
    "邮箱",
)


@dataclass
class RedactionReport:
    """脱敏了什么（写进诊断包 manifest，让收包的人知道哪些值不可信）。"""

    keys: list[str] = field(default_factory=list)
    log_lines: int = 0

    @property
    def count(self) -> int:
        return len(self.keys)

    def add(self, path: str) -> None:
        if path and path not in self.keys:
            self.keys.append(path)

    def to_dict(self) -> dict:
        return {"count": self.count, "keys": self.keys[:50], "log_lines": self.log_lines}

    def summary(self) -> str:
        if not self.count and not self.log_lines:
            return "没有需要脱敏的内容"
        parts = []
        if self.count:
            parts.append(f"配置项 {self.count} 个")
        if self.log_lines:
            parts.append(f"日志 {self.log_lines} 行")
        return "已脱敏：" + "、".join(parts)


def is_sensitive_key(key: Any) -> bool:
    text = str(key or "").strip().lower()
    if not text:
        return False
    # 「Telegram_Chat-ID」「apiId」这类写法统一成空格分隔再比较
    text = text.replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    return any(pattern in text for pattern in SENSITIVE_KEY_PATTERNS)


def redact_value(value: Any) -> Any:
    """把一个敏感值替换成占位符，尽量保留类型与长度信息。"""
    if isinstance(value, str):
        return PLACEHOLDER if value else value
    if isinstance(value, bool) or value is None:
        return PLACEHOLDER
    if isinstance(value, (int, float)):
        return PLACEHOLDER
    if isinstance(value, (list, tuple)):
        return [PLACEHOLDER for _ in value]
    if isinstance(value, dict):
        return dict.fromkeys(value, PLACEHOLDER)
    return PLACEHOLDER


def redact_config(data: Any, path: str = "", report: RedactionReport | None = None) -> Any:
    """递归脱敏一份配置；返回新的结构（不改原对象）。"""
    report = report if report is not None else RedactionReport()

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            child_path = f"{path}.{key}" if path else str(key)
            if is_sensitive_key(key):
                result[key] = redact_value(value)
                report.add(child_path)
            else:
                result[key] = redact_config(value, child_path, report)
        return result
    if isinstance(data, list):
        return [redact_config(item, f"{path}[{index}]", report) for index, item in enumerate(data)]
    return data


def redact_config_with_report(data: Any) -> tuple[Any, RedactionReport]:
    report = RedactionReport()
    return redact_config(data, report=report), report


# ── 日志文本 ─────────────────────────────────────────────
#: ``'Discord Webhook': 'https://…'`` / ``"token": "abc"`` 这种键值对
_KEY_VALUE_JSON = re.compile(
    r"""(?P<key>['"])(?P<name>[^'"]{1,64})(?P=key)\s*:\s*(?P<quote>['"])(?P<value>(?:\\.|[^'"\\])*)(?P=quote)""",
)
#: ``webhook = https://…`` / ``token=abc`` 这种无引号写法
_KEY_VALUE_PLAIN = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_ .\-]{0,40})\s*[=:]\s*(?P<value>https?://\S+|\S{6,})",
)


def redact_text(text: str) -> tuple[str, int]:
    """抹掉日志文本里的敏感值，返回 ``(文本, 抹掉的行数)``。"""
    if not text:
        return text, 0
    lines = text.splitlines()
    changed = 0
    out: list[str] = []
    for line in lines:
        if not any(pattern for pattern in SENSITIVE_KEY_PATTERNS if pattern in line.lower()):
            out.append(line)
            continue

        new_line = _KEY_VALUE_JSON.sub(
            lambda match: match.group(0).replace(match.group("value"), PLACEHOLDER)
            if is_sensitive_key(match.group("name"))
            else match.group(0),
            line,
        )
        new_line = _KEY_VALUE_PLAIN.sub(
            lambda match: match.group(0).replace(match.group("value"), PLACEHOLDER)
            if is_sensitive_key(match.group("name"))
            else match.group(0),
            new_line,
        )
        if new_line != line:
            changed += 1
        out.append(new_line)
    return "\n".join(out), changed
