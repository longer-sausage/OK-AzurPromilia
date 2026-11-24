"""任务运行期能力（策略/卡死/失败帧/自恢复/OCR 分行）——从 ``BaseGameTask`` 抽出来。

为什么抽出来：这些东西原来直接写在 ``src/core/base_game_task.py`` 里
（git diff 显示 +301/−21），等于**改了项目底层基类**。现在它们全部住在这里，
基类只保留上游原样 + 几处一行调用（见 ``base_game_task.py`` 里的 ``agent_*``）。

设计约束：

- 这里的名字基类里**都没有**（``runtime`` / ``ocr_lines`` / ``capture_failure_frame`` …），
  所以用 mixin 组合即可，不会覆盖上游行为；
- 需要「插进」上游方法里的两个钩子（超时缩放、卡死探针）由基类显式调用
  ``agent_scale_timeout()`` / ``agent_tick()``，一行一个，不再改上游逻辑；
- 所有状态（卡死指纹、失败帧库）都用 ``self.__dict__`` 惰性初始化，
  不需要改 ``__init__``。
"""

from __future__ import annotations

import contextlib

from src.config import config as app_config
from src.core.failure_frame_store import FailureFrameStore
from src.core.global_config_store import get_runtime_settings
from src.core.runtime_config import DEFAULT_STEP_TIMEOUT, STUCK_CHECK_INTERVAL, RuntimeSettings
from src.data.page import page_main
from src.image.stability import perceptual_hash


def _runtime_of(task) -> RuntimeSettings:
    """拿策略快照：测试替身可注入 ``_runtime_settings``，其它情况读全局配置。

    注意不要读 ``task.runtime``：mixin 自己的 ``runtime`` 属性就是走这个函数的，
    那样会互相递归（真实踩到过 RecursionError）。
    """
    injected = getattr(task, "_runtime_settings", None)
    if not isinstance(injected, RuntimeSettings):
        # 测试替身常见的另一种注入方式：实例属性 runtime = settings
        injected = task.__dict__.get("runtime")
    if isinstance(injected, RuntimeSettings):
        return injected
    try:
        return get_runtime_settings()
    except Exception:
        return RuntimeSettings()


def _log(task, message: str) -> None:
    """统一的日志出口（对象没有 log_info 时静默）。"""
    logger = getattr(task, "log_info", None)
    if callable(logger):
        with contextlib.suppress(Exception):
            logger(str(message))


def _as_delay(value) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def agent_scale_timeout(task, time_out):
    """模块级钩子：不要求对象是 ``BaseGameTask``，测试替身也能调用。"""
    return _runtime_of(task).resolve_step_timeout(time_out)


def agent_retry_gap(task, check_delay: float = 0.0) -> float:
    """模块级钩子：项目配置与全局「重试间隔」取较大值。"""
    return _runtime_of(task).resolve_retry_gap(check_delay)


def agent_wait_timeout(task, time_out, framework_default: float) -> float:
    """``wait_until`` 的超时解析（与改造前完全一致）：

    ``time_out`` 为 0 时用框架默认值；用户改过「单步超时」就用它覆盖框架值；
    两种都按低配模式缩放。显式传值的同样按低配模式缩放。
    """
    settings = _runtime_of(task)
    if not time_out:
        default = framework_default
        if abs(settings.step_timeout - DEFAULT_STEP_TIMEOUT) > 1e-9:
            default = settings.step_timeout
        return settings.scaled_timeout(default)
    return settings.scaled_timeout(time_out)


def agent_check_stuck(task, now=None):
    """卡死探针的**模块级实现**：任何对象都能用（基类的 loop 会直接调它）。

    时钟优先用调用方传入的 ``now``（基类 loop 已经读过一次）；否则读
    ``active_time()``。测试替身把时钟换成有限 ``side_effect`` 时抛
    ``StopIteration``，在生成器里会变成 RuntimeError，所以这里兜住。
    """
    frame = getattr(task, "frame", None)
    if frame is None:
        return
    if now is None:
        try:
            now = task.active_time()
        except Exception:
            return
    state = task.__dict__
    last = state.get("_last_stuck_check", 0.0)
    if now - last < STUCK_CHECK_INTERVAL:
        return
    state["_last_stuck_check"] = now
    try:
        fingerprint = perceptual_hash(frame)
    except Exception:
        return

    if fingerprint != state.get("_stuck_fingerprint"):
        state["_stuck_fingerprint"] = fingerprint
        state["_stuck_since"] = now
        state["_stuck_warned"] = False
        return

    threshold = _runtime_of(task).resolve_stuck_threshold()
    since = state.get("_stuck_since", now)
    if state.get("_stuck_warned") or (now - since) < threshold:
        return
    state["_stuck_warned"] = True
    message = (
        f"画面已 {now - since:.0f} 秒没有变化（阈值 {threshold:.0f} 秒），"
        "可能卡在加载/弹窗/断线界面，请检查游戏状态"
    )
    with contextlib.suppress(Exception):
        task.log_warning(message, notify=True)


def agent_tick(task, now=None):
    """模块级钩子：每帧的卡死探针（``now`` 可由调用方传入，避免重复读时钟）。"""
    return agent_check_stuck(task, now)


def agent_after_failure(task, message: str = "", task_name: str | None = None):
    """模块级钩子：失败后补失败帧 + 按策略回主菜单。"""
    return AgentMixin.agent_after_failure(task, message, task_name)


class AgentMixin:
    """给任务加「策略 + 卡死 + 失败帧 + 自恢复 + OCR 分行」，不改基类实现。"""

    # ── 全局策略 ─────────────────────────────────────────
    @property
    def runtime(self) -> RuntimeSettings:
        """当前全局策略快照（改完配置下一个任务就生效，不用重启）。"""
        return _runtime_of(self)

    def agent_scale_timeout(self, time_out):
        """把调用方给的超时按策略缩放（低配模式 ×1.5；``None`` 取单步超时）。"""
        return _runtime_of(self).resolve_step_timeout(time_out)

    def agent_retry_gap(self, check_delay: float = 0.0) -> float:
        """条件没满足时该等多久：项目配置与全局「重试间隔」取较大值。"""
        return _runtime_of(self).resolve_retry_gap(check_delay)

    def agent_before_delay(self) -> float:
        return _as_delay(app_config.get("wait_until_before_delay", 0))

    def agent_check_delay(self) -> float:
        return _as_delay(app_config.get("wait_until_check_delay", 0))

    # ── 卡死探针 ─────────────────────────────────────────
    def _check_stuck(self, now=None):
        """每帧调一次：画面长时间不变就告警（实现见模块函数 ``agent_check_stuck``）。"""
        return agent_check_stuck(self, now)

    agent_tick = _check_stuck

    def reset_stuck_detection(self):
        """手动重置卡死基线（切换场景后可调，避免误判）。"""
        self.__dict__["_stuck_fingerprint"] = None
        self.__dict__["_stuck_since"] = 0.0
        self.__dict__["_stuck_warned"] = False
        self.__dict__["_last_stuck_check"] = 0.0

    def sleep_check(self):
        """框架在执行器 sleep 期间会周期性调它（上游没有实现，这里补上探针）。"""
        with contextlib.suppress(Exception):
            super().sleep_check()  # type: ignore[misc]
        with contextlib.suppress(Exception):
            self.agent_tick()

    # ── 失败后自恢复 ──────────────────────────────────────
    def is_main_menu(self) -> bool:
        try:
            return bool(self.ui_page_appear(page_main))
        except Exception:
            return False

    def recover_to_menu(self, attempts: int | None = None, after_sleep: float | None = None) -> bool:
        """失败后按返回键退回主菜单，最多 ``attempts`` 次；只退菜单、不重跑。"""
        settings = self.runtime
        limit = settings.max_recover_attempts if attempts is None else max(0, int(attempts))
        if limit <= 0:
            return self.is_main_menu()
        wait = settings.after_action_wait if after_sleep is None else max(0.0, float(after_sleep))
        if wait <= 0:
            wait = getattr(self, "once_sleep_time", 0.5) or 0.5
        for _ in range(limit):
            if self.is_main_menu():
                return True
            try:
                self.back()
            except Exception:
                return self.is_main_menu()
            if wait > 0:
                self.sleep(wait)
        return self.is_main_menu()

    def recover_after_failure(self, reason: str = "") -> bool:
        settings = self.runtime
        if not settings.auto_recover:
            return False
        suffix = f"：{reason}" if reason else ""
        with contextlib.suppress(Exception):
            self.log_info(f"任务失败，尝试退回主菜单（最多 {settings.max_recover_attempts} 次）{suffix}")
        recovered = self.recover_to_menu()
        with contextlib.suppress(Exception):
            if recovered:
                self.log_info("已退回主菜单，等待下一次手动/触发执行")
            else:
                self.log_warning("未能确认回到主菜单，请手动检查游戏画面")
        return recovered

    # ── 失败帧 ────────────────────────────────────────────
    def failure_store(self) -> FailureFrameStore:
        """失败帧库（惰性创建；保留数量改了会重建，但沿用原目录）。

        调用方（含测试）可以先把 ``_failure_store`` 指到自定义目录，
        之后保留数量变化只会重建到**那个目录**，不会跑到默认位置。
        """
        limit = self.runtime.failure_frame_limit
        store = self.__dict__.get("_failure_store")
        if store is None:
            store = FailureFrameStore(
                limit=limit,
                log=self._log_agent,
                processor=self._privacy_processor_for_frames,
            )
        elif store.limit != limit:
            store = FailureFrameStore(
                root=store.root,
                limit=limit,
                log=self._log_agent,
                processor=self._privacy_processor_for_frames,
            )
        self.__dict__["_failure_store"] = store
        return store

    def _privacy_processor_for_frames(self, frame):
        processor = app_config.get("screenshot_processor")
        if not callable(processor):
            return None
        return processor(frame)

    def _log_agent(self, message: str):
        with contextlib.suppress(Exception):
            self.log_info(message)

    def capture_failure_frame(self, message: str = "", task_name: str | None = None) -> bool:
        """把当前识别帧存进失败帧库（落盘前过 UID 遮挡），供「失败帧复测」使用。"""
        try:
            frame = getattr(self, "frame", None)
            if frame is None:
                return False
            name = task_name or getattr(self, "current_task", None) or self.__class__.__name__
            record = self.failure_store().save(frame, task=str(name), message=str(message or ""))
            if record is not None:
                self._log_agent(f"失败帧已保存: {record.file}")
                return True
        except Exception:
            pass
        return False

    def agent_after_failure(self, message: str = "", task_name: str | None = None) -> None:
        """上游截图之外再补两件事：存失败帧（可复测）、按策略回主菜单。"""
        self.capture_failure_frame(message, task_name)
        with contextlib.suppress(Exception):
            self.recover_after_failure(str(message or ""))

    # ── OCR 文本行 ────────────────────────────────────────
    def ocr_lines(self, box=None, min_confidence: float = 0.0, line_tolerance: float = 0.6) -> list[str]:
        """把 OCR 结果按视觉行输出（同一行的「尤尼」「×1,500」拼回一行）。"""
        try:
            boxes = self.ocr(box=box) if box is not None else self.ocr()
        except Exception as exc:
            _log(self, f"OCR 失败: {exc}")
            return []

        items = []
        for item in boxes or []:
            text = str(getattr(item, "name", "") or "").strip()
            if not text:
                continue
            try:
                confidence = float(getattr(item, "confidence", 1.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 1.0
            if confidence < min_confidence:
                continue
            height = float(getattr(item, "height", 0) or 0)
            items.append(
                (float(getattr(item, "y", 0) or 0), float(getattr(item, "x", 0) or 0), height, text)
            )

        if not items:
            return []
        items.sort()

        heights = [height for _, _, height, _ in items if height > 0]
        typical = sorted(heights)[len(heights) // 2] if heights else 16.0
        tolerance = max(4.0, typical * max(0.1, float(line_tolerance)))

        lines: list[list[tuple[float, str]]] = []
        current_y = None
        current: list[tuple[float, str]] = []
        for y, x, _height, text in items:
            if current_y is None or abs(y - current_y) <= tolerance:
                current.append((x, text))
                current_y = y if current_y is None else (current_y + y) / 2.0
            else:
                lines.append(current)
                current = [(x, text)]
                current_y = y
        if current:
            lines.append(current)
        return ["  ".join(text for _x, text in sorted(line)) for line in lines]


__all__ = ["AgentMixin", "STUCK_CHECK_INTERVAL"]
