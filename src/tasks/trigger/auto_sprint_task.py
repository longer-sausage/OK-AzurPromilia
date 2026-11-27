"""长按物理 Shift 时，按队伍顺序切人并冲刺。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from functools import partial
from time import monotonic

import win32con
import win32gui
from ok import Handler, Logger, TriggerTask
from pynput import keyboard
from pynput.keyboard import Controller, Key, KeyCode

from src.core.base_game_task import BaseGameTask
from src.icons import Icons

logger = Logger.get_logger(__name__)

CHAR_COUNT_CONFIG_KEY = "快速奔跑角色数量"
MAX_CHAR_COUNT = 3
HOLD_SECONDS = 0.5
SWITCH_INTERVAL = 0.7
SPRINT_AFTER_SWITCH = 0.25
SPRINT_INTERVAL = 0.2
KEY_DOWN_SECONDS = 0.02

KEY_DOWN = (win32con.WM_KEYDOWN, win32con.WM_SYSKEYDOWN)
KEY_UP = (win32con.WM_KEYUP, win32con.WM_SYSKEYUP)
SHIFT_VKS = (win32con.VK_SHIFT, win32con.VK_LSHIFT, win32con.VK_RSHIFT)
INJECTED = win32con.LLKHF_INJECTED | win32con.LLKHF_LOWER_IL_INJECTED


@dataclass
class _ShiftHold:
    """每次长按独享取消信号和节奏，旧回调无法继续下一次长按。"""

    released: threading.Event = field(default_factory=threading.Event)
    # None 表示尚未接管。集合里的键已补发抬起，或其首次按下已被拦截。
    owned_shifts: set[Key] | None = None
    slot_index: int = 0
    switch_at: float = 0.0
    sprint_at: float = 0.0

    def next_at(self, count: int) -> float:
        return min(self.switch_at, self.sprint_at) if count > 1 else self.sprint_at

    def next_key(self, count: int, now: float) -> Key | KeyCode:
        if count > 1 and self.switch_at <= self.sprint_at:
            slot = self.slot_index % count
            self.slot_index = slot + 1
            self.switch_at = now + SWITCH_INTERVAL
            self.sprint_at = now + SPRINT_AFTER_SWITCH
            # 游戏绑定的是键盘顶排数字键，使用虚拟键可兼容不同键盘布局。
            return KeyCode.from_vk(ord("1") + slot)
        if count > 1:
            # 调度延迟时保留冲刺到下一次切人的间距，避免集中补发过期动作。
            self.switch_at += now - self.sprint_at
        self.sprint_at = now + SPRINT_INTERVAL
        return Key.shift_l


class _SprintInput:
    """钩子接收物理 Shift，单个 Handler 按时间顺序发送按键。"""

    def __init__(self, task: AutoSprintTask):
        self.task = task
        self._lock = threading.Lock()
        self._held_shifts: set[Key] = set()
        self._hold: _ShiftHold | None = None
        self._closed = False
        self._keyboard = Controller()
        self.listener = keyboard.Listener(win32_event_filter=self._filter)
        self._handler = Handler(task.executor.exit_event, name="auto-sprint", daemon=True)

    def start(self) -> None:
        self.listener.start()
        self.listener.wait()

    def stop(self) -> None:
        with self._lock:
            self._closed = True
            if self._hold:
                self._hold.released.set()
        try:
            self.listener.stop()
        except Exception:
            logger.exception("停止自动冲刺键盘监听失败")
        finally:
            self._handler.stop()
            self._handler.join()

    def _filter(self, msg: int, data) -> bool:
        """返回 False 跳过 pynput 的字符翻译，系统事件仅由 suppress_event 拦截。"""
        if data.flags & INJECTED or data.vkCode not in SHIFT_VKS:
            return False
        down = msg in KEY_DOWN
        if not down and msg not in KEY_UP:
            return False
        right = data.vkCode == win32con.VK_RSHIFT or (data.vkCode == win32con.VK_SHIFT and data.scanCode == 0x36)
        key = Key.shift_r if right else Key.shift_l
        with self._lock:
            if self._closed:
                return False
            allowed = self.task.can_sprint()
            hold = self._hold
            if hold and not allowed:
                hold.released.set()
            suppress = hold is not None and hold.owned_shifts is not None and not hold.released.is_set()
            if down:
                if not self._held_shifts and allowed:
                    self._hold = _ShiftHold()
                    self._handler.post(partial(self._step, self._hold), delay=HOLD_SECONDS)
                elif suppress and key not in self._held_shifts:
                    hold.owned_shifts.add(key)
                self._held_shifts.add(key)
            else:
                suppress = suppress and key in hold.owned_shifts
                self._held_shifts.discard(key)
                if not self._held_shifts and hold:
                    hold.released.set()
                    self._hold = None
        if suppress:
            self.listener.suppress_event()
        return False

    def _can_send(self, hold: _ShiftHold) -> bool:
        if not hold.released.is_set() and self.listener.is_alive() and self.task.can_sprint():
            return True
        hold.released.set()
        return False

    def _step(self, hold: _ShiftHold) -> None:
        try:
            with self._lock:
                if not self._can_send(hold):
                    return
                shifts = ()
                if hold.owned_shifts is None:
                    shifts = tuple(self._held_shifts)
                    hold.owned_shifts = set()
            # 原始 Shift 按下已透传。直接配平它，之后只由本次长按生成按键边沿。
            # 发键会回到 Win32 钩子，不能在持锁期间调用 Controller。
            for key in shifts:
                if not self._can_send(hold):
                    return
                self._keyboard.release(key)
                with self._lock:
                    hold.owned_shifts.add(key)

            count = self.task.char_count()
            now = monotonic()
            if now >= hold.next_at(count):
                key = hold.next_key(count, now)
                if not self._can_send(hold):
                    return
                try:
                    self._keyboard.press(key)
                    hold.released.wait(KEY_DOWN_SECONDS)
                finally:
                    self._keyboard.release(key)
            if not hold.released.is_set():
                delay = max(0.0, hold.next_at(count) - monotonic())
                self._handler.post(partial(self._step, hold), delay=delay)
        except Exception:
            hold.released.set()
            raise  # Handler 记录异常，下一次按住 Shift 可重新开始。


class AutoSprintTask(BaseGameTask, TriggerTask):
    needs_frame = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.tr("自动冲刺")
        self.description = self.tr("按住 Shift 半秒后自动冲刺，松开即停止。")
        self.icon = Icons.Trigger
        self.default_config[CHAR_COUNT_CONFIG_KEY] = MAX_CHAR_COUNT
        self.config_type[CHAR_COUNT_CONFIG_KEY] = {"min": 1, "max": MAX_CHAR_COUNT, "step": 1}
        self.config_description[CHAR_COUNT_CONFIG_KEY] = self.tr("1 人时只冲刺，2–3 人时按队伍顺序切人冲刺。")
        self.trigger_interval = 1  # 框架仅检查监听器是否存活，冲刺节奏由 Handler 驱动。
        self._lifecycle_lock = threading.Lock()
        self._input: _SprintInput | None = None
        self._destroyed = False

    def char_count(self) -> int:
        count = (self.config or self.default_config).get(CHAR_COUNT_CONFIG_KEY, MAX_CHAR_COUNT)
        return max(1, min(MAX_CHAR_COUNT, count)) if type(count) is int else MAX_CHAR_COUNT

    def load_config(self):
        super().load_config()
        self.config[CHAR_COUNT_CONFIG_KEY] = self.char_count()

    def can_sprint(self) -> bool:
        executor = self.executor
        window = getattr(executor.device_manager, "hwnd_window", None)
        hwnd = getattr(window, "hwnd", None)
        return bool(
            self.enabled
            and not self._destroyed
            and not self.paused
            and not executor.paused
            and not executor.exit_event.is_set()
            and executor.exclusive_owner is None
            and executor.interaction is not None
            and hwnd
            and win32gui.GetForegroundWindow() == hwnd
        )

    def _sync_input(self):
        with self._lifecycle_lock:
            enabled = self.enabled and not self._destroyed and not self.executor.exit_event.is_set()
            if self._input:
                if enabled and self._input.listener.is_alive():
                    return
                try:
                    self._input.stop()
                finally:
                    self._input = None
            if enabled:
                sprint_input = _SprintInput(self)
                try:
                    sprint_input.start()
                except Exception:
                    sprint_input.stop()
                    raise
                self._input = sprint_input

    def run(self):
        self._sync_input()

    def on_create(self):
        super().on_create()
        self._sync_input()

    def enable(self):
        super().enable()
        self._sync_input()

    def disable(self):
        super().disable()
        self._sync_input()

    def on_destroy(self):
        self._destroyed = True
        self._sync_input()
        super().on_destroy()
