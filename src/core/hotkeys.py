"""全局快捷键：把 ``Ctrl+Alt+U`` 这种写法解析成 Windows 的 ``RegisterHotKey`` 参数。

框架只内置了「启动/停止」的 F9–F12（`ok/ui/qt/start/StartCard.py`），
暂停/继续、停止、开关 UID 遮挡这些都得自己注册。这里只做**解析与校验**：

- ``parse_hotkey("ctrl+alt+u")`` → ``(modifiers, vk)``，直接喂给
  ``user32.RegisterHotKey(hwnd, id, modifiers, vk)``；
- ``validate(specs)`` 提前发现拼错的键名、重复绑定、与框架内置键冲突；
- ``build_plan(specs)`` 给出「id → 键」的注册计划（id 从 1000 起，避开框架的 999）。

带修饰键的热键才是可靠做法：纯字母键会和游戏输入打架。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

MODIFIERS: Mapping[str, int] = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
}

#: 常用虚拟键码（够覆盖快捷键场景；不够就往这里加）
VK_CODES: Mapping[str, int] = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "tab": 0x09,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "pause": 0x13,
    "printscreen": 0x2C,
}
for _index in range(1, 25):
    VK_CODES[f"f{_index}"] = 0x70 + (_index - 1)
for _char in "0123456789":
    VK_CODES[_char] = ord(_char)
for _char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK_CODES[_char.lower()] = ord(_char)

#: 框架自己占用的键（启动/停止）
FRAMEWORK_HOTKEYS = ("f9", "f10", "f11", "f12")
HOTKEY_ID_BASE = 1000

_SPLIT = re.compile(r"[\s+\-]+")


class HotkeyError(ValueError):
    """快捷键写法不合法。"""


@dataclass(frozen=True)
class Hotkey:
    action: str
    keys: str
    modifiers: int = 0
    vk: int = 0

    @property
    def normalized(self) -> str:
        return self.keys

    def label(self) -> str:
        return f"{self.keys} → {self.action}"


def _split(spec: str) -> list[str]:
    parts = [part for part in _SPLIT.split(str(spec or "").strip().lower()) if part]
    if not parts:
        raise HotkeyError("快捷键是空的")
    return parts


def parse_hotkey(spec: str) -> tuple[int, int]:
    """解析成 ``(modifiers, vk)``。"""
    parts = _split(spec)
    modifiers = 0
    key: str | None = None
    for part in parts:
        if part in MODIFIERS:
            modifiers |= MODIFIERS[part]
            continue
        if key is not None:
            raise HotkeyError(f"一个快捷键只能有一个主键: {spec}")
        key = part
    if key is None:
        raise HotkeyError(f"缺少主键: {spec}")
    if key not in VK_CODES:
        raise HotkeyError(f"不认识的按键 {key!r}")
    if modifiers == 0 and not key.startswith("f"):
        raise HotkeyError(f"纯字母/数字键会和游戏输入冲突，请加修饰键: {spec}")
    return modifiers, VK_CODES[key]


def build_hotkey(action: str, keys: str) -> Hotkey:
    modifiers, vk = parse_hotkey(keys)
    return Hotkey(action=action, keys=str(keys).strip().lower(), modifiers=modifiers, vk=vk)


def identify(spec: str) -> str:
    """把写法规范成「修饰键有序 + 主键」的形式，用于查重。"""
    parts = _split(spec)
    order = ["ctrl", "alt", "shift", "win"]
    seen = []
    key = None
    for part in parts:
        normalized = MODIFIERS.get(part)
        if normalized is not None:
            name = {MOD_CONTROL: "ctrl", MOD_ALT: "alt", MOD_SHIFT: "shift", MOD_WIN: "win"}[normalized]
            if name not in seen:
                seen.append(name)
            continue
        key = part
    seen.sort(key=order.index)
    return "+".join([*seen, key or ""]).strip("+")


def validate(specs: Iterable[Hotkey] | Mapping[str, str]) -> list[str]:
    """返回问题列表（空表表示可以注册）。"""
    issues: list[str] = []
    items: list[Hotkey] = []
    if isinstance(specs, Mapping):
        for action, keys in specs.items():
            try:
                items.append(build_hotkey(action, keys))
            except HotkeyError as exc:
                issues.append(f"{action}: {exc}")
    else:
        items = list(specs)

    seen: dict[str, str] = {}
    for item in items:
        if not item.keys:
            issues.append(f"{item.action}: 没有配置按键")
            continue
        try:
            ident = identify(item.keys)
        except HotkeyError:
            issues.append(f"{item.action}: 写法不合法（{item.keys}）")
            continue
        if ident in FRAMEWORK_HOTKEYS:
            issues.append(f"{item.action}: {item.keys} 被框架的启动/停止占用")
        if ident in seen:
            issues.append(f"{item.action}: {item.keys} 与「{seen[ident]}」重复")
        else:
            seen[ident] = item.action
    return issues


def build_plan(specs: Mapping[str, str] | Iterable[Hotkey]) -> list[dict]:
    """生成注册计划：``[{"id": 1000, "action": ..., "modifiers": 2, "vk": 85}]``。"""
    items: list[Hotkey] = []
    if isinstance(specs, Mapping):
        for action, keys in specs.items():
            items.append(build_hotkey(action, keys))
    else:
        items = list(specs)

    issues = validate(items)
    if issues:
        raise HotkeyError("；".join(issues))
    plan = []
    for offset, item in enumerate(items):
        plan.append(
            {
                "id": HOTKEY_ID_BASE + offset,
                "action": item.action,
                "keys": item.keys,
                "modifiers": item.modifiers,
                "vk": item.vk,
            }
        )
    return plan


#: 默认绑定（与需求里的 F9 暂停/F10 停止、Ctrl+Alt+U 开关遮挡一致）
DEFAULT_HOTKEYS: Mapping[str, str] = {
    "pause": "ctrl+alt+p",
    "stop": "ctrl+alt+s",
    "toggle_uid_mask": "ctrl+alt+u",
}

#: 动作 → 中文说明（UI 与日志用）
ACTION_LABELS: Mapping[str, str] = {
    "pause": "暂停 / 继续",
    "stop": "停止当前任务",
    "toggle_uid_mask": "开关 UID 遮挡",
}


def find_action(plan: Iterable[dict], hotkey_id: int) -> str | None:
    """按 ``RegisterHotKey`` 的 id 反查动作名（消息循环里用）。"""
    for entry in plan:
        if int(entry.get("id", -1)) == int(hotkey_id):
            return str(entry.get("action", "") or "") or None
    return None


def load_hotkeys(path=None) -> dict[str, str]:
    """读 ``configs/hotkeys.json``；缺失/损坏就退回默认绑定。"""
    import json
    from pathlib import Path

    file_path = Path(path) if path is not None else Path("configs") / "hotkeys.json"
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_HOTKEYS)
    if not isinstance(data, dict):
        return dict(DEFAULT_HOTKEYS)
    merged = dict(DEFAULT_HOTKEYS)
    for action, keys in data.items():
        if isinstance(keys, str) and keys.strip():
            merged[str(action)] = keys.strip()
    return merged


def save_hotkeys(hotkeys: Mapping[str, str], path=None) -> Path:
    """写回 ``configs/hotkeys.json``（原子写）。"""
    file_path = Path(path) if path is not None else Path("configs") / "hotkeys.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(hotkeys), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file_path)
    return file_path
