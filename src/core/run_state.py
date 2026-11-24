"""运行时状态：让悬浮窗（以及以后的 HUD/任务栏）读到「现在在跑什么、队列还剩多久」。

为什么单独一个模块：悬浮窗是个独立窗口，不能直接问任务对象要状态；而队列执行器
跑在任务线程里。两边通过这里交换一份**只读快照**，谁也不依赖谁。

- 写入方：``QueueRunner``（队列开始/每项切换/结束）、``FlowTask``（任务名）；
- 读取方：``FloatWindow``（每 500ms 一次）、``taskbar``（以后可接）；
- 线程安全：一个 ``threading.Lock`` 保护内部字典，读返回深拷贝。
"""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_STATE: dict = {}


def _estimate(run_log, name: str) -> float | None:
    if run_log is None or not name:
        return None
    try:
        value = run_log.estimate_seconds(str(name))
    except Exception:
        return None
    if value is None or value <= 0:
        return None
    return float(value)


def set_queue(name: str, items, run_log=None) -> None:
    """队列开始：记录名字与每项的预估用时。"""
    rows = []
    for item in items or []:
        label = getattr(item, "name", None) or getattr(item, "task_name", None) or str(item)
        rows.append(
            {
                "name": str(label),
                "state": "等待",
                "eta_seconds": _estimate(run_log, str(label)),
                "started_at": None,
                "finished_at": None,
            }
        )
    with _LOCK:
        _STATE["queue"] = str(name or "")
        _STATE["items"] = rows
        _STATE["started_at"] = time.monotonic()


def start_item(index: int) -> None:
    with _LOCK:
        items = _STATE.get("items") or []
        if 0 <= index < len(items):
            items[index]["state"] = "运行中"
            items[index]["started_at"] = time.monotonic()


def finish_item(index: int, state: str = "完成") -> None:
    with _LOCK:
        items = _STATE.get("items") or []
        if 0 <= index < len(items):
            items[index]["state"] = state
            items[index]["finished_at"] = time.monotonic()
            started = items[index].get("started_at")
            if started:
                # 用实测用时覆盖预估，下次估得更准
                items[index]["eta_seconds"] = max(0.0, time.monotonic() - started)


def skip_item(index: int) -> None:
    finish_item(index, "跳过")


def set_task(name: str, paused: bool = False) -> None:
    with _LOCK:
        _STATE["task"] = str(name or "")
        _STATE["paused"] = bool(paused)


def clear() -> None:
    with _LOCK:
        _STATE.clear()


def snapshot(run_log=None, now: float | None = None) -> dict:
    """当前状态快照（悬浮窗每帧读它）。"""
    moment = time.monotonic() if now is None else now
    with _LOCK:
        items = [dict(item) for item in (_STATE.get("items") or [])]
        task = str(_STATE.get("task", "") or "")
        queue = str(_STATE.get("queue", "") or "")
        paused = bool(_STATE.get("paused"))
        started = _STATE.get("started_at")

    remaining = 0.0
    known = 0
    for item in items:
        state = item.get("state")
        if state in ("完成", "失败", "跳过"):
            continue
        eta = item.get("eta_seconds")
        if not isinstance(eta, (int, float)):
            eta = _estimate(run_log, item.get("name", ""))
            item["eta_seconds"] = eta
        if isinstance(eta, (int, float)):
            remaining += float(eta)
            known += 1
        if state == "运行中" and isinstance(item.get("started_at"), (int, float)):
            # 正在跑的那项扣掉已过时间，剩余时间才不会“站着不动”
            remaining = max(0.0, remaining - (moment - float(item["started_at"])))

    return {
        "task": task,
        "queue": queue,
        "paused": paused,
        "since": started,
        "items": items,
        # 一项预估都没有时返回 None，UI 显示「未知」而不是「约 0 秒」
        "remaining_seconds": remaining if known else None,
    }
