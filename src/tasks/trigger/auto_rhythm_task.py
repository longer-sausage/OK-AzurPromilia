"""花瓣音游：界面出现后独占逐帧判定，退出、暂停、失焦时释放按键。"""

import logging
import threading
import time
from dataclasses import dataclass, field, replace

import numpy as np
import win32gui
from ok import TriggerTask

from src.core.base_game_task import BaseGameTask
from src.icons import Icons
from src.image.rhythm_detector import JUDGE_X, RhythmDetector, RhythmNote

logger = logging.getLogger(f"ok.{__name__}")
MIN_HIT_INTERVAL = 0.06  # 游戏会把 40ms 内的 Q/E 单键输入合成为紫色双键。
MAX_HIT_LATENESS = 0.10  # 截图偶尔跨过截止时刻时，保留仍可判定的已确认音符。


@dataclass
class _Track:
    note: RhythmNote
    seen: float
    hit: bool = False
    history: list = field(default_factory=list)
    speed: float = 720.0
    fitted_head: float = 0.0
    long_votes: list = field(default_factory=list)
    long: bool | None = None
    length_samples: list = field(default_factory=list)
    length: float | None = None
    fitted: bool = False
    measured_speed: float = 720.0
    model_at: float | None = None

    def predict(self, at):
        return self.fitted_head - self.speed * (at - self.model_time)

    @property
    def model_time(self):
        return self.seen if self.model_at is None else self.model_at

    @property
    def judge_at(self):
        return self.model_time + (self.fitted_head - JUDGE_X) / self.speed

    def observe(self, note, at):
        if self.history and at <= self.seen:
            return
        if note.head >= 1000:
            self.long_votes.append(bool(note.long))
            self.long_votes = self.long_votes[-3:]
            if len(self.long_votes) >= 3:
                if self.long is None:
                    self.long = sum(self.long_votes) >= 2
                elif not self.long and all(self.long_votes):
                    self.long = True
        if note.long and (not note.clipped or note.tail < 1600):
            self.length_samples.append((at, note.tail - note.head))
            self.length_samples = [(t, size) for t, size in self.length_samples if at - t <= 0.6]
            tolerance = max(8, self.speed * 0.02)
            groups = [
                [(t, size) for t, size in self.length_samples if abs(size - center) < tolerance]
                for _, center in self.length_samples
            ]
            stable = max(groups, key=lambda samples: (len(samples), samples[-1][0] - samples[0][0]))
            if len(stable) >= 3 and stable[-1][0] - stable[0][0] >= 0.12:
                length = float(np.median([size for _, size in stable]))
                if self.length is None or length > self.length or not note.clipped:
                    self.length = length
        if self.long is not None:
            if self.long and not note.long:
                note = replace(note, tail=note.head + self.note.tail - self.note.head, clipped=self.note.clipped)
            note = replace(note, long=self.long)
        if note.long and self.length is not None:
            note = replace(note, tail=note.head + self.length, clipped=False)
        self.note = note
        self.seen = at
        self.model_at = at
        self.history.append((at, note.head))
        self.history = [(t, x) for t, x in self.history if at - t <= 0.7]
        self.fitted_head = note.head
        if len(self.history) >= 3 and at - self.history[0][0] >= 0.08:
            # 用多帧直线拟合吸收重复画面、游戏帧步进及单帧掩膜边缘抖动。
            points = np.asarray(self.history)
            times = points[:, 0] - at
            centered = times - times.mean()
            velocity = -float(np.dot(centered, points[:, 1] - points[:, 1].mean()) / np.dot(centered, centered))
            if 75 < velocity < 4000:
                self.speed = velocity
                self.measured_speed = velocity
                self.fitted_head = float(points[:, 1].mean() + velocity * times.mean())
                self.fitted = True


@dataclass
class _HeldNote:
    keys: tuple
    color: str | None
    release_at: float
    speed: float
    estimated_release: float
    tail_samples: list = field(default_factory=list)
    uncertain: bool = False


class RhythmPlayer:
    """时序判定独立于框架，便于用真实截图序列回放。"""

    def __init__(self):
        self.tracks = []
        self.speed = 720.0
        self.holds = []
        self._last_hit_at = float("-inf")

    @property
    def held(self):
        return tuple(key for key in ("q", "e") if any(key in hold.keys for hold in self.holds))

    @property
    def release_at(self):
        return min((hold.release_at for hold in self.holds), default=0.0)

    def next_deadline(self, lead_seconds):
        pending = []
        for track in self.tracks:
            if track.hit or not track.fitted:
                continue
            deadline = max(track.judge_at - lead_seconds, self._last_hit_at + MIN_HIT_INTERVAL)
            conflicts = [
                hold.release_at
                for hold in self.holds
                if hold.color is not None or set(hold.keys).intersection(track.note.keys)
            ]
            if conflicts:
                deadline = max(deadline, max(conflicts))
            pending.append(deadline)
        pending.extend(hold.release_at for hold in self.holds)
        return min(pending, default=float("inf"))

    def _clamp_release(self, lead_seconds):
        for hold in self.holds:
            if hold.color is None:
                continue
            conflicts = [track.judge_at - lead_seconds for track in self.tracks if not track.hit and track.fitted]
            if conflicts:
                hold.release_at = min(hold.release_at, min(conflicts) - 0.045)

    def update(self, notes, now, lead=12.0, observed_at=None):
        observed_at = now if observed_at is None else observed_at
        lead_seconds = lead / self.speed
        self.tracks = [track for track in self.tracks if now - track.seen < 0.8]
        available = list(self.tracks)
        predictions = {id(track): track.predict(observed_at) for track in self.tracks}
        residuals, observed = [], set()
        hits = []
        for note in notes:
            if note.head < JUDGE_X + 220:
                continue
            candidates = [
                track
                for track in available
                if track.note.color == note.color
                and abs(note.head - track.predict(observed_at))
                < max(
                    38,
                    track.speed * 0.1,
                    track.speed * max(0, observed_at - track.seen) * (2 if not track.fitted else 0.35),
                )
            ]
            track = min(
                candidates,
                key=lambda item: abs(note.head - item.predict(observed_at)),
                default=None,
            )
            if track is None:
                # 判定圈附近只有特效/已消耗音符的残影，不在此创建新轨迹。
                # 真正的音符会先经过右侧清晰区域，再进入判定圈。
                if not 1000 <= note.head <= 1400:
                    continue
                track = _Track(note, observed_at, speed=self.speed, fitted_head=note.head)
                track.observe(note, observed_at)
                self.tracks.append(track)
            else:
                available.remove(track)
                if track.fitted:
                    residuals.append(note.head - predictions[id(track)])
                track.observe(note, observed_at)
            observed.add(id(track))
        speeds = [track.measured_speed for track in self.tracks if track.fitted]
        # 定时回调没有新观测，不能因为旧轨迹过期而改变已安排的输入时间。
        if speeds and any(track.fitted and id(track) in observed for track in self.tracks):
            self.speed = float(np.median(speeds))
        # 一条轨道的全部音符使用同一滚动速度和本帧位移，保持相邻音符的顺序与间隔。
        # 对遮挡中的头部也应用共同位移，但 seen 仍只记录真实看见头部的时刻。
        correction = float(np.median(residuals)) if residuals else 0.0
        for track in self.tracks:
            track.speed = self.speed
            track.fitted_head = track.note.head if id(track) in observed else predictions[id(track)] + correction
            track.model_at = observed_at

        # 松键同样补偿输入延迟；不添加滞后，确保紧随的短音符留足抬起间隙。
        for hold in self.holds:
            if hold.color is None:
                continue
            hold.speed = self.speed
            bodies = [
                note
                for note in notes
                if note.color == hold.color
                and note.long
                and not note.clipped
                and note.head <= JUDGE_X + 220
                and note.tail > JUDGE_X
            ]
            if bodies:
                tail = max(bodies, key=lambda note: note.tail).tail
                release_at = observed_at + (tail - JUDGE_X) / hold.speed - lead_seconds
                if (hold.uncertain and release_at >= hold.estimated_release - 0.1) or abs(
                    release_at - hold.estimated_release
                ) <= 0.2:
                    if not hold.tail_samples or observed_at > hold.tail_samples[-1][0]:
                        hold.tail_samples.append((observed_at, release_at))
                        hold.tail_samples = [(at, end) for at, end in hold.tail_samples if observed_at - at <= 1.2]
                    ends = [end for _, end in hold.tail_samples]
                    if (
                        len(ends) >= 3
                        and observed_at - hold.tail_samples[0][0] >= 0.12
                        and max(ends) - min(ends) < 0.08
                    ):
                        hold.release_at = float(np.median(ends))
                        hold.estimated_release = hold.release_at
                        hold.uncertain = False
        self._clamp_release(lead_seconds)
        self.holds = [hold for hold in self.holds if now < hold.release_at]

        for track in self.tracks:
            note = track.note
            predicted = track.predict(now)
            oldest_head = JUDGE_X - track.speed * MAX_HIT_LATENESS
            if predicted < oldest_head:
                track.hit = True  # 已错过的截止时刻不能阻塞后续定时输入。
            # 判定圈的花瓣特效会遮住下一枚音符，用进入特效前的轨迹短暂外推。
            if (
                not track.hit
                and track.fitted
                and now >= self._last_hit_at + MIN_HIT_INTERVAL
                and now - track.seen < 0.65
                and oldest_head <= predicted <= JUDGE_X + lead_seconds * track.speed
                and not any(hold.color is not None or set(hold.keys).intersection(note.keys) for hold in self.holds)
            ):
                displacement = note.head - predicted
                hit = RhythmNote(note.color, predicted, note.tail - displacement, note.y, note.long, note.clipped)
                deadline = track.judge_at
                hits.append((deadline, track, hit))

        selected = min(hits, key=lambda item: item[0], default=None)
        hit = None
        if selected is not None:
            deadline, track, hit = selected
            track.hit = True
            self._last_hit_at = now
            release_at = now + (max(0, hit.tail - JUDGE_X) / track.speed - lead_seconds if hit.long else 0.045)
            hold = _HeldNote(
                hit.keys,
                hit.color if hit.long else None,
                float("inf") if hit.long and hit.clipped else release_at,
                track.speed,
                release_at,
                uncertain=hit.clipped,
            )
            self.holds.append(hold)
            self._clamp_release(lead_seconds)
            logger.debug(
                "Rhythm prediction color=%s long=%s head=%.2f tail=%.2f speed=%.2f "
                "now=%.6f judge_at=%.6f observed_age_ms=%.2f release_at=%.6f",
                hit.color,
                hit.long,
                hit.head,
                hit.tail,
                track.speed,
                now,
                deadline,
                (now - track.seen) * 1000,
                hold.release_at,
            )
        return self.held, hit


class AutoRhythmTask(BaseGameTask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动音游"
        self.description = "检测到音游界面自动完成。"
        self.icon = Icons.Trigger
        self.trigger_interval = 0
        self.default_config.update({"输入延迟 (ms)": 50})
        self._rhythm_detector = RhythmDetector()
        self.player = RhythmPlayer()
        self._key_lock = threading.RLock()
        self._held_keys = set()
        self._last_frame_at = 0.0
        self._cancel = threading.Event()

    def _release_keys(self):
        # 不走 BaseTask.send_key_up：它在禁用/暂停时先 check_enabled，会阻止清理。
        with self._key_lock:
            for key in tuple(self._held_keys):
                try:
                    self.executor.interaction.send_key_up(key)
                except Exception:
                    logger.exception("Failed to release rhythm key %s", key)
                else:
                    self._held_keys.discard(key)

    def _set_keys(self, keys, retrigger=False):
        with self._key_lock:
            if self._cancel.is_set():
                return
            wanted = set(keys)
            retrigger_keys = wanted if retrigger is True else set(retrigger or ())
            changed_at = time.perf_counter() if wanted != self._held_keys or retrigger else None
            retriggered = []
            for key in tuple(self._held_keys):
                if key not in wanted or key in retrigger_keys:
                    self.executor.interaction.send_key_up(key)
                    self._held_keys.remove(key)
                    if key in wanted:
                        retriggered.append(key)
            if retriggered:
                # 重新触发同一按键时保持至少 15ms 的按键抬起态，避免微秒级重触发被游戏输入层判定为持续按住。
                time.sleep(0.015)
            for key in keys:
                if self._cancel.is_set():
                    return
                if key not in self._held_keys:
                    # 双键连续 key-down，中间不截图、不 sleep，之后统一 key-up。
                    if self.executor.interaction.send_key_down(key, activate=False) is False:
                        raise RuntimeError("Rhythm key-down failed")
                    self._held_keys.add(key)
            if changed_at is not None:
                logger.debug(
                    "Rhythm input keys=%s retrigger=%s requested_at=%.6f applied_at=%.6f",
                    tuple(keys),
                    retrigger,
                    changed_at,
                    time.perf_counter(),
                )

    def _watch_input(self, done, hwnd):
        while not done.wait(0.02):
            if (
                self.executor.paused
                or not self.enabled
                or self.executor.exit_event.is_set()
                or win32gui.GetForegroundWindow() != hwnd
                or time.perf_counter() - self._last_frame_at > 0.75
            ):
                self._stop_reason = "pause, disable, focus loss or stalled capture"
                self._cancel.set()
                self._release_keys()
                return

    def run(self):
        capture_started = time.perf_counter()
        frame = self.next_frame()
        captured = time.perf_counter()
        if not self._rhythm_detector.is_active(frame):
            return
        hwnd = self.get_game_hwnd()
        if not hwnd or win32gui.GetForegroundWindow() != hwnd:
            return
        self.player = RhythmPlayer()
        self._cancel.clear()
        self._stop_reason = "session finished"
        self._last_frame_at = captured
        done = threading.Event()
        watcher = threading.Thread(target=self._watch_input, args=(done, hwnd), daemon=True)
        last_band = None
        changed_at = captured
        input_lead = max(0.0, min(0.15, float(self.config.get("输入延迟 (ms)", 50)) / 1000))
        logger.info("Rhythm session started frame=%s input_lead_ms=%.1f", frame.shape, input_lead * 1000)
        watcher.start()
        try:
            for _ in self.loop(time_out=float("inf"), yield_frame=False, raise_if_time_out=False):
                if self._cancel.is_set():
                    break
                now = time.perf_counter()
                self._last_frame_at = now
                if not self._rhythm_detector.is_active(frame):
                    self._stop_reason = "rhythm interface disappeared"
                    break
                band = self._rhythm_detector.track_band(frame)
                if last_band is None or not np.array_equal(band, last_band):
                    changed_at = now
                    last_band = band.copy()
                elif now - changed_at > 0.75:
                    self._stop_reason = "unchanged capture"
                    break
                # WGC 时间戳是画面产生时间；不能把识别完成时间当作音符观测时间。
                method = getattr(self.executor, "method", None)
                observed_at = getattr(method, "frame_timestamp", None)
                if (
                    not isinstance(observed_at, (float, int))
                    or not np.isfinite(observed_at)
                    or abs(observed_at - captured) > 0.2
                ):
                    observed_at = (capture_started + captured) / 2
                notes = self._rhythm_detector.detect(frame)
                now = time.perf_counter()
                keys, hit = self.player.update(notes, now, self.player.speed * input_lead, observed_at)
                self._set_keys(keys, retrigger=hit.keys if hit is not None else ())
                # 若下一次截图与识别会跨过按键时刻，先服务定时输入，再截图。
                # 用本机实际截图 + 识别耗时安排下一帧前的输入；低帧率下不能固定为 60ms。
                horizon = time.perf_counter() + min(max(now - capture_started + 0.010, 0.035), 0.25)
                for _ in self.loop(
                    time_out=max(0.0, horizon - time.perf_counter()), yield_frame=False, raise_if_time_out=False
                ):
                    if self._cancel.is_set():
                        break
                    deadline = self.player.next_deadline(input_lead)
                    now = time.perf_counter()
                    if deadline > horizon:
                        break
                    if self._cancel.wait(max(0, deadline - now)):
                        break
                    keys, hit = self.player.update([], time.perf_counter(), self.player.speed * input_lead)
                    self._set_keys(keys, retrigger=hit.keys if hit is not None else ())
                if self._cancel.is_set():
                    break
                capture_started = time.perf_counter()
                frame = self.next_frame()
                captured = time.perf_counter()
        finally:
            done.set()
            self._cancel.set()
            self._release_keys()
            watcher.join(timeout=0.1)
            logger.info("Rhythm session stopped reason=%s", self._stop_reason)

    def disable(self):
        self._cancel.set()
        self._release_keys()
        super().disable()

    def on_destroy(self):
        self._cancel.set()
        self._release_keys()
        super().on_destroy()
