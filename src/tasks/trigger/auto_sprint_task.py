"""自动冲刺触发式任务。

当游戏窗口位于前台且键盘上 W / A / S / D 任意按键持续按下满 0.2 秒后，
按配置的「冲刺间隔 (ms)」周期性按下 Shift 键。

为什么不只依赖框架调度
---------------------
触发任务由执行器轮询驱动，一旦某个触发任务（例如跳过剧情）在 ``self.loop``
里长时间独占执行器线程，本任务的 ``run()`` 就没有机会被调用，表现为
「按住了但不冲刺、触发率很低」。因此本任务除了保留 ``run()`` 供框架调度，
还支持一个独立的工作线程：由它按固定间隔自行检查按键状态并发送 Shift，
不依赖框架轮询节奏。线程默认开启，可用配置项「使用独立线程」关闭。
"""

from __future__ import annotations

import math
import threading
import time

import win32api
import win32gui
from ok import Logger, TriggerTask

from src.core.base_game_task import BaseGameTask
from src.icons import Icons

logger = Logger.get_logger(__name__)

INTERVAL_CONFIG_KEY = "冲刺间隔 (ms)"
DEFAULT_SPRINT_INTERVAL_MS = 620
MIN_SPRINT_INTERVAL_MS = 10
MAX_SPRINT_INTERVAL_MS = 10000

USE_THREAD_CONFIG_KEY = "使用独立线程"
SHIFT_MODE_CONFIG_KEY = "Shift冲刺模式"
THREAD_POLL_SECONDS = 0.01  # 独立线程检查按键状态的间隔
SHIFT_HOLD_EDGE_SECONDS = 0.02  # Shift 模式制造按键边沿时抬起多久（秒）

SPRINT_KEY = "shift"
SHIFT_VK_CODES = (0x10, 0xA0, 0xA1)  # Shift, LShift, RShift
START_DELAY_SECONDS = 0.2  # 按下 WASD 后等待此时长（秒）再开始冲刺
KEY_DOWN_TIME = 0.02  # 单次按键按下时长（秒），过短时按间隔一半限幅
WASD_VK_CODES = (0x57, 0x41, 0x53, 0x44)  # W, A, S, D


class AutoSprintTask(BaseGameTask, TriggerTask):
    """自动冲刺触发式任务：持续按下 WASD 任意键满 0.2 秒后按设定间隔自动按 Shift。"""

    needs_frame = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动冲刺"
        self.description = "当键盘按住 W/A/S/D 任意按键满 0.2 秒后，按设定间隔自动按下 Shift 冲刺。"
        self.icon = Icons.Trigger
        self.trigger_interval = 0

        self.default_config.update({
            "冲刺间隔 (ms)": DEFAULT_SPRINT_INTERVAL_MS,
            USE_THREAD_CONFIG_KEY: True,
            SHIFT_MODE_CONFIG_KEY: False,
        })
        self.config_description.update({
            "冲刺间隔 (ms)": "按下 W/A/S/D 时自动按 Shift 冲刺的间隔时间（毫秒）。",
            USE_THREAD_CONFIG_KEY: "使用独立线程按固定节奏触发冲刺，不依赖框架轮询；其他触发任务长时间占住执行器时仍然生效。",
            SHIFT_MODE_CONFIG_KEY: (
                "开启后不再要求按住 W/A/S/D：只要按住 Shift 就按设定间隔持续冲刺，"
                "与 DNF 类连发脚本一致（每次先抬起再按下，制造新的按键边沿）。"
            ),
        })
        self.config_type.update({
            "冲刺间隔 (ms)": {
                "min": MIN_SPRINT_INTERVAL_MS,
                "max": MAX_SPRINT_INTERVAL_MS,
                "step": 10,
            },
        })

        self._wasd_pressed_since: float | None = None
        self._last_sprint_at: float | None = None
        self._key_lock = threading.RLock()
        self._held_interaction = None
        self._stop_event = threading.Event()
        self._destroyed = False
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    # ── 状态判定 ────────────────────────────────────────────

    def _is_key_pressed(self, vk: int) -> bool:
        """查询虚拟键当前是否处于物理按下状态（忽略历史触发位）。"""
        return bool(win32api.GetAsyncKeyState(vk) & 0x8000)

    def _is_wasd_pressed(self) -> bool:
        return any(self._is_key_pressed(vk) for vk in WASD_VK_CODES)

    def _shift_mode(self) -> bool:
        if self.config is None:
            return False
        return bool(self.config.get(SHIFT_MODE_CONFIG_KEY, False))

    def _is_shift_pressed(self) -> bool:
        return any(self._is_key_pressed(vk) for vk in SHIFT_VK_CODES)

    def _is_game_foreground(self) -> bool:
        device_manager = getattr(getattr(self, "executor", None), "device_manager", None)
        hwnd_window = getattr(device_manager, "hwnd_window", None)
        if hwnd_window is None:
            return False
        is_fg = getattr(hwnd_window, "is_foreground", None)
        if callable(is_fg):
            return bool(is_fg())
        hwnd = getattr(hwnd_window, "hwnd", 0)
        return bool(hwnd and win32gui.GetForegroundWindow() == hwnd)

    def _can_input(self) -> bool:
        executor = getattr(self, "executor", None)
        if executor is None:
            return False
        exit_event = getattr(executor, "exit_event", None)
        return (
            bool(self.enabled)
            and not self._destroyed
            and not self._stop_event.is_set()
            and not self.paused
            and not getattr(executor, "paused", False)
            and not (exit_event is not None and exit_event.is_set())
            and getattr(executor, "interaction", None) is not None
            and self._is_game_foreground()
        )

    def _sprint_interval_seconds(self) -> float:
        raw = (
            self.config.get(INTERVAL_CONFIG_KEY, DEFAULT_SPRINT_INTERVAL_MS)
            if self.config is not None
            else DEFAULT_SPRINT_INTERVAL_MS
        )
        try:
            interval_ms = float(raw)
        except (TypeError, ValueError):
            interval_ms = float(DEFAULT_SPRINT_INTERVAL_MS)
        if not math.isfinite(interval_ms) or interval_ms <= 0:
            interval_ms = float(DEFAULT_SPRINT_INTERVAL_MS)
        return max(float(MIN_SPRINT_INTERVAL_MS), interval_ms) / 1000.0

    # ── 按键操作 ────────────────────────────────────────────

    def _release_shift(self) -> None:
        """安全释放已按下的 Shift，不经 BaseTask.send_key_up 以免停用后被 check_enabled 阻断。"""
        with self._key_lock:
            interaction = self._held_interaction
            if interaction is None:
                return
            self._held_interaction = None
            try:
                interaction.send_key_up(SPRINT_KEY)
            except Exception:
                logger.exception("释放冲刺按键 %s 失败", SPRINT_KEY)

    def _press_shift(self, down_time: float) -> bool:
        try:
            with self._key_lock:
                if not self._can_input():
                    return False
                interaction = self.executor.interaction
                if interaction.send_key_down(SPRINT_KEY, activate=False) is False:
                    return False
                self._held_interaction = interaction
            if down_time > 0:
                self._stop_event.wait(down_time)
            return True
        finally:
            self._release_shift()

    def _pulse_shift_edge(self) -> bool:
        """Shift 冲刺模式：先抬起再按下，制造一次新的按键边沿。"""
        try:
            with self._key_lock:
                if not self._can_input():
                    return False
                interaction = self.executor.interaction
                # 物理 Shift 已被按住时，直接 down 不会产生新边沿，必须先 up。
                try:
                    interaction.send_key_up(SPRINT_KEY)
                except Exception:
                    logger.exception("抬起冲刺按键 %s 失败", SPRINT_KEY)
                self._held_interaction = None
                if self._stop_event.wait(SHIFT_HOLD_EDGE_SECONDS):
                    return False
                if not self._can_input():
                    return False
                if interaction.send_key_down(SPRINT_KEY, activate=False) is False:
                    return False
                self._held_interaction = interaction
            return True
        finally:
            self._release_shift()

    # ── 生命周期与主逻辑 ────────────────────────────────────

    def run(self):
        if not self._can_input():
            self._wasd_pressed_since = None
            return
        # 框架调度到来时顺带确保独立线程在跑（配置中途打开也能生效）。
        self._ensure_worker()
        if self._shift_mode():
            self._run_shift_mode(self.active_time())
            return
        if not self._is_wasd_pressed():
            self._wasd_pressed_since = None
            return

        now = self.active_time()
        if self._wasd_pressed_since is None:
            self._wasd_pressed_since = now
            return
        if now - self._wasd_pressed_since < START_DELAY_SECONDS:
            return

        interval = self._sprint_interval_seconds()
        if self._last_sprint_at is not None and now - self._last_sprint_at < interval:
            return

        down_time = min(KEY_DOWN_TIME, interval * 0.5)
        if self._press_shift(down_time) and not self._stop_event.is_set():
            self._last_sprint_at = now

    def _run_shift_mode(self, now: float) -> None:
        """Shift 冲刺模式：按住 Shift 时按间隔持续制造新的按键边沿。"""
        if not self._is_shift_pressed():
            self._wasd_pressed_since = None
            return
        if self._wasd_pressed_since is None:
            self._wasd_pressed_since = now
            return
        if now - self._wasd_pressed_since < START_DELAY_SECONDS:
            return
        interval = self._sprint_interval_seconds()
        if self._last_sprint_at is not None and now - self._last_sprint_at < interval:
            return
        if self._pulse_shift_edge() and not self._stop_event.is_set():
            self._last_sprint_at = now

    # ── 独立线程 ────────────────────────────────────────────

    def _thread_enabled(self) -> bool:
        if self.config is None:
            return True
        return bool(self.config.get(USE_THREAD_CONFIG_KEY, True))

    def _worker_loop(self) -> None:
        """按固定节奏自行检查按键并发送 Shift，不依赖框架轮询。"""
        while not self._stop_event.is_set():
            try:
                self._sprint_tick()
            except Exception:
                logger.exception("自动冲刺独立线程异常")
            self._stop_event.wait(THREAD_POLL_SECONDS)
        self._release_shift()

    def _sprint_tick(self) -> None:
        """独立线程的一次判定与动作，语义与 ``run()`` 一致。"""
        now = time.monotonic()
        if not self._can_input():
            self._wasd_pressed_since = None
            return
        if self._shift_mode():
            self._run_shift_mode(now)
            return
        if not self._is_wasd_pressed():
            self._wasd_pressed_since = None
            return
        if self._wasd_pressed_since is None:
            self._wasd_pressed_since = now
            return
        if now - self._wasd_pressed_since < START_DELAY_SECONDS:
            return
        interval = self._sprint_interval_seconds()
        if self._last_sprint_at is not None and now - self._last_sprint_at < interval:
            return
        down_time = min(KEY_DOWN_TIME, interval * 0.5)
        if self._press_shift(down_time) and not self._stop_event.is_set():
            self._last_sprint_at = now

    def _ensure_worker(self) -> None:
        if not self._thread_enabled():
            self._stop_worker()
            return
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            if self._stop_event.is_set() or self._destroyed:
                return
            self._worker = threading.Thread(
                target=self._worker_loop, name="ok-ap-auto-sprint", daemon=True
            )
            self._worker.start()

    def _stop_worker(self) -> None:
        with self._worker_lock:
            worker = self._worker
            self._worker = None
        if worker is None or worker is threading.current_thread():
            return
        worker.join(timeout=1)

    def enable(self):
        with self._key_lock:
            self._stop_event.clear()
            self._wasd_pressed_since = None
            self._last_sprint_at = None
        super().enable()
        self._ensure_worker()

    def disable(self):
        with self._key_lock:
            self._stop_event.set()
            self._wasd_pressed_since = None
            self._last_sprint_at = None
            self._release_shift()
        self._stop_worker()
        super().disable()

    def on_destroy(self):
        with self._key_lock:
            self._destroyed = True
            self._stop_event.set()
            self._wasd_pressed_since = None
            self._last_sprint_at = None
            self._release_shift()
        self._stop_worker()
        super().on_destroy()
