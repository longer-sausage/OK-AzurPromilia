"""子任务配置摘要：在「一条龙顺序」页一眼看到每一项自己的参数。

一条龙的每一项其实都是**独立注册的任务**（家园每日 / 委托每日 / 每日收菜），
它们各自把参数存在 ``configs/<任务名>.json`` 里，归属权、多账户覆盖、
配置迁移全都由那个任务自己管。这里不去抢这份归属，只做两件事：

- 读出来、提炼成一行（只显示**和默认值不同**的键，最多 ``limit`` 项）；
- 给出配置文件路径，UI 上「打开配置文件」直接调系统默认程序编辑。

这样既能看到每项的参数，又不会把同一份配置复制成两份造成漂移。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CONFIG_FOLDER = Path("configs")
MAX_VALUE_LENGTH = 24


def config_path(task_name: str, folder: str | Path | None = None) -> Path:
    """``configs/<任务名>.json``（框架就是按任务名存配置的）。"""
    base = Path(folder) if folder is not None else CONFIG_FOLDER
    return base / f"{str(task_name).strip()}.json"


def load_task_config(task_name: str, folder: str | Path | None = None) -> dict:
    """读任务配置；文件缺失或损坏都返回空 dict（摘要不该因为坏文件炸掉）。"""
    path = config_path(task_name, folder)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "开" if value else "关"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, (list, tuple)):
        text = "、".join(str(item) for item in value)
    elif isinstance(value, dict):
        return ""
    else:
        text = str(value).strip()
    if not text:
        return ""
    if len(text) > MAX_VALUE_LENGTH:
        text = text[: MAX_VALUE_LENGTH - 1] + "…"
    return text


def summarize_config(
    config: Mapping[str, Any] | None,
    defaults: Mapping[str, Any] | None = None,
    limit: int = 4,
) -> list[str]:
    """把配置提炼成 ``["键=值", ...]``，只保留**和默认值不同**的非隐藏键。"""
    if not config:
        return []
    base = defaults or {}
    items: list[str] = []
    for key, value in config.items():
        text_key = str(key)
        if text_key.startswith("_"):
            continue
        if text_key in base and base[text_key] == value:
            continue
        text = _format_value(value)
        if not text:
            continue
        items.append(f"{text_key}={text}")
        if len(items) >= max(1, int(limit)):
            break
    return items


def summary_line(
    task_name: str,
    defaults: Mapping[str, Any] | None = None,
    folder: str | Path | None = None,
    limit: int = 4,
) -> str:
    """一行摘要；没有配置文件时明确说「配置还是默认值」。"""
    config = load_task_config(task_name, folder)
    if not config:
        return "还没有独立配置（全部用默认值）"
    items = summarize_config(config, defaults, limit=limit)
    if not items:
        return "全部是默认值"
    return "，".join(items)


def has_own_config(task_name: str, folder: str | Path | None = None) -> bool:
    return config_path(task_name, folder).is_file()
