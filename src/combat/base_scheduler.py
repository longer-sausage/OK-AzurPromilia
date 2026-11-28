from __future__ import annotations

from typing import TYPE_CHECKING

from src.char.base_char import SwitchPriority

if TYPE_CHECKING:
    from src.char.base_char import BaseChar
    from src.combat.combat import Combat


class BaseScheduler:
    """每场战斗独立创建。可覆写选人、单轮行动，或接管整场调度。"""

    _dispatched: bool = False

    def __init__(self, combat: Combat) -> None:
        self.combat = combat

    def run(self) -> None:
        for _ in self.combat.frames():
            self.step()

    def step(self) -> None:
        combat = self.combat
        current = combat.get_current_char()
        if current is None:
            combat.raise_not_in_combat("cannot find current char")
        current.perform()
        if not combat.state.active or combat.get_current_char() is not current:
            return
        target = self.choose_switch_target(current)
        if target is None:
            combat.sleep(0.1)
        else:
            combat.switch_to(target)

    def choose_switch_target(self, current: BaseChar) -> BaseChar | None:
        """优先级最高的一组中优先选冷却已结束、最久未上场的角色，平局按槽位。"""
        candidates = []
        for char in self.combat.chars:
            if char is current:
                continue
            priority = char.get_switch_priority(current_char=current)
            if priority > SwitchPriority.NO:
                rank = (-priority, self.combat.on_switch_cooldown(char), char.last_switch_in_time, char.index)
                candidates.append((rank, char))
        return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None
