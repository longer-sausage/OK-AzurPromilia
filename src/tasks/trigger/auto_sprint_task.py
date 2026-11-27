"""自动冲刺触发式任务。

按住 Shift 时自动「切换角色 + 按 Shift」实现持续快速跑动。

实现参考 ok-nte 的粉爪大劫案（``HeistTask._handle_quick_run``）
--------------------------------------------------------------
关键点是**接管 Shift 的按键所有权**：

1. 用 ``pynput`` 的键盘监听器维护真实物理按键集合，比 ``GetAsyncKeyState``
   可靠（后者受前台窗口与消息队列状态影响）。
2. 进入快速跑动后，用 ``listener.suppress_event()`` 屏蔽物理 Shift 事件，
   并主动释放物理 Shift。这样游戏只会看到程序发出的 Shift，不会出现
   「程序抬起 Shift、但手指仍按着、Windows 不再补发按下」导致的永久失效。
3. 松开物理 Shift 时立即停止并归还控制权。

只靠「抬起 → 按下」模拟边沿而不接管所有权，会出现按着不放却不再冲刺的
现象，这是本任务改用监听器方案的原因。
"""

from __future__ import annotations

import math
import threading
import time

import win32api
import win32con
import win32gui
from ok import Logger, TriggerTask
from pynput import keyboard
from pynput.keyboard import Controller, Key

from src.core.base_game_task import BaseGameTask
from src.icons import Icons

logger = Logger.get_logger(__name__)

INTERVAL_CONFIG_KEY = "冲刺间隔 (ms)"
DEFAULT_SPRINT_INTERVAL_MS = 200
MIN_SPRINT_INTERVAL_MS = 10
MAX_SPRINT_INTERVAL_MS = 10000

USE_THREAD_CONFIG_KEY = "使用独立线程"
SHIFT_MODE_CONFIG_KEY = "Shift冲刺模式"
CHAR_COUNT_CONFIG_KEY = "快速奔跑角色数量"
SWITCH_ENABLED_CONFIG_KEY = "切换角色"
THREAD_POLL_SECONDS = 0.01
CHAR_COUNT_MIN = 1
CHAR_COUNT_MAX = 4

SPRINT_KEY = "shift"
START_DELAY_SECONDS = 0.2
KEY_DOWN_TIME = 0.02
WASD_VK_CODES = (0x57, 0x41, 0x53, 0x44)

#: 按住 Shift 多久后进入快速跑动（秒）
QUICK_RUN_HOLD_SECONDS = 0.5
#: 一次切人后等待多久再补 Shift（秒）
QUICK_RUN_KEY_AFTER_SECONDS = 0.25
#: 切人动作的重试间隔（秒）
QUICK_RUN_RETRY_SECONDS = 0.5
#: 一个「切人 + Shift」周期的长度（秒）
QUICK_RUN_CYCLE_SECONDS = 0.7

KEY_DOWN_MESSAGES = (win32con.WM_KEYDOWN, win32con.WM_SYSKEYDOWN)
KEY_UP_MESSAGES = (win32con.WM_KEYUP, win32con.WM_SYSKEYUP)
SHIFT_KEYS = (win32con.VK_SHIFT, win32con.VK_LSHIFT, win32con.VK_RSHIFT)
PYNPUT_KEY_MAP = {"shift": Key.shift_l, "lshift": Key.shift_l, "rshift": Key.shift_r}


class AutoSprintTask(BaseGameTask, TriggerTask):
    """自动冲刺：按住 Shift 时自动切换角色并按 Shift，保持高速跑动。"""

    needs_frame = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动冲刺"
        self.description = "按住 Shift 时自动切换角色并按 Shift，保持持续快速跑动。"
        self.icon = Icons.Trigger
        self.trigger_interval = 0

        self.default_config.update({
            INTERVAL_CONFIG_KEY: DEFAULT_SPRINT_INTERVAL_MS,
            USE_THREAD_CONFIG_KEY: True,
            SHIFT_MODE_CONFIG_KEY: True,
            SWITCH_ENABLED_CONFIG_KEY: True,
            CHAR_COUNT_CONFIG_KEY: 2,
        })
        self.config_description.update({
            INTERVAL_CONFIG_KEY: "切人后补按 Shift 的间隔时间（毫秒）。",
            USE_THREAD_CONFIG_KEY: "使用独立线程按固定节奏触发，不依赖框架轮询。",
            SHIFT_MODE_CONFIG_KEY: "开启后按住 Shift 即自动快速跑动，不再要求按住 W/A/S/D。",
            SWITCH_ENABLED_CONFIG_KEY: "开启后在冲刺时切换角色；关闭则只连按 Shift。",
            CHAR_COUNT_CONFIG_KEY: "参与快速跑动的角色数量（1–4）。设为 1 等同于只连按 Shift。",
        })
        self.config_type.update({
            INTERVAL_CONFIG_KEY: {
                "min": MIN_SPRINT_INTERVAL_MS,
                "max": MAX_SPRINT_INTERVAL_MS,
                "step": 10,
            },
            CHAR_COUNT_CONFIG_KEY: {
                "min": CHAR_COUNT_MIN,
                "max": CHAR_COUNT_MAX,
                "step": 1,
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

        # 键盘钩子状态
        self._pynput = Controller()
        self._listener: keyboard.Listener | None = None
        self._physical_keys: set[int] = set()
        self._suppressed_keys: set[int] = set()

        # 快速跑动状态机
        self._shift_pressed = False
        self._shift_down_at = 0.0
        self._quick_running = False
        self._quick_index = 0
        self._quick_at = 0.0
        self._quick_step = 0

    # ── 配置 ────────────────────────────────────────────────

    def _shift_mode(self) -> bool:
        return bool(self.config.get(SHIFT_MODE_CONFIG_KEY, True)) if self.config else True

    def _thread_enabled(self) -> bool:
        return bool(self.config.get(USE_THREAD_CONFIG_KEY, True)) if self.config else True

    def _switch_enabled(self) -> bool:
        return bool(self.config.get(SWITCH_ENABLED_CONFIG_KEY, True)) if self.config else True

    def _char_count(self) -> int:
        raw = self.config.get(CHAR_COUNT_CONFIG_KEY, 2) if self.config else 2
        try:
            count = int(raw)
        except (TypeError, ValueError):
            count = 2
        return max(CHAR_COUNT_MIN, min(CHAR_COUNT_MAX, count))

    def _sprint_interval_seconds(self) -> float:
        raw = self.config.get(INTERVAL_CONFIG_KEY, DEFAULT_SPRINT_INTERVAL_MS) if self.config else DEFAULT_SPRINT_INTERVAL_MS
        try:
            interval_ms = float(raw)
        except (TypeError, ValueError):
            interval_ms = float(DEFAULT_SPRINT_INTERVAL_MS)
        if not math.isfinite(interval_ms) or interval_ms <= 0:
            interval_ms = float(DEFAULT_SPRINT_INTERVAL_MS)
        return max(float(MIN_SPRINT_INTERVAL_MS), interval_ms) / 1000.0

    # ── 状态判定 ────────────────────────────────────────────

    def _is_key_pressed(self, vk: int) -> bool:
        return bool(win32api.GetAsyncKeyState(vk) & 0x8000)

    def _is_wasd_pressed(self) -> bool:
        return any(self._is_key_pressed(vk) for vk in WASD_VK_CODES)

    def _is_shift_physical_down(self) -> bool:
        return any(vk in self._physical_keys for vk in SHIFT_KEYS)

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

    # ── 键盘钩子 ────────────────────────────────────────────

    def _is_listener_running(self) -> bool:
        listener = self._listener
        return bool(listener is not None and listener.running and listener.is_alive())

    def start_listener(self) -> None:
        if self._is_listener_running():
            return
        self._physical_keys = set()
        self._suppressed_keys = set()
        listener = keyboard.Listener(win32_event_filter=self._win32_filter)
        self._listener = listener
        listener.start()
        logger.info("自动冲刺键盘监听已启动")

    def stop_listener(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                logger.exception("停止自动冲刺键盘监听失败")
        self._physical_keys = set()
        self._suppressed_keys = set()
        self._reset_quick_run()

    def _win32_filter(self, msg, data):
        # 注入事件（LLKHF_INJECTED）不参与物理按键集合，避免自我触发。
        if getattr(data, "flags", 0) & 0x10:
            return True
        vk = data.vkCode
        if msg in KEY_DOWN_MESSAGES:
            self._physical_keys.add(vk)
        elif msg in KEY_UP_MESSAGES:
            self._physical_keys.discard(vk)
            if vk in SHIFT_KEYS and not self._is_shift_physical_down():
                self._reset_quick_run()

        listener = self._listener
        suppress = listener is not None and self._should_suppress(msg, vk)
        if suppress:
            listener.suppress_event()
        return not suppress

    def _should_suppress(self, msg, vk: int) -> bool:
        if msg in KEY_UP_MESSAGES:
            suppress = vk in self._suppressed_keys
            self._suppressed_keys.discard(vk)
            return suppress
        if msg not in KEY_DOWN_MESSAGES or not self._can_input():
            return False
        suppress = vk in SHIFT_KEYS and self._quick_running
        if suppress:
            self._suppressed_keys.add(vk)
        return suppress

    # ── pynput 按键 ─────────────────────────────────────────

    def _tap_key(self, key: str) -> bool:
        mapped = PYNPUT_KEY_MAP.get(key.strip().lower(), key)
        try:
            self._pynput.press(mapped)
            time.sleep(0.02)
        finally:
            self._pynput.release(mapped)
        return True

    def _release_shift_keys(self) -> None:
        for mapped in (Key.shift_l, Key.shift_r):
            try:
                self._pynput.release(mapped)
            except Exception:
                logger.exception("释放 Shift 失败")

    # ── 快速跑动状态机（照抄 ok-nte）────────────────────────

    def _reset_quick_run(self) -> None:
        self._shift_pressed = False
        self._shift_down_at = 0.0
        self._quick_running = False
        self._quick_index = 0
        self._quick_at = 0.0
        self._quick_step = 0

    def _handle_quick_run(self, now: float) -> None:
        if not self._can_input():
            self._reset_quick_run()
            return

        shift_down = self._is_shift_physical_down()
        if not shift_down:
            self._reset_quick_run()
            return
        if not self._shift_pressed:
            self._shift_down_at = now
        self._shift_pressed = True

        if not self._quick_running:
            if now - self._shift_down_at < QUICK_RUN_HOLD_SECONDS:
                return
            self._quick_running = True
            self._quick_index = 0
            self._quick_at = 0.0
            self._quick_step = 0
            # 接管所有权：先释放物理 Shift，之后游戏只看到程序发出的按键。
            self._release_shift_keys()
            logger.info("自动冲刺开始（已接管 Shift）")

        if not self._switch_enabled():
            self._run_shift_only(now)
            return

        if now < self._quick_at:
            return
        if not self._is_shift_physical_down():
            self._reset_quick_run()
            return

        if self._quick_step == 0:
            count = self._char_count()
            slot = str(self._quick_index % count + 1)
            self._quick_index += 1
            deadline = now + QUICK_RUN_KEY_AFTER_SECONDS
            next_send = 0.0
            while self._can_input() and self._is_shift_physical_down() and time.time() < now + 3:
                current = time.time()
                if current >= next_send:
                    self._tap_key(slot)
                    next_send = current + QUICK_RUN_RETRY_SECONDS
                if current >= deadline:
                    break
                time.sleep(0.05)
            self._quick_step = 1
            self._quick_at = time.time()
        else:
            elapsed = time.time() - self._quick_at
            if elapsed >= QUICK_RUN_CYCLE_SECONDS:
                self._quick_step = 0
                self._quick_at = time.time()
                return
            interval = self._sprint_interval_seconds()
            if elapsed >= (self._quick_step - 1) * interval:
                self._tap_key("lshift")
                self._quick_step += 1

    def _run_shift_only(self, now: float) -> None:
        """关闭切人时退化为纯 Shift 连按。"""
        interval = self._sprint_interval_seconds()
        if self._last_sprint_at is not None and now - self._last_sprint_at < interval:
            return
        self._tap_key("lshift")
        self._last_sprint_at = now

    # ── 原有 WASD 模式（保持兼容）──────────────────────────

    def _press_shift_wasd(self, down_time: float) -> bool:
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
            pass

    def _release_shift(self) -> None:
        with self._key_lock:
            interaction = self._held_interaction
            if interaction is None:
                return
            self._held_interaction = None
            try:
                interaction.send_key_up(SPRINT_KEY)
            except Exception:
                logger.exception("释放冲刺按键失败")

    def _run_wasd_mode(self, now: float) -> None:
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
        if self._press_shift_wasd(down_time) and not self._stop_event.is_set():
            self._last_sprint_at = now

    # ── 主逻辑 ──────────────────────────────────────────────

    def run(self):
        self._ensure_worker()
        if not self._can_input():
            self._wasd_pressed_since = None
            return
        if self._shift_mode():
            self._handle_quick_run(time.time())
        else:
            self._run_wasd_mode(self.active_time())

    def _sprint_tick(self) -> None:
        if not self._can_input():
            self._wasd_pressed_since = None
            return
        if self._shift_mode():
            self._handle_quick_run(time.time())
        else:
            self._run_wasd_mode(time.monotonic())

    # ── 生命周期 ────────────────────────────────────────────

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sprint_tick()
            except Exception:
                logger.exception("自动冲刺独立线程异常")
            self._stop_event.wait(THREAD_POLL_SECONDS)
        self._release_shift()

    def _ensure_worker(self) -> None:
        self.start_listener()
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
        self._reset_quick_run()
        super().enable()
        self._ensure_worker()

    def disable(self):
        with self._key_lock:
            self._stop_event.set()
            self._wasd_pressed_since = None
            self._last_sprint_at = None
            self._release_shift()
        self._reset_quick_run()
        self._stop_worker()
        self.stop_listener()
        super().disable()

    def on_destroy(self):
        with self._key_lock:
            self._destroyed = True
            self._stop_event.set()
            self._wasd_pressed_since = None
            self._last_sprint_at = None
            self._release_shift()
        self._reset_quick_run()
        self._stop_worker()
        self.stop_listener()
        super().on_destroy()
