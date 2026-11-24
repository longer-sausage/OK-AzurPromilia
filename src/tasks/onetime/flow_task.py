"""流程 / 队列任务：把 JSON 流程或整条队列当成一个普通任务来跑。

为什么做成任务而不是「GUI 里点一下就跑」：ok-script 的任务有暂停/停止、
调度、日志、异常处理与账号上下文，绕开它们直接在界面线程里发键鼠很危险。
所以这里只提供**执行入口**，编辑/校验放在「流程与队列」页。

配置项：

- `任务类型`：执行流程 / 执行队列
- `流程文件` / `队列文件`：名字（对应 `configs/flows/<名字>.json`、`configs/queues/<名字>.json`）
- `从断点继续`：仅流程；按顶层步骤跳过已完成的步骤
- `失败后继续`：单步/单项失败后是否继续后面的步骤（队列按自己的 `stop_on_failure`）
- `预算(分钟)`：0 表示不限；流程会真的被预算打断，任务类只能事后标记超预算
"""

from __future__ import annotations

import contextlib
import time

from src.core.base_game_task import BaseGameTask
from src.core.flow import (
    CheckpointStore,
    Flow,
    FlowContext,
    FlowExecutor,
    FlowFormatError,
    list_flows,
    load_flow_by_name,
)
from src.core.ledger import RunLog
from src.core.queue import QueueFormatError, QueueRunner, list_queues, load_queue
from src.core.replay import SchemeFormatError, list_schemes, load_scheme
from src.icons import Icons

JOB_FLOW = "执行流程"
JOB_QUEUE = "执行队列"
DEFAULT_BUDGET_MINUTES = 30.0


class FlowTask(BaseGameTask):
    """执行 JSON 流程或任务队列。"""

    support_schedule_task = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "流程 / 队列"
        self.icon = Icons.Task
        self.description = (
            "按 JSON 流程逐步操作游戏，或按队列依次执行多个流程/任务。"
            "流程与队列文件在「流程与队列」页里管理。"
        )
        self.default_config = {
            "任务类型": JOB_FLOW,
            "流程文件": "",
            "队列文件": "",
            "方案文件": "",
            "从断点继续": True,
            "失败后继续": False,
            "预算(分钟)": DEFAULT_BUDGET_MINUTES,
        }
        self.config_type = {
            "任务类型": {"type": "drop_down", "options": [JOB_FLOW, JOB_QUEUE]},
        }
        self.config_description = {
            "任务类型": "执行流程：跑一份 JSON 流程；执行队列：按顺序跑队列里的每一项",
            "流程文件": "configs/flows 下的文件名（不带 .json）",
            "队列文件": "configs/queues 下的文件名（不带 .json）",
            "方案文件": "configs/schemes 下的轮椅方案文件名；单独跑方案时填它",
            "从断点继续": "只对流程生效：跳过上次已经跑完的顶层步骤",
            "失败后继续": "流程里某一步失败后是否继续后面的步骤",
            "预算(分钟)": "0 表示不限；流程会被预算打断，任务类只能事后标记",
        }

    # ── 入口 ─────────────────────────────────────────────
    def run(self):
        """顶层入口：跑流程或队列，并把这一次的成败与用时写进运行报表。

        报表数据同时喂给任务栏进度与 HUD 的「预计剩余」——之前只有日常任务会写，
        流程/队列跑完在报表里查不到，进度条也永远估不出来。
        """
        started = self._clock()
        name = self._job_name()
        try:
            result = self._execute_job()
        except Exception as exc:
            self._record_run(name, False, started, note=f"{type(exc).__name__}: {exc}")
            raise
        ok = bool(result)
        self._record_run(name, ok, started, note="" if ok else "任务返回 False")
        return result

    def _execute_job(self) -> bool:
        job = str(self.config.get("任务类型", JOB_FLOW) or JOB_FLOW)
        budget = self._budget_seconds()
        if job == JOB_QUEUE:
            return self.run_queue(budget)
        return self.run_flow(budget)

    def _job_name(self) -> str:
        job = str(self.config.get("任务类型", JOB_FLOW) or JOB_FLOW)
        key = "队列文件" if job == JOB_QUEUE else "流程文件"
        name = str(self.config.get(key, "") or "").strip()
        return name or job

    def _clock(self) -> float:
        """暂停感知的计时（拿不到 active_time 就退回单调时钟）。"""
        try:
            return float(self.active_time())
        except Exception:
            return time.monotonic()

    def _run_log(self) -> RunLog:
        log = getattr(self, "_run_log_instance", None)
        if log is None:
            log = RunLog()
            self._run_log_instance = log
        return log

    def _record_run(self, name: str, ok: bool, started: float, note: str = "") -> None:
        """写报表；失败不影响主流程。"""
        with contextlib.suppress(Exception):
            elapsed = max(0.0, self._clock() - started)
            self._run_log().record(name or "流程/队列", ok, elapsed, note="" if ok else note)

    # ── 流程 ─────────────────────────────────────────────
    def run_flow(self, budget_seconds: float | None = None, name: str | None = None,
                 resume: bool | None = None) -> bool:
        flow_name = name or str(self.config.get("流程文件", "") or "").strip()
        if not flow_name:
            self.log_info(self._available_hint("没有填写流程文件", list_flows()), notify=True)
            return False
        try:
            flow = load_flow_by_name(flow_name)
        except FlowFormatError as exc:
            self.log_info(f"流程文件读取失败：{exc}", notify=True)
            return False

        if resume is None:
            resume = bool(self.config.get("从断点继续", True))
        result = self._execute_flow(flow, budget_seconds, resume=resume)
        if result is None:
            return False
        self.log_info(result.summary(), notify=True)
        return result.ok

    def _execute_flow(self, flow: Flow, budget_seconds: float | None, resume: bool):
        context = FlowContext(self, default_timeout=self.runtime.step_timeout)
        executor = FlowExecutor(
            flow,
            context,
            checkpoint=CheckpointStore().for_flow(flow),
            continue_on_error=bool(self.config.get("失败后继续", False)),
            budget_seconds=budget_seconds,
            default_timeout=self.runtime.step_timeout,
            log=self.log_info,
        )
        try:
            return executor.run(resume=resume)
        except Exception as exc:
            self.mark_task_failure(f"流程执行失败：{exc}", task_name=flow.key or "Flow")
            return None

    # ── 队列 ─────────────────────────────────────────────
    def run_queue(self, budget_seconds: float | None = None, name: str | None = None) -> bool:
        queue_name = name or str(self.config.get("队列文件", "") or "").strip()
        if not queue_name:
            self.log_info(self._available_hint("没有填写队列文件", list_queues()), notify=True)
            return False
        try:
            queue = load_queue(queue_name)
        except QueueFormatError as exc:
            self.log_info(f"队列文件读取失败：{exc}", notify=True)
            return False
        if queue.is_empty():
            self.log_info("队列里没有启用的项", notify=True)
            return False

        result = QueueRunner(
            queue,
            self._queue_resolver(),
            log=self.log_info,
            total_budget_seconds=budget_seconds,
            estimate=self._estimate_task_seconds,
        ).run()
        self.log_info(result.summary(), notify=True)
        return result.ok

    def _estimate_task_seconds(self, ref: str) -> float:
        """按运行报表的历史均值预估某项要跑多久（给队列的预算判断用）。

        报表是按**任务显示名**记的，队列里写的是类名或显示名，所以两种都试一次；
        没有历史数据就返回 0（表示「不知道」，队列就不会因为它跳过）。
        """
        names = [ref]
        for task in (getattr(self.executor, "onetime_tasks", None) or []) + (
            getattr(self.executor, "trigger_tasks", None) or []
        ):
            display = str(getattr(task, "name", "") or "")
            if ref in (task.__class__.__name__, display) and display:
                names.append(display)
                break
        log = self._run_log()
        for name in names:
            try:
                value = float(log.estimate_seconds(name) or 0.0)
            except Exception:
                continue
            if value > 0:
                return value
        return 0.0

    def _queue_resolver(self):
        """把队列项翻译成「跑一下、返回是否成功」的函数。"""

        def resolve(item):
            if item.kind == "flow":
                return lambda budget: self.run_flow(budget, name=item.ref, resume=False)
            if item.kind == "scheme":
                return lambda budget: self.run_scheme(budget, name=item.ref)
            if item.kind in ("task", "assist"):
                return lambda budget: self._run_registered_task(item.kind, item.ref)
            return None

        return resolve

    def run_scheme(self, budget_seconds: float | None = None, name: str | None = None) -> bool:
        """跑一个轮椅方案：拼成流程后交给同一个流程执行器。"""
        scheme_name = name or str(self.config.get("方案文件", "") or "").strip()
        if not scheme_name:
            available = list_schemes()
            hint = f"可选：{'、'.join(available[:8])}" if available else "还没有任何方案文件"
            self.log_info(f"没有填写方案文件；{hint}", notify=True)
            return False
        try:
            scheme = load_scheme(scheme_name)
        except SchemeFormatError as exc:
            self.log_info(f"方案读取失败：{exc}", notify=True)
            return False
        try:
            flow, report = scheme.to_flow()
        except SchemeFormatError as exc:
            self.log_info(f"方案拼装失败：{exc}", notify=True)
            return False

        self.log_info(f"轮椅方案 {scheme_name}：{report.summary()}")
        result = self._execute_flow(flow, budget_seconds, resume=False)
        if result is None:
            return False
        self.log_info(result.summary(), notify=True)
        return result.ok

    def _run_registered_task(self, kind: str, ref: str) -> bool:
        source = getattr(self.executor, "onetime_tasks" if kind == "task" else "trigger_tasks", []) or []
        for task in source:
            class_name = task.__class__.__name__
            display = str(getattr(task, "name", "") or "")
            if ref in (class_name, display):
                self.log_info(f"队列执行任务：{display or class_name}")
                return task.run() is not False
        raise RuntimeError(f"找不到已注册的任务：{kind}:{ref}")

    # ── 工具 ─────────────────────────────────────────────
    def _budget_seconds(self) -> float | None:
        try:
            minutes = float(self.config.get("预算(分钟)", DEFAULT_BUDGET_MINUTES) or 0)
        except (TypeError, ValueError):
            return None
        return minutes * 60 if minutes > 0 else None

    def _available_hint(self, prefix: str, names: list[str]) -> str:
        if not names:
            return f"{prefix}，且还没有任何可用文件"
        return f"{prefix}；可选：{'、'.join(names[:8])}"
