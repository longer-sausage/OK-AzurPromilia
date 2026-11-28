"""角色协作请求：路线 / 保留 / 切人。

有 6 种请求；这里先实现战斗真正用到的 3 种，接口形式保持一致，
以后要加 tag/role/window 请求按同样模式扩。
"""

from __future__ import annotations

from enum import StrEnum

from src.combat.types import RouteStep


class RequestStatus(StrEnum):
    PENDING = "pending"
    FULFILLED = "fulfilled"
    EXPIRED = "expired"
    FAILED = "failed"


class CombatRequest:
    """所有请求的共同生命周期。"""

    def __init__(self, owner_index: int, reason: str = "", deadline: float | None = None) -> None:
        self.owner_index = owner_index
        self.reason = reason
        self.deadline = deadline
        self.status = RequestStatus.PENDING

    @property
    def active(self) -> bool:
        return self.status is RequestStatus.PENDING

    def expired(self, now: float) -> bool:
        return self.deadline is not None and now > self.deadline

    def fulfill(self) -> None:
        self.status = RequestStatus.FULFILLED

    def fail(self) -> None:
        self.status = RequestStatus.FAILED

    def close(self) -> None:
        if self.status is RequestStatus.PENDING:
            self.status = RequestStatus.EXPIRED


class RouteRequest(CombatRequest):
    """固定顺序的协作路线：切到某人放某个动作，再切下一个人。"""

    def __init__(self, owner_index: int, steps: list[RouteStep], reason: str = "", deadline: float | None = None):
        super().__init__(owner_index, reason, deadline)
        if not steps:
            raise ValueError("route must have at least one step")
        self.steps = list(steps)
        self.index = 0

    def current_step(self) -> RouteStep | None:
        if self.index >= len(self.steps):
            return None
        return self.steps[self.index]

    def advance(self) -> None:
        self.index += 1

    @property
    def done(self) -> bool:
        return self.index >= len(self.steps)


class ReservationRequest(CombatRequest):
    """保留某角色的某个槽位动作，别人不要抢着用。"""

    def __init__(self, owner_index: int, slot, until: float = 0.0, reason: str = "") -> None:
        super().__init__(owner_index, reason, deadline=until if until > 0 else None)
        self.slot = slot

    def reserves(self, char_index: int, slot) -> bool:
        return self.active and self.owner_index == char_index and self.slot is slot


class SwitchRequest(CombatRequest):
    """请求下一次调度把某人切上来。"""

    def __init__(self, owner_index: int, target_index: int, reason: str = "", deadline: float | None = None):
        super().__init__(owner_index, reason, deadline)
        self.target_index = target_index

    def fulfilled_by(self, target_index: int) -> bool:
        return self.active and target_index == self.target_index
