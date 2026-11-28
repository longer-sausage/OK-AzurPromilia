"""战斗运行状态：请求、锁定路线、切入期望。"""

from __future__ import annotations

from src.combat.requests import CombatRequest, RouteRequest, SwitchRequest
from src.combat.types import ExpectedEntry


class CombatState:
    """planner 的运行状态容器。"""

    def __init__(self) -> None:
        self.chars: list = []
        self.requests: list[CombatRequest] = []
        self.locked_route: RouteRequest | None = None
        self.pending_entry: dict[int, ExpectedEntry] = {}
        #: 最近一次切人的时间：用来做全局切人冷却，防止来回切
        self.last_switch_at = 0.0

    def reset(self, chars) -> None:
        self.chars = list(chars)
        self.requests.clear()
        self.locked_route = None
        self.pending_entry.clear()
        self.last_switch_at = 0.0

    def add_request(self, request: CombatRequest) -> None:
        if isinstance(request, RouteRequest):
            if self.locked_route is not None and self.locked_route.active:
                request.close()  # 同时只允许一条锁定路线
                return
            self.locked_route = request
        self.requests.append(request)

    def prune(self, now: float) -> None:
        for request in self.requests:
            if request.active and request.expired(now):
                request.close()
        self.requests = [request for request in self.requests if request.active]
        if self.locked_route is not None and not self.locked_route.active:
            self.locked_route = None

    def active_requests(self) -> list[CombatRequest]:
        return [request for request in self.requests if request.active]

    def has_active_request(self) -> bool:
        return any(request.active for request in self.requests)

    def switch_request_for(self, target_index: int) -> SwitchRequest | None:
        for request in self.requests:
            if isinstance(request, SwitchRequest) and request.fulfilled_by(target_index):
                return request
        return None

    def record_switch(self, target_index: int, now: float = 0.0) -> None:
        """记录一次实际切人，消费匹配的切人请求。"""
        self.last_switch_at = now
        request = self.switch_request_for(target_index)
        if request is not None:
            request.fulfill()

    def set_expected_entry(self, char_index: int, entry: ExpectedEntry | None) -> None:
        if entry is None:
            self.pending_entry.pop(char_index, None)
        else:
            self.pending_entry[char_index] = entry

    def pop_expected_entry(self, char_index: int) -> ExpectedEntry | None:
        return self.pending_entry.pop(char_index, None)

    def finish_route(self, fulfilled: bool) -> None:
        route = self.locked_route
        if route is None:
            return
        if fulfilled:
            route.fulfill()
        else:
            route.fail()
        self.locked_route = None
