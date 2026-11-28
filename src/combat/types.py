"""战斗数据模型：动作、计划、诉求、切人决策。

`ActionSpec` ≈ `ActionIntent`（简化掉 generator 协议），
`CombatPlan` ≈ `CombatPlan`，`FieldClaim` / `RoleProfile` / `SwitchInGuard` /
`SwitchDecision` / `ExpectedEntry` 与 同名同义。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.combat.enums import (
    ACTION_TAG_SCORES,
    ActionSlot,
    ActionTag,
    FieldClaimLevel,
    FieldPreference,
    Role,
)


@dataclass(slots=True)
class ActionResult:
    """一次动作的执行结果。"""

    success: bool
    reason: str = ""

    @classmethod
    def ok(cls, reason: str = "") -> ActionResult:
        return cls(True, reason)

    @classmethod
    def fail(cls, reason: str = "") -> ActionResult:
        return cls(False, reason)


@dataclass(slots=True)
class ActionSpec:
    """角色声明给 planner 的候选动作（不可变声明）。"""

    name: str
    slot: ActionSlot
    tags: frozenset[ActionTag]
    priority: int = 0
    key: str | None = None
    hold: float = 0.02
    after_sleep: float = 0.05
    cooldown: float = 0.0
    #: 额外可用条件（资源、连招窗口等）；None 表示只看冷却
    can_execute: Callable[[], bool] | None = None
    #: 自定义执行体；None 表示按 key 按键
    executor: Callable[[], ActionResult | bool | None] | None = None
    #: 是否参与切人评分
    scored: bool = True

    def tag_score(self) -> int:
        return sum(ACTION_TAG_SCORES.get(tag, 0) for tag in self.tags)


@dataclass(slots=True)
class FieldClaim:
    """角色声明「我应该被切进来」的理由。"""

    level: FieldClaimLevel
    reason: str = ""
    expected_entry: str | None = None


@dataclass(slots=True)
class RoleProfile:
    """角色向 planner 声明的基础战斗画像。"""

    role: Role = Role.SUB_DPS
    field_preference: FieldPreference | None = None
    max_field_time: float = 1.5
    combat_start_priority: int = 0

    def resolved_preference(self) -> FieldPreference:
        if self.field_preference is not None:
            return self.field_preference
        return {
            Role.MAIN_DPS: FieldPreference.MAIN_DPS,
            Role.SUB_DPS: FieldPreference.SUB_DPS,
            Role.SUPPORT: FieldPreference.SUPPORT,
        }[self.role]


@dataclass(slots=True)
class SwitchInGuard:
    """目标角色切入前的保护条件（对应 `SwitchInGuard`）。"""

    allowed: bool = True
    condition: Callable[[], bool] | None = None
    timeout: float = 3.0
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> SwitchInGuard:
        return cls(allowed=True, reason=reason)

    @classmethod
    def delay_until_ready(
        cls,
        condition: Callable[[], bool],
        timeout: float = 3.0,
        reason: str = "",
    ) -> SwitchInGuard:
        return cls(allowed=False, condition=condition, timeout=timeout, reason=reason)

    def should_delay(self) -> bool:
        if self.allowed:
            return False
        if self.condition is None:
            return False
        return not self.condition()


@dataclass(slots=True)
class ExpectedEntry:
    """切入目标后应优先尝试的动作。"""

    action_name: str | None = None
    slot: ActionSlot | None = None

    def matches(self, action: ActionSpec) -> bool:
        if self.action_name is not None and action.name == self.action_name:
            return True
        return self.slot is not None and action.slot is self.slot


@dataclass(slots=True)
class SwitchDecision:
    """切人决策结果。"""

    target_index: int | None
    reason: str = ""
    priority: int = 0
    expected_entry: ExpectedEntry | None = None
    skip_waits: bool = False


@dataclass(slots=True)
class RouteStep:
    """协作路线的一步：切到某人并放某个动作。"""

    target_index: int | None = None
    action_name: str | None = None
    note: str = ""
    optional: bool = False


@dataclass(slots=True)
class CombatPlan:
    """角色一次 planner 查询的完整计划（对应 `CombatPlan`）。"""

    actions: list[ActionSpec] = field(default_factory=list)
    claims: list[FieldClaim] = field(default_factory=list)
    route: list[RouteStep] = field(default_factory=list)
    entry: Callable[[], ActionResult | bool | None] | None = None
    #: 出招表（动作名序列，一拍推进一步）；完成或全不可用后退回常规评分
    entry_steps: tuple[str, ...] = ()

    def register(self, action: ActionSpec) -> ActionSpec:
        if any(existing.name == action.name for existing in self.actions):
            raise ValueError(f"duplicate action name: {action.name}")
        self.actions.append(action)
        return action

    def add(self, item) -> None:
        if isinstance(item, ActionSpec):
            self.register(item)
        elif isinstance(item, FieldClaim):
            self.claims.append(item)
        else:
            raise TypeError(f"unsupported plan item: {type(item)!r}")

    def sorted_actions(self) -> list[ActionSpec]:
        """按优先级、标签分排序，同分保持注册顺序。"""
        return sorted(
            self.actions,
            key=lambda action: (-action.priority, -action.tag_score()),
        )
