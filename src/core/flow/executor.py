"""流程执行器、上下文适配与断点存储。

执行器不直接依赖 ``BaseGameTask``：它只要求 ``FlowContext`` 提供少量原语
（点击 / 按键 / 移动 / 等待 / 等模板）。这样测试里可以塞一个假上下文，
不需要真机、真实截图或真实输入。

断点续跑的语义：**按顶层步骤计**。每完成一个顶层步骤就写一次进度，
恢复时跳过已完成的顶层步骤 —— 顶层步骤内部的循环/条件重新执行，
因为半途的状态无法可靠重建。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.flow.model import (
    MAX_LOOP_TIMES,
    Flow,
    FlowStep,
    validate_flow,
)

DEFAULT_POLL_INTERVAL = 0.1
DEFAULT_STEP_TIMEOUT = 10.0
PROGRESS_FOLDER = "flow_progress"

#: 流程里 ``{"type": "action", "action": "..."}`` 允许调用的任务方法。
#: 想加新动作就往这里加白名单 —— 流程文件是用户可编辑的 JSON，
#: 不能让任意字符串变成任意方法调用。
#:
#: ⚠️ 名单里每个名字都必须在 ``BaseGameTask`` 上真的存在，否则等用户跑到那一步
#: 才炸（``dodge`` 就这么躺了一阵子：白名单里有、方法没有）。这条由
#: ``tests/test_imports.py`` 守着。
ACTION_ALLOWLIST = frozenset(
    {
        "back",
        "ensure_main",
        "handle_confirm",
        "handle_login",
        "press_combat_key",
        "click_relative",
        "next_frame",
        "sleep",
        "wait_ui_stable",
    }
)

_SAFE_KEY = re.compile(r"[^0-9A-Za-z_.\u4e00-\u9fff-]+")


class FlowError(Exception):
    """流程执行失败。``path`` 指向具体步骤。"""

    def __init__(self, message: str, path: str = "", step_type: str = ""):
        super().__init__(f"{path}: {message}" if path else message)
        self.path = path
        self.step_type = step_type
        self.message = message


class FlowBudgetExceeded(FlowError):
    """超出本次时间预算。

    单独一个类型是因为它**不是步骤失败**：``continue_on_error=True`` 时也不该
    「继续跑剩下的步骤」——剩下的每一步都会立刻再撞上预算，日志刷屏且毫无意义。
    预算用完就整条流程中止，断点停在最后一个**成功**的步骤，下次续跑会重跑被打断的那步。
    """


@dataclass
class FlowRunResult:
    """一次流程运行的结果。"""

    ok: bool = False
    completed: int = 0
    total: int = 0
    resumed_from: int = 0
    failed: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed: float = 0.0
    budget_exceeded: bool = False

    def summary(self) -> str:
        if self.ok:
            return f"流程完成：{self.completed}/{self.total} 步，用时 {self.elapsed:.1f}s"
        if self.budget_exceeded:
            return f"超出时间预算，已中止（已完成 {self.completed}/{self.total} 步）"
        head = f"流程失败于 {self.error}" if self.error else "流程失败"
        return f"{head}（已完成 {self.completed}/{self.total} 步）"


# ── 断点 ─────────────────────────────────────────────────
class CheckpointStore:
    """按流程记录「已完成几个顶层步骤」。"""

    def __init__(self, root: str | Path | None = None):
        if root is None:
            root = Path("configs") / PROGRESS_FOLDER
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        safe = _SAFE_KEY.sub("_", str(key or "flow")).strip("_") or "flow"
        return self.root / f"{safe}.json"

    def for_key(self, key: str, fingerprint: str = "") -> FlowCheckpoint:
        """按任意标识取断点（路线等非流程对象也复用这套存储）。"""
        return FlowCheckpoint(self, str(key or "flow"), fingerprint=fingerprint)

    def for_flow(self, flow: Flow) -> FlowCheckpoint:
        return FlowCheckpoint(self, flow.key, fingerprint=flow_fingerprint(flow))

    def load_record(self, key: str) -> dict | None:
        path = self._path(key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_record(self, key: str, payload: dict) -> bool:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    def clear(self, key: str) -> None:
        with contextlib.suppress(OSError):
            self._path(key).unlink(missing_ok=True)


@dataclass
class FlowCheckpoint:
    """绑定到某个流程的断点。

    ``fingerprint`` 是「步骤结构」的摘要：流程/路线被改过之后，旧的 ``completed``
    已经不可信（可能跳过刚插进去的新步骤，或者跳过完全不同的一步），
    这时直接当作没有断点，从头开始跑，并在日志里说明。
    """

    store: CheckpointStore
    key: str
    fingerprint: str = ""

    def load(self) -> int:
        record = self.store.load_record(self.key)
        if not record:
            return 0
        if not self._same_content(record):
            self.clear()
            return 0
        try:
            return max(0, int(record.get("completed", 0)))
        except (TypeError, ValueError):
            return 0

    def _same_content(self, record: dict) -> bool:
        """结构变了就不认旧断点；老断点没有 fingerprint 字段时按兼容处理。"""
        if not self.fingerprint:
            return True
        stored = str(record.get("fingerprint", "") or "")
        if not stored:
            return True
        return stored == self.fingerprint
    def save(self, completed: int, total: int) -> bool:
        payload = {
            "flow": self.key,
            "completed": int(completed),
            "total": int(total),
            "updated_at": time.time(),
        }
        if self.fingerprint:
            payload["fingerprint"] = self.fingerprint
        return self.store.save_record(self.key, payload)

    def clear(self) -> None:
        self.store.clear(self.key)


# ── 上下文 ───────────────────────────────────────────────
class FlowContext:
    """把 ``BaseGameTask``（或任意同类对象）适配成流程原语。"""

    def __init__(self, task, default_timeout: float = DEFAULT_STEP_TIMEOUT,
                 poll_interval: float = DEFAULT_POLL_INTERVAL):
        self.task = task
        self.default_timeout = float(default_timeout or DEFAULT_STEP_TIMEOUT)
        self.poll_interval = max(0.01, float(poll_interval or DEFAULT_POLL_INTERVAL))

    # 时间
    def now(self) -> float:
        active_time = getattr(self.task, "active_time", None)
        if callable(active_time):
            try:
                return float(active_time())
            except Exception:
                pass
        return time.monotonic()

    # 输入
    def click(self, x: float, y: float) -> None:
        click_relative = getattr(self.task, "click_relative", None)
        if callable(click_relative):
            click_relative(float(x), float(y))
            return
        self.task.click(float(x), float(y))

    def click_hold(self, x: float, y: float, hold: float) -> None:
        """按住 ``hold`` 秒再松开（长按 / 蓄力）。

        框架有 ``mouse_down`` / ``mouse_up``，但它们收的是绝对坐标，
        所以这里用任务的 ``width`` / ``height`` 把比例坐标换算过去；
        拿不到尺寸就退回「普通点击 + 等待」，并在日志里说明——**不静默改变语义**。
        """
        down = getattr(self.task, "mouse_down", None)
        up = getattr(self.task, "mouse_up", None)
        width = getattr(self.task, "width", 0) or 0
        height = getattr(self.task, "height", 0) or 0
        if not (callable(down) and callable(up)) or width <= 0 or height <= 0:
            self.click(float(x), float(y))
            self.sleep(max(0.0, float(hold)))
            self.log_fallback(f"任务不支持按住鼠标，{hold:g}s 长按退化为「点击 + 等待」")
            return
        down(round(float(x) * width), round(float(y) * height))
        self.sleep(max(0.0, float(hold)))
        up()

    def log_fallback(self, message: str) -> None:
        """能力缺失时的说明通道（有 task.log_info 就用它）。"""
        logger = getattr(self.task, "log_info", None)
        if callable(logger):
            with contextlib.suppress(Exception):
                logger(message)

    def key(self, key: str, duration: float = 0.0) -> None:
        press_key = getattr(self.task, "press_key", None)
        if callable(press_key):
            press_key(key, down_time=float(duration or 0.02))
            return
        self.task.send_key(key, down_time=float(duration or 0.02))

    def move(self, keys: str, duration: float = 0.0) -> None:
        move_keys = getattr(self.task, "move_keys", None)
        if callable(move_keys):
            move_keys(keys, float(duration or 0.0))
            return
        raise FlowError("当前任务不支持 move（缺少 move_keys）")

    def mouse_delta(self, dx: float, dy: float) -> None:
        """相对鼠标位移（转视角）：走 runtime_mixin 的 active_and_send_mouse_delta。"""
        send_delta = getattr(self.task, "active_and_send_mouse_delta", None)
        if callable(send_delta):
            send_delta(float(dx), float(dy))
            return
        raise FlowError("当前任务不支持鼠标相对位移（缺少 active_and_send_mouse_delta）")

    def sleep(self, seconds: float) -> None:
        if seconds and seconds > 0:
            self.task.sleep(seconds)

    # 识别
    def template_box(self, name: str, threshold: float | None = None):
        """返回识别到的 box（未命中为 None）。"""
        find_one = getattr(self.task, "find_one", None)
        if not callable(find_one):
            raise FlowError("当前任务不支持模板识别（缺少 find_one）")
        kwargs: dict[str, Any] = {}
        if threshold is not None:
            kwargs["threshold"] = float(threshold)
        return find_one(feature=name, **kwargs)

    def template_hit(self, name: str, threshold: float | None = None) -> bool:
        return bool(self.template_box(name, threshold))

    # 动作
    def action(self, name: str, args: dict | None = None) -> Any:
        if name not in ACTION_ALLOWLIST:
            raise FlowError(f"动作 {name!r} 不在白名单里")
        method = getattr(self.task, name, None)
        if not callable(method):
            raise FlowError(f"当前任务没有动作 {name!r}")
        return method(**(args or {}))


# ── 执行器 ───────────────────────────────────────────────
class FlowExecutor:
    """按顺序执行顶层步骤；``resume=True`` 时从断点继续。"""

    def __init__(
        self,
        flow: Flow,
        context: FlowContext,
        checkpoint: FlowCheckpoint | None = None,
        continue_on_error: bool = False,
        budget_seconds: float | None = None,
        default_timeout: float | None = None,
        log: Callable[[str], None] | None = None,
        max_loops: int = MAX_LOOP_TIMES,
    ):
        self.flow = flow
        self.context = context
        self.checkpoint = checkpoint
        self.continue_on_error = bool(continue_on_error)
        self.budget_seconds = float(budget_seconds) if budget_seconds else None
        self.default_timeout = float(
            default_timeout if default_timeout is not None else context.default_timeout
        )
        self.max_loops = max(1, int(max_loops or MAX_LOOP_TIMES))
        self._log = log
        self._started = 0.0

    # ── 入口 ─────────────────────────────────────────────
    def run(self, resume: bool = False) -> FlowRunResult:
        issues = [issue for issue in validate_flow(self.flow) if issue.level == "error"]
        if issues:
            raise FlowError("流程校验未通过：" + "；".join(str(issue) for issue in issues))

        steps = self.flow.steps
        total = len(steps)
        start = 0
        if resume and self.checkpoint is not None:
            start = min(self.checkpoint.load(), total)

        result = FlowRunResult(ok=True, total=total, resumed_from=start, completed=start)
        self._started = self.context.now()
        if start:
            self._info(f"从断点继续：跳过前 {start} 个顶层步骤")

        for index in range(start, total):
            step = steps[index]
            try:
                self._check_budget(step)
                self._run_step(step)
            except FlowBudgetExceeded as exc:
                # 预算不是「某一步失败」：整条流程立刻中止，不看 continue_on_error
                result.ok = False
                result.budget_exceeded = True
                result.error = str(exc)
                self._info(f"超出时间预算，流程中止（已完成 {result.completed}/{total} 步）")
                break
            except Exception as exc:  # 单步失败：按策略决定继续或中止
                result.ok = False
                result.failed.append(getattr(exc, "path", step.path) or step.path)
                result.error = str(exc)
                self._info(f"步骤失败 {result.error}")
                if not self.continue_on_error:
                    break
                continue

            result.completed = index + 1
            if self.checkpoint is not None:
                self.checkpoint.save(result.completed, total)

        if result.ok and self.checkpoint is not None and result.completed >= total:
            # 全部跑完就没有断点可续了，清掉避免下次误跳过
            self.checkpoint.clear()
        result.elapsed = self.context.now() - self._started
        return result

    # ── 步骤分发 ─────────────────────────────────────────
    def _run_step(self, step: FlowStep) -> None:
        handler = getattr(self, f"_step_{step.type}", None)
        if handler is None:
            raise FlowError(f"不支持的步骤类型 {step.type}", path=step.path, step_type=step.type)
        handler(step)

    def _step_move(self, step: FlowStep) -> None:
        if step.direction:
            self.context.move(step.direction.lower(), step.duration)
            return
        self.context.mouse_delta(step.dx or 0.0, step.dy or 0.0)

    def _step_click(self, step: FlowStep) -> None:
        times = max(1, int(step.times or 1))
        for _ in range(times):
            if step.template:
                reached, box = self._wait_state(step.template, True, step.timeout, step.threshold)
                if not reached:
                    raise FlowError(f"点击目标 {step.template} 未出现", path=step.path, step_type="click")
                x, y = self._click_point_of(box, fallback=(0.5, 0.5))
            else:
                x, y = step.x or 0.0, step.y or 0.0
            if step.hold:
                self.context.click_hold(x, y, step.hold)
            else:
                self.context.click(x, y)
            if step.duration:
                self.context.sleep(step.duration)

    def _click_point_of(self, box, fallback: tuple[float, float]) -> tuple[float, float]:
        """把识别到的 box 中心换算成比例坐标（任务拿不到尺寸时回退）。"""
        center = getattr(box, "center", None)
        width = getattr(self.context.task, "width", 0) or 0
        height = getattr(self.context.task, "height", 0) or 0
        if not callable(center) or width <= 0 or height <= 0:
            return fallback
        try:
            cx, cy = center()
        except Exception:
            return fallback
        return cx / width, cy / height

    def _step_action(self, step: FlowStep) -> None:
        self.context.action(step.action or "", dict(step.args))

    def _step_key(self, step: FlowStep) -> None:
        times = max(1, int(step.times or 1))
        for _ in range(times):
            self.context.key(step.key or "", step.duration)

    def _step_wait(self, step: FlowStep) -> None:
        self._sleep_within_budget(max(0.0, float(step.seconds or 0.0)))

    def _step_wait_template(self, step: FlowStep) -> None:
        want = step.expect != "disappear"
        reached, _box = self._wait_state(step.template or "", want, step.timeout, step.threshold)
        if not reached:
            target = "出现" if want else "消失"
            raise FlowError(
                f"等待模板 {step.template} {target}超时", path=step.path, step_type="wait_template"
            )
        if step.settle and step.settle > 0:
            self._settle(step)

    def _settle(self, step: FlowStep) -> None:
        """要求目标状态连续保持 ``settle`` 秒。"""
        want = step.expect != "disappear"
        deadline = self.context.now() + float(step.settle)
        while self.context.now() < deadline:
            if self.context.template_hit(step.template or "", step.threshold) != want:
                raise FlowError(
                    f"模板 {step.template} 在稳定确认期间状态翻转", path=step.path, step_type="wait_template"
                )
            self.context.sleep(self.context.poll_interval)

    def _step_loop(self, step: FlowStep) -> None:
        if step.times is not None:
            for _ in range(int(step.times)):
                self._run_children(step)
            return

        if step.until_template:
            limit = int(step.max_times or self.max_loops)
            for _ in range(limit):
                if self.context.template_hit(step.until_template):
                    return
                self._run_children(step)
            raise FlowError(
                f"循环等待 {step.until_template} 出现达到上限 {limit} 次", path=step.path, step_type="loop"
            )

        if step.until_not_template:
            limit = int(step.max_times or self.max_loops)
            for _ in range(limit):
                if not self.context.template_hit(step.until_not_template):
                    return
                self._run_children(step)
            raise FlowError(
                f"循环等待 {step.until_not_template} 消失达到上限 {limit} 次", path=step.path, step_type="loop"
            )

        raise FlowError("循环缺少条件", path=step.path, step_type="loop")

    def _step_if(self, step: FlowStep) -> None:
        hit = self.context.template_hit(step.template or "", step.threshold)
        branch = step.steps if hit else step.else_steps
        for child in branch:
            self._check_budget(child)
            self._run_step(child)

    def _run_children(self, step: FlowStep) -> None:
        for child in step.steps:
            self._check_budget(child)
            self._run_step(child)

    # ── 通用工具 ─────────────────────────────────────────
    def _wait_state(self, name: str, want: bool, timeout: float | None,
                    threshold: float | None = None) -> tuple[bool, Any]:
        """等到「模板存在状态 == want」。

        返回 ``(是否达到目标状态, 命中时的 box)``：等出现成功时 box 有值，
        等消失成功时没有 box（也就不存在「用 False 当哨兵」的歧义）。

        轮询过程中也会检查时间预算：否则一个 ``timeout=600`` 的等待会把
        「预算 5 分钟」直接撑到 10 分钟——文档里说「流程会被预算打断」就成了空话。
        """
        deadline = self.context.now() + float(timeout or self.default_timeout)
        while True:
            box = self.context.template_box(name, threshold)
            if bool(box) == want:
                return True, box
            self._check_deadline(name)
            if self.context.now() >= deadline:
                return False, None
            self.context.sleep(self.context.poll_interval)

    def _check_deadline(self, name: str) -> None:
        """只按预算判断（不看步骤），给等待循环用。"""
        if not self.budget_seconds:
            return
        if self.context.now() - self._started > self.budget_seconds:
            raise FlowBudgetExceeded(
                f"超出本次时间预算 {self.budget_seconds:.0f}s（等待 {name} 期间）",
            )

    def _check_budget(self, step: FlowStep) -> None:
        if not self.budget_seconds:
            return
        if self.context.now() - self._started > self.budget_seconds:
            raise FlowBudgetExceeded(
                f"超出本次时间预算 {self.budget_seconds:.0f}s",
                path=step.path,
                step_type=step.type,
            )

    def _sleep_within_budget(self, seconds: float) -> None:
        """等待也受预算约束：睡到预算边界就中止，而不是把整个 5 分钟等完。"""
        if seconds <= 0:
            return
        if not self.budget_seconds:
            self.context.sleep(seconds)
            return
        remaining = self.budget_seconds - (self.context.now() - self._started)
        if remaining <= 0:
            raise FlowBudgetExceeded(
                f"超出本次时间预算 {self.budget_seconds:.0f}s",
            )
        if seconds <= remaining:
            self.context.sleep(seconds)
            return
        self.context.sleep(remaining)
        raise FlowBudgetExceeded(
            f"超出本次时间预算 {self.budget_seconds:.0f}s（等待 {seconds:g}s 期间）",
        )

    def _info(self, message: str) -> None:
        if self._log:
            with contextlib.suppress(Exception):
                self._log(message)


def iter_step_paths(steps: Iterable[FlowStep]) -> Iterable[str]:
    """按先序产出所有步骤路径（调试/UI 用）。"""
    for step in steps:
        yield step.path
        yield from iter_step_paths(step.steps)
        yield from iter_step_paths(step.else_steps)


def structure_fingerprint(payload: Any) -> str:
    """把「结构」压成短摘要：用来判断断点还算不算数。"""
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = repr(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def flow_fingerprint(flow: Flow) -> str:
    """只看步骤结构，不看名字/说明（改个备注不该让断点失效）。"""
    return structure_fingerprint(flow.to_dict().get("steps", []))
