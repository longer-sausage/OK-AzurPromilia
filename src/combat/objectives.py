"""战斗目标（模式）：决定一场战斗什么时候停。

两种模式共用全部战斗逻辑，唯一差别是 `should_stop`：
- 全自动：检测到脱战/结算就停；
- 捉宠：敌人血量比例连续 N 帧低于阈值就停手（随后可自动星结）。
"""

from __future__ import annotations

from src.combat import profile
from src.combat.enums import CombatMode, StopReason


class CombatObjective:
    """战斗终止策略基类。"""

    mode: CombatMode = CombatMode.FULL_AUTO

    def reset(self) -> None:
        """新一场战斗开始时重置内部计数。"""

    def should_stop(self, task, observation, now: float) -> StopReason | None:
        raise NotImplementedError

    def on_stop(self, task, reason: StopReason) -> None:
        """停手后的额外动作（默认无）。"""


class FullAutoObjective(CombatObjective):
    """全自动：脱战 / 结算 / 超时。"""

    mode = CombatMode.FULL_AUTO

    def __init__(
        self,
        timeout: float = profile.SESSION_TIMEOUT,
        min_seconds: float = profile.MIN_SESSION_SECONDS,
    ) -> None:
        self.timeout = max(0.0, float(timeout))
        self.min_seconds = max(0.0, float(min_seconds))

    def should_stop(self, task, observation, now: float) -> StopReason | None:
        if not observation.frame_ok:
            return None
        if observation.ended:
            return StopReason.BATTLE_ENDED
        if self.timeout > 0 and now - task.battle_started_at >= self.timeout:
            return StopReason.TIMEOUT
        # 进战太短：血条可能被特效/镜头短暂遮住，先不动，等有把握再判
        if now - task.battle_started_at < self.min_seconds:
            return None
        if not observation.in_combat:
            return StopReason.OUT_OF_COMBAT
        return None


class CaptureObjective(FullAutoObjective):
    """捉宠：血量比例降到阈值以下就停手，其余与全自动一致。"""

    mode = CombatMode.CAPTURE

    def __init__(
        self,
        hp_threshold: float = profile.CAPTURE_HP_THRESHOLD,
        confirm_frames: int = profile.HP_CONFIRM_FRAMES,
        timeout: float = profile.SESSION_TIMEOUT,
    ) -> None:
        super().__init__(timeout)
        self.hp_threshold = min(1.0, max(0.0, float(hp_threshold)))
        self.confirm_frames = max(1, int(confirm_frames))
        self._hits = 0

    def reset(self) -> None:
        self._hits = 0

    def should_stop(self, task, observation, now: float) -> StopReason | None:
        hp = observation.enemy_hp_ratio
        if hp is not None and hp <= self.hp_threshold:
            self._hits += 1
            if self._hits >= self.confirm_frames:
                return StopReason.HP_THRESHOLD
        else:
            self._hits = 0
        return super().should_stop(task, observation, now)
