"""示例角色：演示「入场诉求 + 协作路线」的写法（不是真实角色数据）。

真实角色脚本结构与此一致，只是动作表、诉求与路线按角色机制填写。
"""

from __future__ import annotations

from src.combat.character import BaseCombatChar
from src.combat.enums import FieldClaimLevel, FieldPreference, Role
from src.combat.types import CombatPlan, FieldClaim, RoleProfile, RouteStep


class SampleSupportChar(BaseCombatChar):
    """辅助位：技能就绪时申请上场，并给出一条两步协作路线。"""

    char_id = "sample_support"
    cn_name = "示例辅助"

    def describe_role(self) -> RoleProfile:
        return RoleProfile(
            role=Role.SUPPORT,
            field_preference=FieldPreference.SETUP_ONLY,
            max_field_time=1.0,
            combat_start_priority=0,
        )

    def combat_plan(self) -> CombatPlan:
        plan = super().combat_plan()
        plan.route = [
            RouteStep(target_index=None, action_name="技能", note="原地开技能"),
            RouteStep(target_index=0, action_name=None, note="切回主 C", optional=True),
        ]
        return plan

    def claims(self) -> list[FieldClaim]:
        """技能可用时申请入场（HIGH 强度）。"""
        skill = self.action("技能")
        if skill is not None and self.available(skill):
            return [FieldClaim(FieldClaimLevel.HIGH, "技能就绪，需要上场铺垫")]
        return []
