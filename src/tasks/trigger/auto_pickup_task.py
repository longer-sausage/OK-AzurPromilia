"""自动拾取触发式任务：检测到可拾取图标时连按 F 直到图标消失。"""

from functools import partial
from threading import Event, Lock
from time import monotonic, sleep, time

import cv2
import numpy as np
import win32con
import win32gui
from ok import Handler, Logger, TriggerTask
from pynput import keyboard
from pynput.keyboard import Controller, KeyCode

from src.core.base_game_task import BaseGameTask
from src.data.feature_list import FeatureList
from src.icons import Icons
from src.patches import pickup_wgc_region

logger = Logger.get_logger(__name__)

COLLECT_KEY = "f"
COLLECT_AFTER_SLEEP = 0.1
COLLECT_TIMEOUT = 10.0
MANUAL_BURST = "手动 F 连拾"
BURST_SECONDS = 10.0
BURST_INTERVAL_KEY = "手动连拾间隔 (ms)"
DEFAULT_BURST_INTERVAL_MS = 300
MIN_BURST_INTERVAL_MS = 200
MAX_BURST_INTERVAL_MS = 1000
KEY_DOWN_SECONDS = 0.02
F_VK = ord("F")
INJECTED = win32con.LLKHF_INJECTED | win32con.LLKHF_LOWER_IL_INJECTED


class _PickupBurst:
    """旁听物理 F，不拦截原按键；独立于截图执行器安排补按。"""

    def __init__(self, task):
        self.task = task
        self._lock = Lock()
        self._stopped = Event()
        self._down = False
        self._deadline = 0.0
        self._keyboard = Controller()
        self._key = KeyCode.from_vk(F_VK)
        self.listener = keyboard.Listener(win32_event_filter=self._filter)
        self._handler = Handler(task.executor.exit_event, name="pickup-burst", daemon=True)

    def start(self):
        self.listener.start()

    def stop(self):
        self._stopped.set()
        try:
            self.listener.stop()
        finally:
            self._handler.stop()
            self._handler.join()

    def active(self):
        with self._lock:
            return not self._stopped.is_set() and monotonic() < self._deadline

    def _filter(self, msg, data):
        if data.vkCode != F_VK or data.flags & INJECTED:
            return False
        with self._lock:
            if msg in (win32con.WM_KEYUP, win32con.WM_SYSKEYUP):
                self._down = False
            elif msg in (win32con.WM_KEYDOWN, win32con.WM_SYSKEYDOWN) and not self._down:
                self._down = True
                now = monotonic()
                if not self._stopped.is_set() and now >= self._deadline and (hwnd := self.task._burst_window()):
                    self._deadline = now + BURST_SECONDS
                    self._handler.post(partial(self._tick, hwnd, self._deadline), delay=self.task._burst_interval())
        # Skip pynput's character translation, not delivery to the game.
        return False

    def _tick(self, hwnd, deadline):
        now = monotonic()
        with self._lock:
            if self._deadline != deadline or self._stopped.is_set():
                return
            if now + KEY_DOWN_SECONDS >= deadline or self.task._burst_window() != hwnd or not self.listener.is_alive():
                self._deadline = 0.0
                return
            held = self._down
        try:
            # The original F is delivered normally; don't fight a held physical F.
            if not held and not self._stopped.is_set() and self.task._burst_window() == hwnd:
                self._keyboard.press(self._key)
                try:
                    self._stopped.wait(KEY_DOWN_SECONDS)
                finally:
                    self._keyboard.release(self._key)
        except Exception:
            with self._lock:
                if self._deadline == deadline:
                    self._deadline = 0.0
            logger.exception("手动 F 连拾发送按键失败")
            return
        if not self._stopped.is_set():
            delay = max(0.0, now + self.task._burst_interval() - monotonic())
            self._handler.post(partial(self._tick, hwnd, deadline), delay=delay)


class AutoPickupTask(BaseGameTask, TriggerTask):
    """自动拾取触发式任务：检测到 interact_collect 时连按 F 直到消失。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动拾取"
        self.description = "检测到可拾取提示时自动连按 F，直到提示消失。"
        self.icon = Icons.Trigger
        self.default_config.update({MANUAL_BURST: False, BURST_INTERVAL_KEY: DEFAULT_BURST_INTERVAL_MS})
        self.config_description[MANUAL_BURST] = (
            "在游戏前台轻按一次 F，接下来十秒按设定间隔补按，不识别画面。"
            "可能触发对话等其他 F 交互；切出游戏或暂停即停止。"
        )
        self.config_description[BURST_INTERVAL_KEY] = "仅影响手动 F 连拾，不改变原有识别拾取的节奏。"
        self.config_type[BURST_INTERVAL_KEY] = {
            "min": MIN_BURST_INTERVAL_MS, "max": MAX_BURST_INTERVAL_MS, "step": 10,
        }
        self._burst = None
        self._burst_lock = Lock()
        self._full_frame_path = False

    @property
    def needs_frame(self):
        if self._full_frame_path or self.debug:
            return True
        executor = self.executor
        method = getattr(executor, 'method', None)
        if not pickup_wgc_region.supported(method):
            return True
        # Keep full-frame consumers (UID overlay, snapshot API) supplied.
        processor = getattr(executor, 'blur_overlay_processor', None)
        return bool(
            ((processor is not None and processor.enabled()) or executor._snapshot_enabled)
            and time() - executor._last_frame_time >= 0.5
        )

    def _find_first_collect(self):
        executor = self.executor
        method = getattr(executor, 'method', None)
        if self.needs_frame:
            return self.find_one(FeatureList.interact_collect)
        # Only reuse the current executor frame; never use the diagnostic last frame.
        frame = executor._frame
        now = time()
        if (frame is not None and 0 <= now - executor._last_frame_time <= 0.075
                and frame.shape[:2] == (method.last_size.Height, method.last_size.Width)):
            return self.find_one(FeatureList.interact_collect, frame=frame)
        region_hit = self._match_pickup_region(method)
        if region_hit is not None:
            return region_hit
        self._full_frame_path = True
        try:
            # Force a fresh frame: a previous round's frame may have expired.
            frame = self.next_frame()
            return frame is not None and self.find_one(FeatureList.interact_collect, frame=frame)
        finally:
            self._full_frame_path = False

    def _match_pickup_region(self, method):
        executor = self.executor
        if not executor.can_capture():
            return None
        features = executor.feature_set
        width, height = method.last_size.Width, method.last_size.Height
        name = FeatureList.interact_collect
        # FeatureSet only inspects shape here, not pixels. Use a zero-stride view.
        features.check_size(np.broadcast_to(np.uint8(0), (height, width, 3)))
        features.ensure_feature(name)
        feature = features.feature_dict.get(name)
        if feature is None or feature.mat.ndim != 3 or feature.mat.shape[2] != 3:
            return None
        dx, dy = width * features.default_horizontal_variance, height * features.default_vertical_variance
        box = (max(0, round(feature.x - dx)), max(0, round(feature.y - dy)),
               min(width, round(feature.x + feature.width + dx)),
               min(height, round(feature.y + feature.height + dy)))
        region = method.get_pickup_region(box)
        if region is None or any(a < b for a, b in zip(region.shape[:2], feature.mat.shape[:2], strict=True)):
            return None
        scores = cv2.matchTemplate(region, feature.mat, cv2.TM_CCOEFF_NORMED, mask=feature.mask)
        np.nan_to_num(scores, copy=False, nan=0, posinf=0, neginf=0)
        return cv2.minMaxLoc(scores)[1] >= features.default_threshold

    def _burst_interval(self):
        value = (self.config or self.default_config).get(BURST_INTERVAL_KEY, DEFAULT_BURST_INTERVAL_MS)
        if type(value) is not int:
            value = DEFAULT_BURST_INTERVAL_MS
        return max(MIN_BURST_INTERVAL_MS, min(MAX_BURST_INTERVAL_MS, value)) / 1000

    def should_trigger(self):
        # 在执行器为本任务准备截图之前跳过，其他触发任务仍可照常取帧。
        return not self._burst_active() and super().should_trigger()

    def _burst_window(self):
        executor = self.executor
        if (not self.enabled or not (self.config or {}).get(MANUAL_BURST, False)
                or self.paused or executor.paused or executor.exit_event.is_set()
                or executor.exclusive_owner is not None):
            return None
        window = getattr(executor.device_manager, "hwnd_window", None)
        hwnd = getattr(window, "hwnd", None)
        return hwnd if hwnd and win32gui.GetForegroundWindow() == hwnd else None

    def _start_burst_listener(self):
        with self._burst_lock:
            if self._burst is not None or not self.enabled or self.executor.exit_event.is_set():
                return
            burst = _PickupBurst(self)
            try:
                burst.start()
            except Exception:
                burst.stop()
                logger.exception("无法启动手动 F 连拾监听，识别拾取仍可用")
                return
            self._burst = burst

    def _stop_burst_listener(self):
        with self._burst_lock:
            if self._burst is not None:
                burst, self._burst = self._burst, None
                burst.stop()

    def _burst_active(self):
        burst = self._burst
        return burst is not None and burst.active()

    def on_create(self):
        super().on_create()
        self._start_burst_listener()

    def enable(self):
        super().enable()
        self._start_burst_listener()

    def disable(self):
        super().disable()
        self._stop_burst_listener()

    def on_destroy(self):
        self._stop_burst_listener()
        super().on_destroy()

    def _send_first_collect(self):
        """首次命中只向当前前台游戏发送 F，不尝试重新激活窗口。"""
        started = monotonic()
        interaction = self.executor.interaction
        hwnd = getattr(getattr(interaction, "hwnd_window", None), "hwnd", None)
        if not hwnd or win32gui.GetForegroundWindow() != hwnd or self._burst_active():
            return False
        self.executor.reset_scene()
        if win32gui.GetForegroundWindow() != hwnd or self._burst_active():
            return False
        if not interaction.send_key_down(COLLECT_KEY, activate=False):
            logger.debug("自动拾取首键未送出")
            return False
        sent = monotonic()
        try:
            sleep(0.04)
        finally:
            interaction.send_key_up(COLLECT_KEY)
        logger.debug(f"自动拾取首键从准备到送出 key-down 耗时 {(sent - started) * 1000:.1f} ms")
        self.sleep(COLLECT_AFTER_SLEEP)
        return True

    def handle_collect(self):
        """持续按 F 拾取直到 interact_collect 消失。"""
        if self._burst_active() or not self._send_first_collect():
            return False
        for frame in self.loop(COLLECT_TIMEOUT, raise_if_time_out=False):
            if self._burst_active() or frame is None or not self.find_one(FeatureList.interact_collect, frame=frame):
                break
            if self._burst_active():
                break
            self.send_key(COLLECT_KEY, after_sleep=COLLECT_AFTER_SLEEP)
        return True

    def run(self):
        if not self._burst_active() and self._find_first_collect() and not self._burst_active():
            logger.info("检测到可拾取目标，开始自动拾取")
            self._full_frame_path = True
            try:
                return self.handle_collect()
            finally:
                self._full_frame_path = False
