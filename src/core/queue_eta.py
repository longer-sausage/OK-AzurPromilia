"""队列预计耗时：记录每个任务最近几次的实际用时，算出「大概还要多久」。

- **历史**：每个任务只留最近 ``KEEP_RUNS``（5）次；不足 5 次就用手上这几条的平均值；
  一次都没跑过则返回 ``None``（界面显示「未知」，而不是瞎猜一个数）。
- **格式**：``约 45 秒`` / ``约 12 分 30 秒`` / ``约 1 小时 5 分``。
- **队列**：把「正在跑的 + 排队等着的」拼成若干行，每行带自己的预计耗时，最后给总计。

数据存 ``configs/task_times.json``：``{"任务名": [秒, 秒, ...]}``。坏值一律丢掉而不是抛错——
这个文件是给界面读的，读崩了整个悬浮窗就没了。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

TIMES_FILE = Path("configs") / "task_times.json"

#: 只保留最近几次
KEEP_RUNS = 5
#: 太短的一次（误触、秒退）不该左右平均值
MIN_RECORD_SECONDS = 3.0
UNKNOWN_TEXT = "未知"


def format_duration(seconds) -> str:
    """秒 → ``约 N 秒`` / ``约 N 分 M 秒``；``None`` 或非法值 → ``未知``。"""
    if seconds is None:
        return UNKNOWN_TEXT
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return UNKNOWN_TEXT
    if value != value or value < 0:  # NaN / 负数
        return UNKNOWN_TEXT
    total = round(value)
    if total < 60:
        return f"约 {total} 秒"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"约 {minutes} 分钟" if secs == 0 else f"约 {minutes} 分 {secs} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"约 {hours} 小时" if minutes == 0 else f"约 {hours} 小时 {minutes} 分"


def _clean_runs(runs) -> list[float]:
    """过滤出一个干净的历史列表（正数、有限、最多 ``KEEP_RUNS`` 条）。"""
    cleaned: list[float] = []
    if not isinstance(runs, (list, tuple)):
        return cleaned
    for value in runs:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number or number < MIN_RECORD_SECONDS:
            continue
        cleaned.append(round(number, 1))
    return cleaned[-KEEP_RUNS:]


def average_seconds(runs) -> float | None:
    """最近几次的平均值；没有可用记录返回 ``None``。"""
    cleaned = _clean_runs(runs)
    if not cleaned:
        return None
    return round(sum(cleaned) / len(cleaned), 1)


def load_history(path: str | Path | None = None) -> dict[str, list[float]]:
    """读历史；文件缺失/损坏返回空表。"""
    target = Path(path) if path is not None else TIMES_FILE
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    history: dict[str, list[float]] = {}
    for name, runs in data.items():
        cleaned = _clean_runs(runs)
        if cleaned:
            history[str(name)] = cleaned
    return history


def save_history(history: dict[str, list[float]], path: str | Path | None = None) -> Path:
    """原子写历史（只写干净的部分）。"""
    target = Path(path) if path is not None else TIMES_FILE
    payload = {str(name): _clean_runs(runs) for name, runs in (history or {}).items()}
    payload = {name: runs for name, runs in payload.items() if runs}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass
    return target


def record_run(history: dict[str, list[float]], name: str, seconds) -> dict[str, list[float]]:
    """记一次实际用时，返回新表（原地也改了，方便调用方直接存）。"""
    try:
        number = float(seconds)
    except (TypeError, ValueError):
        return history
    if number != number or number < MIN_RECORD_SECONDS:
        return history
    key = str(name or "").strip()
    if not key:
        return history
    runs = [*_clean_runs(history.get(key)), round(number, 1)]
    history[key] = runs[-KEEP_RUNS:]
    return history


def estimate_total(history: dict[str, list[float]], names) -> tuple[float | None, int]:
    """队列总耗时：``(已知项合计秒数, 未知项个数)``。"""
    total = 0.0
    unknown = 0
    for name in names:
        average = average_seconds(history.get(str(name)))
        if average is None:
            unknown += 1
        else:
            total += average
    return (round(total, 1) if total or not unknown else None), unknown


def build_rows(history: dict[str, list[float]], current: str | None, queued) -> list[dict]:
    """把「正在跑的 + 排队的」拼成界面要画的行。

    每行：``{"name", "state", "seconds", "eta_text"}``；``state`` 为 ``running`` / ``queued``。
    """
    rows: list[dict] = []
    names: list[str] = []

    def add(name: str, state: str) -> None:
        key = str(name or "").strip()
        if not key or key in names:
            return
        names.append(key)
        seconds = average_seconds(history.get(key))
        rows.append({"name": key, "state": state, "seconds": seconds, "eta_text": format_duration(seconds)})

    if current:
        add(current, "running")
    for name in queued or ():
        add(name, "queued")
    return rows


def total_text(history: dict[str, list[float]], rows) -> str:
    """总计文案：全部已知就给合计，有未知项就标注出来。"""
    if not rows:
        return UNKNOWN_TEXT
    total, unknown = estimate_total(history, [row["name"] for row in rows])
    if total is None:
        return UNKNOWN_TEXT
    text = format_duration(total)
    if unknown:
        text = f"{text}（{unknown} 项未知）"
    return text
