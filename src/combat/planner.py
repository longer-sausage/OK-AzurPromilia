"""战斗调度器：决定「当前角色这一拍做什么」和「该切给谁」。

- ``perform_current_char``：锁定路线优先，其次出招表，最后按计划顺序打**一个**动作；
- ``decide_switch``：锁定路线 → STRICT 诉求 → 切人请求 → 评分；
- 评分 = 该角色最佳动作的标签分 + 最高诉求分 + 站场偏好 − 切人冷却惩罚。

一次只推进一个动作（不阻塞整轮），请求只保留路线 / 保留 / 切人三种。
"""

from __future__ import annotations

from src.combat import profile
from src.combat.enums import (
    FIELD_CLAIM_SCORES,
    FIELD_PREFERENCE_REQUEST_SCORES,
    FIELD_PREFERENCE_SCORES,
    ROUTE_PRIORITY,
    STRICT_CLAIM_PRIORITY,
    SWITCH_COOLDOWN_PENALTY,
    FieldClaimLevel,
    FieldPreference,
)
from src.combat.requests import ReservationRequest, SwitchRequest
from src.combat.state import CombatState
from src.combat.types import ActionResult, ExpectedEntry, SwitchDecision

#: 切人请求的固定优先级：低于 STRICT 诉求，高于普通评分
SWITCH_REQUEST_PRIORITY = 900_000
#: 开战首切优先级：高于普通评分，低于请求
COMBAT_START_PRIORITY = 800_000
#: 角色自留动作带来的评分加成
RESERVATION_BONUS = 150
#: 没有候选时的兜底优先级
NO_CANDIDATE = -999_999


class CombatPlanner:
    """队伍战斗调度器。"""

    def __init__(self, task) -> None:
        self.task = task
        self.state = CombatState()
        #: 本拍实际执行的动作（供上层决定「出招这一拍是否还切人」）
        self.last_action = None

    def reset(self, chars) -> None:
        self.state.reset(chars)

    # ── 一拍 ──────────────────────────────────────────────

    def perform_current_char(self, char) -> ActionResult | None:
        """当前角色这一拍做什么：路线 → 出招表 → 计划评分。"""
        self.last_action = None
        self.state.prune(self.task.active_time())
        route = self.state.locked_route
        if route is not None:
            result = self._run_route_step(char, route)
            if result is not None:
                return result
        step = char.next_entry_step(self.task.active_time())
        if step is not None:
            self.last_action = step
            return char.execute(step)
        return self._run_plan_action(char)

    def _run_plan_action(self, char) -> ActionResult | None:
        expected = self.state.pop_expected_entry(char.index)
        actions = char.plan.sorted_actions()
        if expected is not None:
            actions = sorted(
                actions,
                key=lambda action: (not expected.matches(action), -action.priority, -action.tag_score()),
            )
        for action in actions:
            if self.action_allowed(char, action):
                self.last_action = action
                return char.execute(action)
        return None

    def _run_route_step(self, char, route) -> ActionResult | None:
        step = route.current_step()
        if step is None:
            self.state.finish_route(True)
            return None
        if step.target_index is not None and step.target_index != char.index:
            self.task.switch_to(
                SwitchDecision(
                    step.target_index,
                    f"路线：{step.note or route.reason}",
                    ROUTE_PRIORITY,
                    skip_waits=True,
                ),
                current=char,
            )
            return None
        if not step.action_name:
            route.advance()
            if route.done:
                self.state.finish_route(True)
            return None

        action = char.action(step.action_name)
        if action is None or not self.action_allowed(char, action):
            route.advance()
            if route.done:
                self.state.finish_route(not step.optional)
            elif not step.optional:
                self.state.finish_route(False)
                self.task.log_info(f"路线中断：{step.action_name} 不可用")
            return None

        result = char.execute(action)
        self.last_action = action
        if result.success:
            route.advance()
            if route.done:
                self.state.finish_route(True)
        elif not step.optional:
            self.state.finish_route(False)
            self.task.log_info(f"路线中断：{step.action_name} 执行失败（{result.reason}）")
        return result

    # ── 切人 ──────────────────────────────────────────────

    def decide_switch(self, current) -> SwitchDecision:
        route = self.state.locked_route
        if route is not None:
            step = route.current_step()
            if step is not None and step.target_index is not None:
                return SwitchDecision(
                    step.target_index,
                    f"路线：{step.note or route.reason}",
                    ROUTE_PRIORITY,
                    skip_waits=True,
                )

        strict = self._strict_claim_decision(current)
        if strict is not None:
            return strict

        # 出招表没走完：不切人
        if current is not None and current.entry_active:
            return SwitchDecision(current.index, "连招中", 0)

        request = self._switch_request_decision(current)
        if request is not None:
            return request

        return self._score_decision(current)

    def _strict_claim_decision(self, current) -> SwitchDecision | None:
        for char in self.state.chars:
            if char is None or char.is_dead or char.index == current.index:
                continue
            for claim in char.claims():
                if claim.level is not FieldClaimLevel.STRICT:
                    continue
                entry = ExpectedEntry(action_name=claim.expected_entry) if claim.expected_entry else None
                return SwitchDecision(
                    char.index,
                    f"STRICT 诉求：{claim.reason}",
                    STRICT_CLAIM_PRIORITY,
                    expected_entry=entry,
                    skip_waits=True,
                )
        return None

    def _switch_request_decision(self, current) -> SwitchDecision | None:
        for request in self.state.active_requests():
            if not isinstance(request, SwitchRequest) or request.target_index == current.index:
                continue
            if not 0 <= request.target_index < len(self.state.chars):
                continue
            target = self.state.chars[request.target_index]
            if target is None or target.is_dead:
                continue
            return SwitchDecision(
                target.index,
                f"切人请求：{request.reason}",
                SWITCH_REQUEST_PRIORITY,
            )
        return None

    def _score_decision(self, current) -> SwitchDecision:
        """普通评分。

        - 当前角色不参与比较；兜底是"保持当前"，优先级 -999999；
        - 只有存在可评分动作（技能/大招/诉求）的角色才是候选，
          普攻不算（`ActionSpec.scored=False`）；
        - 同分时选 `last_perform` 更早的角色，避免两个同型角色互抢。
        """
        now = self.task.active_time()
        keep_index = current.index if current is not None else None
        best = SwitchDecision(keep_index, "保持当前", NO_CANDIDATE)
        best_char = current
        for char in self.state.chars:
            if char is None or char.is_dead:
                continue
            if current is not None and char.index == current.index:
                continue
            score, reason = self._score_char(char, now)
            if score <= NO_CANDIDATE:
                continue
            if self._switch_on_cooldown(char, now):
                score -= SWITCH_COOLDOWN_PENALTY
                reason += "（切人冷却）"
            if score > best.priority or (
                score == best.priority and best_char is not None and char.last_perform < best_char.last_perform
            ):
                best = SwitchDecision(char.index, reason, score)
                best_char = char
        return best

    def _score_char(self, char, now: float) -> tuple[int, str]:
        """单个角色的切人评分。"""
        best_action = None
        action_score = 0
        for action in char.plan.sorted_actions():
            if not action.scored:
                continue
            if not self.action_allowed(char, action):
                continue
            score = action.tag_score()
            if score > action_score:
                action_score, best_action = score, action

        claim_score = 0
        for claim in char.claims():
            claim_score = max(claim_score, FIELD_CLAIM_SCORES.get(claim.level, 0))

        # 没有可评分动作、也没有诉求 → 不进候选
        if best_action is None and claim_score <= 0:
            return NO_CANDIDATE, "没有可评分动作"

        profile = char.describe_role()
        preference = self._field_preference_score(profile, is_current=False)
        reservation = self._reservation_bonus(char)
        total = action_score + claim_score + preference + reservation
        action_name = best_action.name if best_action is not None else "无"
        reason = f"动作={action_name}({action_score}) 诉求={claim_score} 偏好={preference}"
        if reservation:
            reason += f" 保留={reservation}"
        return total, reason

    def _field_preference_score(self, profile, is_current: bool) -> int:
        """站场偏好。"""
        table = FIELD_PREFERENCE_REQUEST_SCORES if self.state.has_active_request() else FIELD_PREFERENCE_SCORES
        score = table.get(profile.resolved_preference(), 0)
        if is_current and profile.resolved_preference() is FieldPreference.MAIN_DPS:
            score += 60
        return score

    def _reservation_bonus(self, char) -> int:
        for request in self.state.active_requests():
            if isinstance(request, ReservationRequest) and request.owner_index == char.index:
                return RESERVATION_BONUS
        return 0

    def _switch_on_cooldown(self, char, now: float) -> bool:
        cooldown = float(self.task.config_value("_切人冷却(秒)", profile.SWITCH_COOLDOWN))
        return char.last_switch > 0 and now - char.last_switch < cooldown

    # ── 开战首切 ──────────────────────────────────────────

    def decide_combat_start_char(self, current) -> SwitchDecision | None:
        best_index = None
        best_priority = 0
        for char in self.state.chars:
            if char is None or char.is_dead:
                continue
            priority = char.describe_role().combat_start_priority
            if priority > best_priority:
                best_index, best_priority = char.index, priority
        if best_index is None:
            return None
        if current is not None and current.index == best_index:
            return None
        return SwitchDecision(
            best_index,
            f"开战首切（优先级 {best_priority}）",
            COMBAT_START_PRIORITY,
            skip_waits=True,
        )

    # ── 公共判定 ──────────────────────────────────────────

    def action_allowed(self, char, action) -> bool:
        """动作是否允许执行：角色自身可用性（冷却 + 游戏侧就绪提示）。"""
        return char.available(action)
