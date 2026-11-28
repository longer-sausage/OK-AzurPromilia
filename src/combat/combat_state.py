from dataclasses import dataclass, field


@dataclass
class SlotState:
    """同一场战斗中，一个槽位最近一次切出、切入和开始行动的时间。"""

    last_switch_time: float = -1.0
    last_switch_in_time: float = -1.0
    last_perform: float = -1.0


@dataclass
class CombatState:
    """一场战斗的共享状态。角色和调度器始终读取 Combat 持有的同一个实例。"""

    active: bool = False
    current_index: int | None = None
    slots: tuple[SlotState, ...] = ()
    started_at: float = 0.0
    end_reason: str = ""
    skip_combat_check: bool = False
    freeze_durations: list[tuple[float, float, float]] = field(default_factory=list)

    def reset_team(self, team_size: int) -> None:
        self.current_index = None
        self.slots = tuple(SlotState() for _ in range(team_size))
        self.started_at = 0.0
        self.freeze_durations.clear()

    def set_current(self, index: int, now: float) -> None:
        """同一次 HUD 确认同时记录切出和切入。重复看到同一角色时保留原时间。"""
        if not 0 <= index < len(self.slots):
            raise IndexError(f"Invalid team slot: {index}")
        if self.current_index == index:
            return
        if self.current_index is not None:
            self.slots[self.current_index].last_switch_time = now
        self.slots[index].last_switch_in_time = now
        self.current_index = index
