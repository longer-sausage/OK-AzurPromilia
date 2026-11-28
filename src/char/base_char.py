from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

from ok import Logger

if TYPE_CHECKING:
    from src.combat.combat import Combat
    from src.combat.combat_state import CombatState
    from src.core.base_game_task import BaseGameTask


class SwitchPriority(IntEnum):
    """默认调度器的切人优先级，可用 HIGH + 1 等数值细调。"""

    NO = 0
    LOW = 100
    NORMAL = 200
    HIGH = 300
    MUST = 400


class BaseChar:
    """角色的身份和专属动作。战斗信息从所属 Combat 的共享状态读取。"""

    def __init__(
        self,
        combat: Combat,
        index: int,
        char_name: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        self.combat = combat
        self.index = index
        self.char_name = char_name or self.__class__.__name__
        self.confidence = float(confidence)
        self.logger = Logger.get_logger(self.name)

    @property
    def task(self) -> BaseGameTask:
        return self.combat.task

    @property
    def state(self) -> CombatState:
        return self.combat.state

    @property
    def is_current_char(self) -> bool:
        return self.combat.get_current_char() is self

    @property
    def last_switch_time(self) -> float:
        return self.combat.slot_state(self).last_switch_time

    @property
    def last_switch_in_time(self) -> float:
        return self.combat.slot_state(self).last_switch_in_time

    @property
    def last_perform(self) -> float:
        return self.combat.slot_state(self).last_perform

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def __repr__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        if isinstance(other, BaseChar):
            return self.char_name == other.char_name and self.name == other.name and self.index == other.index
        return False

    def perform(self) -> None:
        """将行动起点记入全队可见的槽位记录，然后执行专属动作。"""
        self.combat.record_perform(self)
        self.do_perform()
        self.logger.debug(f"perform finished char={self.name} index={self.index}")

    def do_perform(self) -> None:
        """子类在此实现本角色的场上动作，返回后由调度器决定下一步。"""

    def sleep(self, sec: float, check_combat: bool = True) -> None:
        self.combat.sleep(sec, check_combat=check_combat)

    def check_combat(self) -> None:
        self.combat.check_combat()

    def add_freeze_duration(self, start: float, duration: float = -1.0, freeze_time: float = 0.1) -> None:
        self.combat.add_freeze_duration(start, duration=duration, freeze_time=freeze_time)

    def time_elapsed_accounting_for_freeze(self, start: float) -> float:
        return self.combat.time_elapsed_accounting_for_freeze(start)

    def on_combat_end(self, chars: list[BaseChar]) -> None:
        """战斗正常结束时，当前出战角色可以在共享状态清理前执行收尾动作。"""

    def skip_combat_check(self) -> bool:
        return False

    def get_switch_priority(self, current_char: BaseChar | None = None) -> int:
        return SwitchPriority.NORMAL

    def wait_switch(self) -> bool:
        """返回 True 表示当前暂缓向该角色发送切入按键。"""
        return False
