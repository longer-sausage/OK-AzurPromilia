"""队列执行器。

调用方注入 ``resolver(item) -> callable(budget_seconds) -> bool``：

```python
def resolver(item):
    if item.kind == "flow":
        return lambda budget: run_flow(item.ref, budget)
    if item.kind == "task":
        return lambda budget: run_task(item.ref)
    return None   # 未知类型 → 记失败
```

这样队列不知道任务怎么跑，只知道「这一项有没有成功、花了多久」。

预算分两层：

- **队列总预算**（``total_budget_seconds``）：累计用时超了就**不再开始新项**，
  剩下的记成「跳过：总预算已用完」；
- **单项预算**（``item.budget_minutes``）：流程/方案类会真的被它打断（执行器收得到），
  任务类没法中途打断 —— 所以用 ``estimate(ref)`` 预估：**剩余时间不够就不开始**，
  而不是等它跑完再标一个「超预算」。
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from src.core import run_state
from src.core.queue.model import QueueItem, TaskQueue

Resolver = Callable[[QueueItem], Callable[[float | None], bool] | None]
#: 预估「这个任务大概要跑多久」（秒）；拿不到就返回 0 / None
Estimator = Callable[[str], float]

#: 这两类项的预算只能事后核对：它们不收 budget 参数，也没法中途打断
NON_INTERRUPTIBLE_KINDS = ("task", "assist")


@dataclass
class QueueItemResult:
    item: QueueItem
    ok: bool = False
    skipped: bool = False
    elapsed: float = 0.0
    error: str | None = None
    budget_exceeded: bool = False

    def summary(self) -> str:
        if self.skipped:
            reason = f"：{self.error}" if self.error else ""
            return f"跳过 {self.item.label}{reason}"
        state = "完成" if self.ok else "失败"
        text = f"{state} {self.item.label}（{self.elapsed:.1f}s）"
        if self.budget_exceeded:
            text += "，超出预算"
        if self.error:
            text += f"：{self.error}"
        return text


@dataclass
class QueueRunResult:
    ok: bool = False
    results: list[QueueItemResult] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def completed(self) -> int:
        return sum(1 for result in self.results if result.ok and not result.skipped)

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if not result.ok and not result.skipped)

    def summary(self) -> str:
        state = "队列完成" if self.ok else "队列中断"
        return (
            f"{state}：成功 {self.completed} 项，失败 {self.failed} 项，"
            f"用时 {self.elapsed:.1f}s"
        )


class QueueRunner:
    """顺序执行队列项，按 ``stop_on_failure`` 决定失败后是否继续。"""

    def __init__(
        self,
        queue: TaskQueue,
        resolver: Resolver,
        log: Callable[[str], None] | None = None,
        clock: Callable[[], float] | None = None,
        total_budget_seconds: float | None = None,
        estimate: Estimator | None = None,
    ):
        self.queue = queue
        self.resolver = resolver
        self._log = log
        self._clock = clock or time.monotonic
        self.total_budget_seconds = (
            max(0.0, float(total_budget_seconds)) if total_budget_seconds else None
        )
        self._estimate = estimate

    def run(self) -> QueueRunResult:
        result = QueueRunResult(ok=True)
        started = self._clock()
        # 把队列进度暴露给悬浮窗（结束/异常都要清，否则悬浮窗会一直显示旧队列）
        with contextlib.suppress(Exception):
            run_state.set_queue(self.queue.name, self.queue.items, run_log=self._run_log)

        for index, item in enumerate(self.queue.items):
            if not item.enabled:
                result.results.append(QueueItemResult(item=item, skipped=True))
                self._info(f"跳过未启用项 {item.label}")
                continue

            remaining = self._remaining(started)
            if remaining is not None and remaining <= 0:
                result.results.append(
                    QueueItemResult(item=item, skipped=True, error="总预算已用完")
                )
                self._info(f"跳过 {item.label}：总预算已用完")
                continue

            skip_reason = self._skip_reason(item, remaining)
            if skip_reason:
                result.results.append(QueueItemResult(item=item, skipped=True, error=skip_reason))
                self._info(f"跳过 {item.label}：{skip_reason}")
                continue

            runner = self._resolve(item, result)
            if runner is None:
                continue

            budget = self._effective_budget(item, remaining)
            with contextlib.suppress(Exception):
                run_state.start_item(index)
            item_started = self._clock()
            error = None
            ok = False
            try:
                ok = bool(runner(budget))
            except Exception as exc:  # 单项异常不该炸掉整条队列
                ok = False
                error = str(exc)
            elapsed = self._clock() - item_started
            budget_exceeded = budget is not None and elapsed > budget

            item_result = QueueItemResult(
                item=item,
                ok=ok,
                elapsed=elapsed,
                error=error,
                budget_exceeded=budget_exceeded,
            )
            result.results.append(item_result)
            with contextlib.suppress(Exception):
                run_state.finish_item(index, "完成" if ok else "失败")
            self._info(item_result.summary())

            if not ok:
                result.ok = False
                if self.queue.stop_on_failure:
                    self._info("队列设置为失败即停，剩余项不再执行")
                    break

        result.elapsed = self._clock() - started
        return result

    # ── 预算 ─────────────────────────────────────────────
    def _remaining(self, started: float) -> float | None:
        """距离总预算还剩多少秒；没设总预算返回 ``None``（不限制）。"""
        if self.total_budget_seconds is None:
            return None
        return self.total_budget_seconds - (self._clock() - started)

    def _effective_budget(self, item: QueueItem, remaining: float | None) -> float | None:
        """传给执行器的预算：单项预算与「总预算剩余」取更小的那个。"""
        item_budget = item.budget_seconds
        if remaining is None:
            return item_budget
        if item_budget is None:
            return remaining
        return min(item_budget, remaining)

    def _skip_reason(self, item: QueueItem, remaining: float | None) -> str:
        """任务类项没法中途打断：剩余时间不够就不开始。"""
        if remaining is None or self._estimate is None:
            return ""
        if item.kind not in NON_INTERRUPTIBLE_KINDS:
            return ""
        try:
            expected = float(self._estimate(item.ref) or 0.0)
        except Exception:
            return ""
        if expected <= 0 or expected <= remaining:
            return ""
        return f"预算不足（预计需要 {expected / 60:.0f} 分钟，只剩 {remaining / 60:.0f} 分钟）"

    def _resolve(self, item: QueueItem, result: QueueRunResult):
        try:
            runner = self.resolver(item)
        except Exception as exc:
            runner = None
            reason = f"解析队列项失败：{exc}"
        else:
            reason = f"找不到队列项的实现：{item.kind}:{item.ref}"

        if runner is None:
            result.ok = False
            result.results.append(QueueItemResult(item=item, ok=False, error=reason))
            self._info(reason)
            return None
        return runner

    def _info(self, message: str) -> None:
        if self._log:
            with contextlib.suppress(Exception):
                self._log(message)
