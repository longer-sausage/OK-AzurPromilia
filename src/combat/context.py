"""角色侧 API：角色通过它向 planner 发请求，而不是直接改 planner 状态。

本版只保留战斗必需的三类请求。
"""

from __future__ import annotations

from src.combat.enums import ActionSlot
from src.combat.requests import ReservationRequest, RouteRequest, SwitchRequest
from src.combat.types import RouteStep


class CombatContext:
    """通过 ``BaseCombatChar.combat_context`` 拿到，供角色脚本使用。"""

    def __init__(self, task, char) -> None:
        self._task = task
        self._char = char

    @property
    def task(self):
        return self._task

    @property
    def char(self):
        return self._char

    @property
    def chars(self) -> list:
        return self._task.chars

    def _now(self) -> float:
        return self._task.active_time()

    def request_route(
        self,
        steps: list[RouteStep],
        reason: str = "",
        timeout: float | None = None,
    ) -> RouteRequest:
        """发布一条固定顺序的协作路线（同时只允许一条生效）。"""
        deadline = None if timeout is None else self._now() + timeout
        request = RouteRequest(self._char.index, steps, reason=reason, deadline=deadline)
        self._task.planner.state.add_request(request)
        self._task.log_debug(f"{self._char.cn_name or self._char.name} 发布路线：{reason}（{len(steps)} 步）")
        return request

    def reserve_action(self, slot: ActionSlot, until: float = 0.0, reason: str = "") -> ReservationRequest:
        """保留自己的某个槽位动作。"""
        deadline = None if until <= 0 else self._now() + until
        request = ReservationRequest(self._char.index, slot, until=0.0, reason=reason)
        request.deadline = deadline
        self._task.planner.state.add_request(request)
        return request

    def request_switch(self, target_index: int | None = None, reason: str = "", timeout: float = 5.0) -> SwitchRequest:
        """请求下一次调度切给目标角色（默认切自己）。"""
        target = self._char.index if target_index is None else int(target_index)
        request = SwitchRequest(
            self._char.index,
            target,
            reason=reason,
            deadline=self._now() + timeout,
        )
        self._task.planner.state.add_request(request)
        return request
