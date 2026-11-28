from __future__ import annotations

import time
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from ok import Logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.combat.base_combat_task import BaseCombatTask


class SwitchPriority(IntEnum):
    """切人优先级档位；每档间隔 100，便于子类做细粒度微调（如 ``HIGH + 1``）。"""

    NO = 0
    LOW = 100
    NORMAL = 200
    HIGH = 300
    MUST = 400


class BaseChara:
    """战斗角色基类，定义角色生命周期、切人调度钩子与战斗安全延时。

    具体角色子类通常只需覆写 ``do_perform``，按需覆写 ``get_switch_priority``、
    ``wait_switch``、``skip_combat_check``、``reset_state``、``on_combat_end``。
    """

    def __init__(
        self,
        task: BaseCombatTask | None,
        index: int,
        char_name: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        self.task = task
        self.index = index
        self.char_name = char_name or self.__class__.__name__
        self.confidence = float(confidence)
        self.is_current_char = False
        self.last_switch_time = -1.0
        self.last_switch_in_time = -1.0
        self.last_perform = -1.0
        self.logger = Logger.get_logger(self.name)

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def __repr__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        if isinstance(other, BaseChara):
            return self.char_name == other.char_name and self.name == other.name and self.index == other.index
        return False

    def _now(self) -> float:
        if self.task is not None and hasattr(self.task, "combat_time"):
            return self.task.combat_time()
        return time.monotonic()

    def perform(self) -> None:
        """执行当前角色的出战轮次（包装 ``do_perform`` 以统一计时与日志）。"""
        self.last_perform = self._now()
        self.do_perform()
        self.logger.debug(f"perform finished char={self.name} index={self.index}")

    def do_perform(self) -> None:
        """具体角色的场上战斗逻辑；子类应覆写此方法。"""
        if self.task is None:
            return
        if self.switch_next_char() is None:
            self.sleep(0.1)

    def switch_in(self) -> None:
        """角色切上场时的状态更新。"""
        self.is_current_char = True
        self.last_switch_in_time = self._now()

    def switch_out(self) -> None:
        """角色切下场时的状态更新。"""
        self.is_current_char = False
        self.last_switch_time = self._now()

    def switch_next_char(self, post_action: Callable[[BaseChara], Any] | None = None) -> BaseChara | None:
        """委托战斗任务切换到下一个候选角色。"""
        if self.task is None:
            return None
        return self.task.switch_next_char(self, post_action=post_action)

    def sleep(self, sec: float, check_combat: bool = True) -> None:
        """战斗安全休眠；``check_combat=False`` 时临时屏蔽睡眠期脱战检查且支持嵌套恢复。"""
        if self.task is None:
            return
        previous_skip = getattr(self.task, "skip_combat_check", False)
        if not check_combat:
            self.task.skip_combat_check = True
        try:
            self.task.sleep(sec)
        finally:
            self.task.skip_combat_check = previous_skip

    def check_combat(self) -> None:
        """检查是否仍在战斗中，脱战时由任务抛出 ``NotInCombatException``。"""
        if self.task is not None:
            self.task.check_combat()

    def reset_state(self) -> None:
        """重置单场战斗内的临时状态；子类可扩展并调用 ``super().reset_state()``。"""
        self.last_switch_time = -1.0
        self.last_switch_in_time = -1.0
        self.last_perform = -1.0

    def on_combat_end(self, chars: list[BaseChara | None]) -> None:
        """战斗结束时在当前出战角色上触发的清理钩子。"""

    def skip_combat_check(self) -> bool:
        """返回 ``True`` 可在特殊动画/状态期间跳过脱战判定。"""
        return False

    def get_switch_priority(self, current_char: BaseChara | None = None) -> int:
        """返回作为切人目标的优先级，返回 ``SwitchPriority.NO`` 表示禁止切入。"""
        return SwitchPriority.NORMAL

    def wait_switch(self) -> bool:
        """返回 ``True`` 表示暂缓切入该角色。"""
        return False

    def add_freeze_duration(self, start: float, duration: float = -1.0, freeze_time: float = 0.1) -> None:
        """记录时停/动画冻结耗时，用于扣除冷却与计时偏差。"""
        if self.task is not None:
            self.task.add_freeze_duration(start, duration=duration, freeze_time=freeze_time)

    def time_elapsed_accounting_for_freeze(self, start: float) -> float:
        """计算扣除冻结时长后的实际经过时间。"""
        if self.task is not None:
            return self.task.time_elapsed_accounting_for_freeze(start)
        if start < 0:
            return 10000.0
        return max(0.0, time.monotonic() - start)
