"""记账与运行报表。

两件事共用一套 JSONL 追加写法（一行一条、原子追加、坏行跳过）：

- ``ledger.jsonl`` —— 结算记账。把结算界面的文字（OCR 结果或手工粘贴）
  解析成 ``物品 ×数量`` 的收支条目；
- ``runs.jsonl`` —— 运行报表。每个任务结束记一条成功/失败与用时，
  用来算成功率、平均用时，以及任务栏的「预计剩余时间」。

结算文本的常见写法（都支持，``×`` 也可以写成 ``x`` / ``*``）::

    获得的奖励：尤尼 ×1,500
    12 × 波力果
    普通的灵粹*24
    消耗：星脉结晶 ×3

带「消耗 / 花费 / 扣除 / 支出」的行记为支出，其余记为收入。
"""

from __future__ import annotations

import contextlib
import csv
import json
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from src.core.file_naming import ensure_suffix

DEFAULT_LEDGER_NAME = "ledger.jsonl"
DEFAULT_RUNS_NAME = "runs.jsonl"

INCOME_WORDS = ("获得", "奖励", "收获", "得到", "收入", "掉落")
EXPENSE_WORDS = ("消耗", "花费", "扣除", "支出", "消耗了", "使用")

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
# 物品名：中文/字母/数字/·-_，遇到 × x * 冒号逗号空白就截断
_NAME = r"[^\s×✕xX*:：,，、()（）\[\]【】]+"

# 「尤尼 ×1,500」——名字在前
_NAME_FIRST = re.compile(rf"(?P<name>{_NAME})\s*[×✕xX*]\s*(?P<count>{_NUMBER})")
# 「12 × 波力果」——数量在前
_COUNT_FIRST = re.compile(rf"(?P<count>{_NUMBER})\s*[×✕xX*]\s*(?P<name>{_NAME})")

_SKIP_NAMES = {"个", "件", "次", "共"}


def _valid_name(name: str) -> bool:
    """物品名必须含至少一个非数字字符。

    否则「材料2 ×3」会被「数量在前」的正则再匹配一次（``2 ×3`` → 物品 ``3``），
    凭空多出一条记录。
    """
    return any(not ch.isdigit() for ch in name) and name not in _SKIP_NAMES


class SettlementFormatError(ValueError):
    """结算文本里没有解析出任何条目。"""


@dataclass(frozen=True)
class LedgerEntry:
    item: str
    amount: float
    kind: str = "income"
    source: str = ""
    ts: float = 0.0

    @property
    def signed_amount(self) -> float:
        return self.amount if self.kind == "income" else -self.amount

    def to_dict(self) -> dict:
        return {
            "ts": self.ts or time.time(),
            "kind": self.kind,
            "item": self.item,
            "amount": self.amount,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LedgerEntry:
        return cls(
            item=str(data.get("item", "")),
            amount=float(data.get("amount", 0) or 0),
            kind=str(data.get("kind", "income") or "income"),
            source=str(data.get("source", "") or ""),
            ts=float(data.get("ts", 0) or 0),
        )


def _to_amount(raw: str) -> float:
    text = str(raw).replace(",", "").strip()
    try:
        value = float(text)
    except ValueError:
        return 0.0
    return value if value > 0 else 0.0


def _line_kind(line: str) -> str:
    if any(word in line for word in EXPENSE_WORDS):
        return "expense"
    return "income"


def parse_settlement_text(text: str, source: str = "settlement") -> list[LedgerEntry]:
    """把结算文字解析成收支条目。"""
    entries: list[LedgerEntry] = []
    seen: set[tuple[str, float, str]] = set()

    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        kind = _line_kind(line)
        found: list[tuple[int, str, float]] = []
        for pattern in (_NAME_FIRST, _COUNT_FIRST):
            for match in pattern.finditer(line):
                name = match.group("name").strip()
                amount = _to_amount(match.group("count"))
                if not name or amount <= 0 or not _valid_name(name):
                    continue
                found.append((match.start(), name, amount))

        # 同一条目可能被两个正则各匹配一次（例如「12 × 波力果」），按位置去重
        found.sort(key=lambda item: item[0])
        used_positions: list[int] = []
        for position, name, amount in found:
            if any(abs(position - used) < len(str(int(amount))) + len(name) for used in used_positions):
                continue
            used_positions.append(position)
            key = (name, amount, kind)
            if key in seen:
                continue
            seen.add(key)
            entries.append(LedgerEntry(item=name, amount=amount, kind=kind, source=source))

    if not entries:
        raise SettlementFormatError("没有识别到「物品 ×数量」形式的条目")
    return entries


class JsonlStore:
    """极简 JSONL 追加器：写一行、读全部、坏行跳过。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, payload: dict) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return True
        except OSError:
            return False

    def read(self) -> Iterator[dict]:
        if not self.path.is_file():
            return iter(())
        return self._iter_rows()

    def _iter_rows(self) -> Iterator[dict]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        yield row
        except OSError:
            return

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)


class Ledger:
    """结算记账本。"""

    def __init__(self, path: str | Path = DEFAULT_LEDGER_NAME):
        self.store = JsonlStore(path)

    @property
    def path(self) -> Path:
        return self.store.path

    def record(self, entries: Iterable[LedgerEntry], ts: float | None = None) -> int:
        now = time.time() if ts is None else float(ts)
        written = 0
        for entry in entries:
            payload = entry.to_dict()
            payload["ts"] = now
            if self.store.append(payload):
                written += 1
        return written

    def record_text(self, text: str, source: str = "settlement", ts: float | None = None) -> int:
        return self.record(parse_settlement_text(text, source=source), ts=ts)

    def entries(self) -> list[LedgerEntry]:
        result = []
        for row in self.store.read():
            try:
                result.append(LedgerEntry.from_dict(row))
            except (TypeError, ValueError):
                continue
        return result

    def summary(self, since: float | None = None) -> dict[str, dict[str, float]]:
        """按物品汇总收入 / 支出 / 净值。"""
        summary: dict[str, dict[str, float]] = {}
        for entry in self.entries():
            if since is not None and entry.ts < since:
                continue
            bucket = summary.setdefault(entry.item, {"income": 0.0, "expense": 0.0, "net": 0.0})
            if entry.kind == "expense":
                bucket["expense"] += entry.amount
            else:
                bucket["income"] += entry.amount
            bucket["net"] = bucket["income"] - bucket["expense"]
        return summary

    def today_summary(self) -> dict[str, dict[str, float]]:
        start = time.mktime((*time.localtime()[:3], 0, 0, 0, 0, 0, -1))
        return self.summary(since=start)

    def totals(self, since: float | None = None) -> dict[str, float]:
        income = expense = 0.0
        for entry in self.entries():
            if since is not None and entry.ts < since:
                continue
            if entry.kind == "expense":
                expense += entry.amount
            else:
                income += entry.amount
        return {"income": income, "expense": expense, "net": income - expense}

    def export_csv(self, path: str | Path) -> Path:
        target = ensure_suffix(path, '.csv')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["时间", "类型", "物品", "数量", "来源"])
            for entry in self.entries():
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.ts)) if entry.ts else ""
                writer.writerow([
                    stamp,
                    "支出" if entry.kind == "expense" else "收入",
                    entry.item,
                    entry.amount,
                    entry.source,
                ])
        return target


class RunLog:
    """任务运行报表。"""

    def __init__(self, path: str | Path = DEFAULT_RUNS_NAME):
        self.store = JsonlStore(path)

    @property
    def path(self) -> Path:
        return self.store.path

    def record(self, task: str, ok: bool, seconds: float, ts: float | None = None, note: str = "") -> bool:
        """记一条运行结果。``note`` 用来存失败原因（成功时留空），旧记录没有这个字段也能读。

        用时会被夹到 **≥ 0**：系统时间跳变 / 暂停感知时钟算出负数时，
        负时长会污染平均用时，进而把任务栏进度和 HUD 的「预计剩余」全带偏。
        """
        try:
            elapsed = max(0.0, float(seconds))
        except (TypeError, ValueError):
            elapsed = 0.0
        data = {
            "ts": time.time() if ts is None else float(ts),
            "task": str(task),
            "ok": bool(ok),
            "seconds": round(elapsed, 3),
        }
        if note:
            data["note"] = str(note)[:200]
        return self.store.append(data)

    def rows(self) -> list[dict]:
        result = []
        for row in self.store.read():
            try:
                result.append(
                    {
                        "ts": float(row.get("ts", 0) or 0),
                        "task": str(row.get("task", "")),
                        "ok": bool(row.get("ok", False)),
                        "seconds": float(row.get("seconds", 0) or 0),
                        "note": str(row.get("note", "") or ""),
                    }
                )
            except (TypeError, ValueError):
                continue
        return result

    def stats(self, task: str | None = None) -> dict[str, float]:
        rows = [row for row in self.rows() if task is None or row["task"] == task]
        count = len(rows)
        success = sum(1 for row in rows if row["ok"])
        total_seconds = sum(row["seconds"] for row in rows)
        return {
            "count": count,
            "success": success,
            "failed": count - success,
            "success_rate": (success / count) if count else 0.0,
            "avg_seconds": (total_seconds / count) if count else 0.0,
            "total_seconds": total_seconds,
        }

    def by_task(self) -> dict[str, dict[str, float]]:
        """按任务分组统计（次数/成功/失败/成功率/平均/总用时）。"""
        grouped: dict[str, list[dict]] = {}
        for row in self.rows():
            grouped.setdefault(row["task"], []).append(row)
        result: dict[str, dict[str, float]] = {}
        for task, rows in grouped.items():
            count = len(rows)
            success = sum(1 for row in rows if row["ok"])
            total = sum(row["seconds"] for row in rows)
            result[task] = {
                "count": count,
                "success": success,
                "failed": count - success,
                "success_rate": (success / count) if count else 0.0,
                "avg_seconds": (total / count) if count else 0.0,
                "total_seconds": total,
            }
        return result

    def failure_notes(self, limit: int = 8) -> list[tuple[str, int]]:
        """失败原因汇总：``[(原因, 次数)]``，按次数降序；没写原因的归到「未记录原因」。"""
        counts: dict[str, int] = {}
        for row in self.rows():
            if row["ok"]:
                continue
            reason = str(row.get("note", "") or "").strip() or "未记录原因"
            counts[reason] = counts.get(reason, 0) + 1
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return ordered[: max(1, int(limit))]

    def report_lines(self, limit: int = 8) -> list[str]:
        """给 UI 直接渲染的多行报表文本。"""
        stats = self.stats()
        if not stats["count"]:
            return ["还没有运行记录"]
        total_minutes = stats["total_seconds"] / 60
        lines = [
            f"共 {int(stats['count'])} 次，成功 {int(stats['success'])} 次，"
            f"成功率 {stats['success_rate'] * 100:.1f}%，平均 {stats['avg_seconds']:.0f} 秒，"
            f"累计 {total_minutes:.0f} 分钟",
        ]
        breakdown = self.by_task()
        if len(breakdown) > 1:
            lines.append("按任务：")
            for task, item in sorted(breakdown.items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:limit]:
                lines.append(
                    f"  {task}：{int(item['count'])} 次，成功率 {item['success_rate'] * 100:.0f}%，"
                    f"平均 {item['avg_seconds']:.0f} 秒"
                )
        notes = self.failure_notes(limit=limit)
        if notes:
            lines.append("失败原因：")
            for reason, count in notes:
                lines.append(f"  {reason} ×{count}")
        return lines

    def estimate_seconds(self, task: str | None = None, default: float = 0.0) -> float:
        """按历史平均用时估算一次任务要多久（任务栏「预计剩余」用）。"""
        stats = self.stats(task)
        return stats["avg_seconds"] or default

    def progress_ratio(
        self,
        task: str | None,
        elapsed_seconds: float,
        cap: float = 0.97,
        fallback_seconds: float | None = None,
    ) -> float:
        """任务栏进度条比例：``已用时间 / 历史平均``，**永远到不了 100%**。

        进度条的意义是「大概还剩多少」，估到 100% 还在跑反而误导人，
        所以按 ``cap`` 封顶；真结束时由调用方自己置 1.0 或隐藏。
        没有历史数据时用 ``fallback_seconds``，两者都没有就返回 0。
        """
        cap = max(0.0, min(float(cap), 0.99))
        estimate = self.estimate_seconds(task, fallback_seconds or 0.0)
        if estimate <= 0:
            return 0.0
        ratio = max(0.0, float(elapsed_seconds)) / float(estimate)
        return min(cap, ratio)

    def export_csv(self, path: str | Path) -> Path:
        target = ensure_suffix(path, '.csv')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["时间", "任务", "结果", "用时(秒)"])
            for row in self.rows():
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"])) if row["ts"] else ""
                writer.writerow([stamp, row["task"], "成功" if row["ok"] else "失败", row["seconds"]])
        return target
