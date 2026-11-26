"""自动冲刺触发式任务。

当游戏窗口位于前台且键盘上 W / A / S / D 任意按键持续按下满 0.2 秒后，
按配置的「冲刺间隔 (ms)」周期性按下 Shift 键。
"""

from __future__ import annotations

import math
import threading

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

SPRINT_KEY = "shift"
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
        })
        self.config_description.update({
            "冲刺间隔 (ms)": "按下 W/A/S/D 时自动按 Shift 冲刺的间隔时间（毫秒）。",
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

    # ── 状态判定 ────────────────────────────────────────────

    def _is_key_pressed(self, vk: int) -> bool:
        """查询虚拟键当前是否处于物理按下状态（忽略历史触发位）。"""
        return bool(win32api.GetAsyncKeyState(vk) & 0x8000)

    def _is_wasd_pressed(self) -> bool:
        return any(self._is_key_pressed(vk) for vk in WASD_VK_CODES)

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

    # ── 生命周期与主逻辑 ────────────────────────────────────

    def run(self):
        if not self._can_input():
            self._wasd_pressed_since = None
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

    def enable(self):
        with self._key_lock:
            self._stop_event.clear()
            self._wasd_pressed_since = None
            self._last_sprint_at = None
        super().enable()

    def disable(self):
        with self._key_lock:
            self._stop_event.set()
            self._wasd_pressed_since = None
            self._last_sprint_at = None
            self._release_shift()
        super().disable()

    def on_destroy(self):
        with self._key_lock:
            self._destroyed = True
            self._stop_event.set()
            self._wasd_pressed_since = None
            self._last_sprint_at = None
            self._release_shift()
        super().on_destroy()
