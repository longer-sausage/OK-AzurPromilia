"""战斗检测：入战判定 + 脱战前的"不确定期 + 中键索敌"，以及脱战后的索敌扫描。


- 命中战斗信号（血条 / 锁定 / 游戏 auto）→ 在战斗中；
- 连续 miss 达阈值 → UNCERTAIN（0.5 秒，仍算在战斗，按一次中键）；
- UNCERTAIN 结束 → RETARGET：每 0.3 秒按一次中键，最多 2 秒；
  期间信号连续 2 帧回来 → 回到战斗；超时或索敌次数超预算 → 脱战；
- 脱战后：每 1 秒按一次中键扫描敌人，锁到目标（出现锁定/血条）就重新入战。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.combat import profile
from src.combat.recognition.report import RecognitionReport, recognize

_STATE_OUT = "out"
_STATE_IN = "in"
_STATE_UNCERTAIN = "uncertain"
_STATE_RETARGET = "retarget"


@dataclass(slots=True)
class CombatObservation:
    """一帧的战斗观测。"""

    in_combat: bool = False
    ended: bool = False
    game_auto: bool = False
    enemy_hp_ratio: float | None = None
    lock_on: bool = False
    current_slot: int | None = None
    frame_ok: bool = True
    retargeting: bool = False


class CombatChecker:
    """带"不确定期 + 中键索敌 + 脱战扫描"的战斗判定。"""

    def __init__(
        self,
        find,
        enter_frames: int = profile.ENTER_CONFIRM_FRAMES,
        exit_frames: int = profile.EXIT_CONFIRM_FRAMES,
        middle_click=None,
        uncertain_seconds: float = profile.EXIT_UNCERTAIN_SECONDS,
        retarget_seconds: float = profile.RETARGET_SECONDS,
        click_interval: float = profile.RETARGET_CLICK_INTERVAL,
        revive_frames: int = profile.REVIVE_CONFIRM_FRAMES,
        max_retargets: int = profile.MAX_RETARGET_ROUNDS,
        sweep_interval: float = profile.TARGET_SWEEP_INTERVAL,
        sweep_seconds: float = profile.SWEEP_AFTER_EXIT_SECONDS,
    ) -> None:
        self._find = find
        self.enter_frames = max(1, int(enter_frames))
        self.exit_frames = max(1, int(exit_frames))
        self.middle_click = middle_click
        self.uncertain_seconds = max(0.0, float(uncertain_seconds))
        self.retarget_seconds = max(0.0, float(retarget_seconds))
        self.click_interval = max(0.05, float(click_interval))
        self.revive_frames = max(1, int(revive_frames))
        self.max_retargets = max(0, int(max_retargets))
        self.sweep_interval = max(0.2, float(sweep_interval))
        self.sweep_seconds = max(0.0, float(sweep_seconds))
        self.reset()

    def reset(self) -> None:
        self._state = _STATE_OUT
        self._enter_hits = 0
        self._miss = 0
        self._revive_hits = 0
        self._uncertain_until = 0.0
        self._retarget_until = 0.0
        self._next_click_at = 0.0
        self._next_sweep_at = 0.0
        self._sweep_until = 0.0
        self._retarget_count = 0
        self._last: CombatObservation | None = None

    @property
    def last(self) -> CombatObservation | None:
        return self._last

    @property
    def in_combat(self) -> bool:
        return self._state in (_STATE_IN, _STATE_UNCERTAIN, _STATE_RETARGET)

    @property
    def retargeting(self) -> bool:
        return self._state in (_STATE_UNCERTAIN, _STATE_RETARGET)

    def observe(self, frame, now: float = 0.0) -> CombatObservation:
        """跑一帧全部识别，返回观测。"""
        report = recognize(frame, find=self._find)
        if not report.frame_ok:
            self._last = CombatObservation(frame_ok=False)
            return self._last
        return self._apply(report, now)

    # ── 内部 ──────────────────────────────────────────────

    def _apply(self, report: RecognitionReport, now: float) -> CombatObservation:
        # 只用"场上有敌人"的信号；游戏自带自动战斗的 UI 随处可见，不能算战斗
        signal = report.health is not None or report.lock is not None
        ended = False

        if report.result_visible:
            self._to_out(0.0)  # 结算界面：不进入扫描窗口
            ended = True
        elif self._state == _STATE_OUT:
            self._step_out(signal, now)
        elif self._state == _STATE_IN:
            self._step_in(signal, now)
        else:
            self._step_recover(signal, now)

        self._last = CombatObservation(
            in_combat=self.in_combat,
            ended=ended,
            game_auto=report.game_auto,
            enemy_hp_ratio=report.health.fill_ratio if report.health is not None else None,
            lock_on=report.lock is not None,
            current_slot=report.slot.index if report.slot is not None and report.slot.accepted else None,
            retargeting=self.retargeting,
        )
        return self._last

    def _step_out(self, signal: bool, now: float) -> None:
        """不在战斗：等信号入战；同时定期按中键扫描敌人。"""
        if signal:
            self._enter_hits = min(self._enter_hits + 1, self.enter_frames)
            if self._enter_hits >= self.enter_frames:
                self._state = _STATE_IN
                self._miss = 0
                self._revive_hits = 0
                self._retarget_count = 0
                self._sweep_until = 0.0
            return
        self._enter_hits = 0
        # 只在"刚打完一场"的窗口内扫描，避免一直在后台按中键抢鼠标
        if now >= self._sweep_until:
            return
        if now >= self._next_sweep_at:
            self._click()
            self._next_sweep_at = now + self.sweep_interval

    def _step_in(self, signal: bool, now: float) -> None:
        if signal:
            self._miss = 0
            return
        self._miss += 1
        if self._miss >= self.exit_frames:
            self._state = _STATE_UNCERTAIN
            self._uncertain_until = now + self.uncertain_seconds
            self._revive_hits = 0
            self._click()

    def _step_recover(self, signal: bool, now: float) -> None:
        if signal:
            self._revive_hits += 1
            if self._revive_hits >= self.revive_frames:
                self._state = _STATE_IN
                self._miss = 0
                self._revive_hits = 0
                self._uncertain_until = 0.0
                self._retarget_until = 0.0
            return

        self._revive_hits = 0
        if self._state == _STATE_UNCERTAIN:
            if now >= self._uncertain_until:
                self._start_retarget(now)
            return
        if now >= self._retarget_until:
            self._to_out(now)
            return
        if now >= self._next_click_at:
            self._click()
            self._next_click_at = now + self.click_interval

    def _start_retarget(self, now: float) -> None:
        """不确定期结束：有预算就中键索敌，超预算直接脱战。"""
        self._uncertain_until = 0.0
        if self._retarget_count >= self.max_retargets:
            self._to_out(now)
            return
        self._retarget_count += 1
        self._state = _STATE_RETARGET
        self._retarget_until = now + self.retarget_seconds
        self._next_click_at = now + self.click_interval
        self._click()

    def _to_out(self, now: float = 0.0) -> None:
        self._state = _STATE_OUT
        self._sweep_until = now + self.sweep_seconds if now else 0.0
        self._enter_hits = 0
        self._miss = 0
        self._revive_hits = 0
        self._uncertain_until = 0.0
        self._retarget_until = 0.0
        self._next_sweep_at = 0.0

    def _click(self) -> None:
        if self.middle_click is None:
            return
        try:
            self.middle_click()
        except Exception:
            return
