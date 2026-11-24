"""失败帧库：把任务失败那一刻的识别帧存盘，供事后复测。

为什么单独存、不用框架的 ``screenshots/``：

1. 框架的 ``screenshots/`` 有体积/天数清理策略，失败现场会被顺手删掉；
2. 复测要把这一帧喂给**全部模板**并按得分排序，需要一个稳定的索引（时间/任务/消息）；
3. 框架落盘图会被压缩/加框，与识别帧不是同一份输入。

**隐私遮挡**：帧来自识别用的原始画面，落盘前过一遍 ``screenshot_processor``
（与框架落盘截图用同一个处理器），所以失败帧里同样看不到被遮挡的区域。
处理器抛异常时会退回未遮挡并记一条警告 —— 失败路径上不允许因为遮罩把帧丢掉。
被遮挡的帧在索引里标 ``masked: true``，复测页会提示「遮挡区内的模板必然不命中」。

设计约束：

- ``save()`` 在失败路径上被调用，**任何异常都不能外抛**，否则会盖掉原始错误；
- 帧文件与索引分开写，索引最后写，保证索引里出现的帧一定存在；
- 索引写盘用 ``.tmp`` + ``os.replace``，避免半截 JSON。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from ok.util.file import get_relative_path

DEFAULT_FOLDER = "failure_frames"
INDEX_NAME = "index.json"
DEFAULT_LIMIT = 20

_SAFE_NAME = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff-]+")


def _safe_token(text: str, fallback: str = "task", max_len: int = 40) -> str:
    token = _SAFE_NAME.sub("_", str(text or "")).strip("_")
    if not token:
        token = fallback
    return token[:max_len]


@dataclass(frozen=True)
class FailureFrameRecord:
    """一条失败帧记录。``path`` 是相对 ``root`` 的文件名。"""

    id: str
    file: str
    created_at: float
    task: str
    message: str
    width: int
    height: int
    channels: int = 3
    masked: bool = False

    @property
    def created_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file": self.file,
            "created_at": self.created_at,
            "task": self.task,
            "message": self.message,
            "width": self.width,
            "height": self.height,
            "channels": self.channels,
            "masked": self.masked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FailureFrameRecord:
        return cls(
            id=str(data.get("id", "")),
            file=str(data.get("file", "")),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            task=str(data.get("task", "")),
            message=str(data.get("message", "")),
            width=int(data.get("width", 0) or 0),
            height=int(data.get("height", 0) or 0),
            channels=int(data.get("channels", 3) or 3),
            masked=bool(data.get("masked", False)),
        )

    def label(self) -> str:
        """UI 里展示用的单行标题。"""
        head = f"{self.created_text}  {self.task or '未知任务'}  {self.width}×{self.height}"
        if self.masked:
            head += "  已遮挡"
        if self.message:
            head += f"  {self.message}"
        return head


class FailureFrameStore:
    """失败帧的落盘、索引与读取。"""

    def __init__(
        self,
        root: str | Path | None = None,
        limit: int = DEFAULT_LIMIT,
        log: Callable[[str], None] | None = None,
        processor: Callable[[np.ndarray], np.ndarray | None] | None = None,
    ):
        self.root = Path(root) if root is not None else Path(get_relative_path(DEFAULT_FOLDER))
        self.limit = max(1, int(limit or DEFAULT_LIMIT))
        self._log = log
        self._processor = processor
        self._index_path = self.root / INDEX_NAME

    # ── 路径 ──────────────────────────────────────────────
    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def path_of(self, record: FailureFrameRecord) -> Path:
        return self.root / record.file

    def set_limit(self, limit: int) -> None:
        self.limit = max(1, int(limit or DEFAULT_LIMIT))

    # ── 索引读写 ──────────────────────────────────────────
    def list(self) -> list[FailureFrameRecord]:
        """按时间倒序返回全部记录；索引缺失/损坏都返回空表。"""
        try:
            raw = self._index_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return []
        items: Iterable = data.get("frames", []) if isinstance(data, dict) else data
        records = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            record = FailureFrameRecord.from_dict(item)
            if record.file and self.path_of(record).exists():
                records.append(record)
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def latest(self) -> FailureFrameRecord | None:
        records = self.list()
        return records[0] if records else None

    def get(self, frame_id: str) -> FailureFrameRecord | None:
        for record in self.list():
            if record.id == frame_id:
                return record
        return None

    def _write_index(self, records: list[FailureFrameRecord]) -> None:
        self._ensure_root()
        payload = {"version": 1, "frames": [r.to_dict() for r in records]}
        tmp = self._index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._index_path)

    # ── 写入 ──────────────────────────────────────────────
    def save(
        self,
        frame: np.ndarray | None,
        task: str = "",
        message: str = "",
        created_at: float | None = None,
    ) -> FailureFrameRecord | None:
        """保存一帧。失败时返回 ``None``，绝不抛异常。"""
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return None
        frame, masked = self._apply_processor(frame)
        try:
            self._ensure_root()
            created = float(created_at if created_at is not None else time.time())
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(created))
            base_id = f"{stamp}_{int(created * 1000) % 1000:03d}"
            frame_id, file_name = self._unique_name(base_id, _safe_token(task))
            file_path = self.root / file_name
            if not cv2.imwrite(str(file_path), frame):
                self._warn(f"失败帧写入失败: {file_path}")
                return None

            height, width = frame.shape[:2]
            channels = frame.shape[2] if frame.ndim == 3 else 1
            record = FailureFrameRecord(
                id=frame_id,
                file=file_name,
                created_at=created,
                task=str(task or ""),
                message=str(message or ""),
                width=int(width),
                height=int(height),
                channels=int(channels),
                masked=masked,
            )

            records = [record, *self.list()]
            records, removed = self._prune_records(records)
            self._write_index(records)
            for old in removed:
                self._remove_file(old)
            return record
        except Exception as exc:  # 失败路径上不允许再抛
            self._warn(f"保存失败帧异常: {exc}")
            return None

    def _apply_processor(self, frame: np.ndarray) -> tuple[np.ndarray, bool]:
        """落盘前过一遍隐私处理器（返回 ``(帧, 是否遮挡成功)``）。

        处理器抛异常就退回未遮挡的帧再记一条警告：失败路径上宁可有隐私风险，
        也不能因为遮罩把失败现场弄丢（用户装了遮罩多半也知道自己在录什么）。
        """
        if self._processor is None:
            return frame, False
        try:
            processed = self._processor(frame)
        except Exception as exc:
            self._warn(f"失败帧遮挡失败，按未遮挡保存: {exc}")
            return frame, False
        if not isinstance(processed, np.ndarray) or processed.size == 0:
            self._warn("失败帧遮挡返回了空结果，按未遮挡保存")
            return frame, False
        return processed, True

    def _prune_records(self, records: list[FailureFrameRecord]):
        kept = records[: self.limit]
        removed = records[self.limit:]
        return kept, removed

    def _unique_name(self, base_id: str, task_token: str) -> tuple[str, str]:
        """同一毫秒内多次失败时追加序号，保证 id 与文件名都唯一。"""
        index = 0
        while True:
            frame_id = base_id if index == 0 else f"{base_id}_{index:02d}"
            file_name = f"{frame_id}_{task_token}.png"
            if not (self.root / file_name).exists():
                return frame_id, file_name
            index += 1

    def _remove_file(self, record: FailureFrameRecord) -> None:
        with contextlib.suppress(OSError):
            self.path_of(record).unlink(missing_ok=True)

    # ── 读取与清理 ────────────────────────────────────────
    def load_image(self, record: FailureFrameRecord) -> np.ndarray | None:
        try:
            data = np.fromfile(str(self.path_of(record)), dtype=np.uint8)
        except OSError:
            return None
        if data.size == 0:
            return None
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return image

    def clear(self) -> int:
        """清空失败帧库，返回删除的帧数。"""
        records = self.list()
        for record in records:
            self._remove_file(record)
        with contextlib.suppress(OSError):
            self._index_path.unlink(missing_ok=True)
        return len(records)

    def total_size(self) -> int:
        return sum(self.path_of(r).stat().st_size for r in self.list() if self.path_of(r).exists())

    def _warn(self, message: str) -> None:
        if self._log:
            with contextlib.suppress(Exception):
                self._log(message)
