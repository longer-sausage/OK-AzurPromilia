"""队列的数据模型与存取。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

QUEUE_FOLDER = Path("configs") / "queues"

#: 队列项类型：
#: - ``flow``   流程（``configs/flows/<名字>.json``）
#: - ``task``   注册在 ``onetime_tasks`` 里的一次性任务（按类名）
#: - ``assist`` 注册在 ``trigger_tasks`` 里的触发任务（按类名）
#: - ``scheme`` 轮椅方案（录制回放，批次 D 提供）
QUEUE_KINDS = ("flow", "task", "assist", "scheme")

_SAFE_NAME = re.compile(r"[^0-9A-Za-z_.\u4e00-\u9fff-]+")


class QueueFormatError(ValueError):
    """队列 JSON 结构不合法。"""


def _safe(name: str) -> str:
    token = _SAFE_NAME.sub("_", str(name or "")).strip("._")
    if not token:
        raise QueueFormatError("队列名字不能为空")
    return token


@dataclass(frozen=True)
class QueueItem:
    kind: str
    ref: str
    budget_minutes: float = 0.0
    enabled: bool = True
    name: str = ""

    def __post_init__(self):
        if self.kind not in QUEUE_KINDS:
            raise QueueFormatError(f"队列项类型只能是 {', '.join(QUEUE_KINDS)}，收到 {self.kind!r}")
        if not str(self.ref or "").strip():
            raise QueueFormatError("队列项的 ref 不能为空")

    @property
    def label(self) -> str:
        return self.name or f"{self.kind}:{self.ref}"

    @property
    def budget_seconds(self) -> float | None:
        minutes = float(self.budget_minutes or 0.0)
        return minutes * 60.0 if minutes > 0 else None

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"kind": self.kind, "ref": self.ref}
        if self.budget_minutes:
            data["budget_minutes"] = self.budget_minutes
        if not self.enabled:
            data["enabled"] = False
        if self.name:
            data["name"] = self.name
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QueueItem:
        if not isinstance(data, Mapping):
            raise QueueFormatError("队列项必须是 JSON 对象")
        try:
            budget = float(data.get("budget_minutes", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise QueueFormatError("budget_minutes 必须是数字") from exc
        if budget < 0:
            raise QueueFormatError("budget_minutes 不能是负数")
        return cls(
            kind=str(data.get("kind", "") or "").strip(),
            ref=str(data.get("ref", "") or "").strip(),
            budget_minutes=budget,
            enabled=bool(data.get("enabled", True)),
            name=str(data.get("name", "") or ""),
        )


@dataclass
class TaskQueue:
    name: str = "默认队列"
    items: tuple[QueueItem, ...] = ()
    stop_on_failure: bool = True
    description: str = ""
    issues: list[str] = field(default_factory=list)

    # ── 存取 ─────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskQueue:
        if not isinstance(data, Mapping):
            raise QueueFormatError("队列顶层必须是 JSON 对象")
        raw_items = data.get("items")
        if raw_items is None:
            raise QueueFormatError("队列缺少 items")
        if not isinstance(raw_items, list):
            raise QueueFormatError("items 必须是数组")
        items = []
        for index, item in enumerate(raw_items):
            try:
                items.append(QueueItem.from_dict(item))
            except QueueFormatError as exc:
                raise QueueFormatError(f"items[{index}]: {exc}") from exc
        return cls(
            name=str(data.get("name", "") or "默认队列"),
            items=tuple(items),
            stop_on_failure=bool(data.get("stop_on_failure", True)),
            description=str(data.get("description", "") or ""),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> TaskQueue:
        file_path = Path(path)
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise QueueFormatError(f"读不到队列文件: {exc}") from exc
        except ValueError as exc:
            raise QueueFormatError(f"队列文件不是合法 JSON: {exc}") from exc
        queue = cls.from_dict(data)
        if not queue.name:
            queue.name = file_path.stem
        return queue

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "stop_on_failure": self.stop_on_failure,
            "items": [item.to_dict() for item in self.items],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def save(self, folder: str | Path | None = None) -> Path:
        return save_queue(self, folder)

    # ── 视图 ─────────────────────────────────────────────
    @property
    def enabled_items(self) -> list[QueueItem]:
        return [item for item in self.items if item.enabled]

    def total_budget_minutes(self) -> float:
        return sum(item.budget_minutes for item in self.enabled_items)

    def is_empty(self) -> bool:
        return not self.enabled_items


def queue_path(name: str, folder: str | Path | None = None) -> Path:
    folder = Path(folder) if folder is not None else QUEUE_FOLDER
    return folder / f"{_safe(name)}.json"


def save_queue(queue: TaskQueue, folder: str | Path | None = None) -> Path:
    path = queue_path(queue.name, folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_queue(name: str, folder: str | Path | None = None) -> TaskQueue:
    path = queue_path(name, folder)
    if not path.exists():
        raise QueueFormatError(f"队列不存在: {name}")
    return TaskQueue.from_file(path)


def list_queues(folder: str | Path | None = None) -> list[str]:
    folder = Path(folder) if folder is not None else QUEUE_FOLDER
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.json"))


def iter_items(items: Iterable[QueueItem]) -> Iterable[QueueItem]:
    return (item for item in items if item.enabled)
