"""角色基类：声明动作计划、执行单个动作、提供诉求与入场保护。

- ``combat_plan()`` 声明技能 / 大招 / 普攻等动作，以及诉求和出招表；
- 按触发任务的约定，一次只推进一个动作，不阻塞整个战斗循环。
"""

from __future__ import annotations

from src.combat import profile
from src.combat.context import CombatContext
from src.combat.enums import ActionSlot, ActionTag, Role
from src.combat.types import (
    ActionResult,
    ActionSpec,
    CombatPlan,
    FieldClaim,
    RoleProfile,
    SwitchInGuard,
)


class BaseCombatChar:
    """角色基类。子类覆盖 ``combat_plan`` / ``describe_role`` 即可。"""

    char_id = "generic"
    cn_name = "通用角色"

    def __init__(self, task, index: int, impl_id: str = "") -> None:
        self.task = task
        self.index = int(index)
        self.impl_id = impl_id or self.char_id
        self.is_current_char = False
        self.is_dead = False
        self.has_intro = False
        self.last_perform = 0.0
        self.last_switch = 0.0
        self._plan: CombatPlan | None = None
        #: 每个动作下一次可用的时间
        self._next_ready: dict[str, float] = {}
        self._entry_index = 0
        self._entry_finished = False

    # ── 基础信息 ──────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def display_name(self) -> str:
        return self.cn_name or self.name

    @property
    def combat_context(self) -> CombatContext:
        return CombatContext(self.task, self)

    @property
    def plan(self) -> CombatPlan:
        if self._plan is None:
            self._plan = self.combat_plan() or CombatPlan()
        return self._plan

    def action(self, name: str) -> ActionSpec | None:
        for action in self.plan.actions:
            if action.name == name:
                return action
        return None

    # ── 出招表────────────

    def next_entry_step(self, now: float | None = None) -> ActionSpec | None:
        """出招表的下一步；不可用的步骤直接跳过，走完就结束本套连招。"""
        steps = self.plan.entry_steps
        if not steps or self._entry_finished:
            return None
        while self._entry_index < len(steps):
            action = self.action(steps[self._entry_index])
            self._entry_index += 1
            if action is not None and self.available(action, now):
                if self._entry_index >= len(steps):
                    self._entry_finished = True
                return action
        self._entry_finished = True
        return None

    @property
    def entry_active(self) -> bool:
        return bool(self.plan.entry_steps) and not self._entry_finished

    def reset_entry(self) -> None:
        self._entry_index = 0
        self._entry_finished = False

    # ── 子类扩展点 ────────────────────────────────────────

    def combat_plan(self) -> CombatPlan:
        """默认出招表：技能 → 大招 → 普攻（按优先级，可用配置关掉）。"""
        plan = CombatPlan()
        if self.task.config_value("释放技能", True):
            plan.register(
                ActionSpec(
                    name="技能",
                    slot=ActionSlot.SKILL,
                    tags=frozenset({ActionTag.SKILL_ACTION}),
                    priority=100,
                    key=profile.KEY_SKILL,
                    cooldown=float(self.task.config_value("_技能冷却(秒)", profile.SKILL_COOLDOWN)),
                    after_sleep=0.05,
                )
            )
        if self.task.config_value("释放大招", True):
            plan.register(
                ActionSpec(
                    name="大招",
                    slot=ActionSlot.ULTIMATE,
                    tags=frozenset({ActionTag.ULTIMATE_ACTION}),
                    priority=200,
                    key=profile.KEY_ULTIMATE,
                    cooldown=float(self.task.config_value("_大招冷却(秒)", profile.ULTIMATE_COOLDOWN)),
                    after_sleep=0.1,
                )
            )
        if self.task.config_value("释放E技能", True):
            plan.register(
                ActionSpec(
                    name="E技能",
                    slot=ActionSlot.SKILL2,
                    tags=frozenset({ActionTag.SKILL_ACTION}),
                    priority=90,
                    key=profile.KEY_SKILL2,
                    cooldown=float(self.task.config_value("_副技能冷却(秒)", profile.SKILL2_COOLDOWN)),
                    after_sleep=0.05,
                )
            )
        if self.task.config_value("自动普攻", True):
            plan.register(
                ActionSpec(
                    name="普攻",
                    slot=ActionSlot.ATTACK,
                    tags=frozenset({ActionTag.DEFAULT_ACTION, ActionTag.DAMAGE}),
                    priority=0,
                    key=profile.KEY_ATTACK,
                    cooldown=float(self.task.config_value("_普攻间隔(秒)", profile.ATTACK_INTERVAL)),
                    after_sleep=0.02,
                    # 普攻不进 plan 评分，只在站场时兜底执行
                    scored=False,
                )
            )
        return plan

    def rebuild_plan(self) -> None:
        """配置变化后重建出招表。"""
        self._plan = None

    def describe_role(self) -> RoleProfile:
        return RoleProfile(role=Role.SUB_DPS)

    def claims(self) -> list[FieldClaim]:
        """当前这一刻的入场诉求（默认取计划里声明的静态诉求）。"""
        return list(self.plan.claims)

    def switch_in_guard(self) -> SwitchInGuard:
        """是否允许被切入：默认立即允许。"""
        return SwitchInGuard.allow()

    # ── 可用性与执行 ──────────────────────────────────────

    def available(self, action: ActionSpec, now: float | None = None) -> bool:
        now = self.task.active_time() if now is None else now
        if action.can_execute is not None and not action.can_execute():
            return False
        if now < self._next_ready.get(action.name, 0.0):
            return False
        # 只有上场角色的技能/大招按钮在画面上；替补角色的就绪状态读不到，
        # 一律按“未知”处理，否则所有角色都会被判成“大招可用”，导致来回切。
        if not self.is_current_char:
            return True
        # E 技能的按钮指示和 Q/R 不一样（没有蓝环，冷却时显示数字）：
        # 读得到冷却数字就是没好，读不到就算可用。
        if action.slot is ActionSlot.SKILL2:
            reader = getattr(self.task, "slot_cooldown_seconds", None)
            seconds = None
            if reader is not None:
                try:
                    seconds = reader(action.slot)
                except Exception:
                    seconds = None
            if seconds and seconds > 0:
                self._next_ready[action.name] = now + max(0.5, seconds)
                return False
            return True
        hint = self.task.slot_ready(action.slot)
        if hint is False:
            # 上场后发现没好：读按钮上的冷却秒数作为真实冷却；
            # 读不到才退回默认重试间隔。统一封锁固定值会让切人变慢。
            retry = float(self.task.config_value("_就绪重试间隔(秒)", 2.0))
            seconds = None
            reader = getattr(self.task, "slot_cooldown_seconds", None)
            if reader is not None:
                try:
                    seconds = reader(action.slot)
                except Exception:
                    seconds = None
            block = seconds if seconds and seconds > 0 else retry
            self._next_ready[action.name] = now + max(0.5, block)
            return False
        return True

    def execute(self, action: ActionSpec) -> ActionResult:
        now = self.task.active_time()
        self._next_ready[action.name] = now + max(0.0, action.cooldown)
        self.last_perform = now
        if action.executor is not None:
            raw = action.executor()
        elif action.key:
            self.task.press(action.key, hold=action.hold, after_sleep=action.after_sleep)
            raw = True
        else:
            raw = True
        # 真按键时也要留痕，否则日志里看不出到底放没放技能
        if action.slot in (ActionSlot.SKILL, ActionSlot.ULTIMATE):
            self.task.log_info(f"{self.display_name} 释放 {action.name}")
        else:
            self.task.log_debug(f"{self.display_name} 普攻")
        if isinstance(raw, ActionResult):
            return raw
        return ActionResult.ok(action.name) if raw is not False else ActionResult.fail(action.name)

    # ── 状态 ──────────────────────────────────────────────

    def reset(self) -> None:
        self._next_ready.clear()
        self.reset_entry()
        self.is_dead = False
        self.has_intro = False

    def switch_out(self) -> None:
        self.is_current_char = False
        self.has_intro = False

    def switch_in(self, now: float) -> None:
        self.is_current_char = True
        self.last_switch = now
        self.has_intro = False
        self.reset_entry()

    def mark_dead(self, reason: str = "") -> None:
        if not self.is_dead:
            self.task.log_info(f"{self.display_name} 阵亡：{reason}")
        self.is_dead = True
        self.is_current_char = False

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{self.name}#{self.index}>"
