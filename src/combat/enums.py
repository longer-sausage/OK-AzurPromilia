"""战斗枚举与评分权重。"""

from enum import StrEnum


class CombatMode(StrEnum):
    """战斗模式。两者共用全部战斗逻辑，只有终止条件不同。"""

    FULL_AUTO = "full_auto"
    CAPTURE = "capture"


class Role(StrEnum):
    """角色队伍定位。"""

    MAIN_DPS = "main_dps"
    SUB_DPS = "sub_dps"
    SUPPORT = "support"


class ActionSlot(StrEnum):
    """动作槽位。"""

    ATTACK = "attack"
    SKILL = "skill"
    SKILL2 = "skill2"
    ULTIMATE = "ultimate"
    SWAP = "swap"


class ActionTag(StrEnum):
    """动作语义标签；planner 用它做通用评分，不用来描述角色专属机制。"""

    DEFAULT_ACTION = "default_action"
    DAMAGE = "damage"
    SKILL_ACTION = "skill_action"
    ULTIMATE_ACTION = "ultimate_action"
    SUPPORT = "support"
    HIGH_PRIORITY = "high_priority"


class FieldPreference(StrEnum):
    """没有协作战请求时，谁更该站在场上。"""

    MAIN_DPS = "main_dps"
    SUB_DPS = "sub_dps"
    SUPPORT = "support"
    SETUP_ONLY = "setup_only"


class FieldClaimLevel(StrEnum):
    """角色请求入场的强度。STRICT 是硬切人诉求，优先于普通评分。"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"
    STRICT = "strict"


class StopReason(StrEnum):
    """一场战斗为什么结束。"""

    OUT_OF_COMBAT = "out_of_combat"
    BATTLE_ENDED = "battle_ended"
    HP_THRESHOLD = "hp_threshold"
    TIMEOUT = "timeout"
    YIELDED_TO_GAME = "yielded_to_game"


#: 动作标签权重：大招 200 / 强需求 160 / 技能 75 / 辅助 45 / 伤害 35 / 默认 10
ACTION_TAG_SCORES = {
    ActionTag.DEFAULT_ACTION: 10,
    ActionTag.DAMAGE: 35,
    ActionTag.SKILL_ACTION: 75,
    ActionTag.HIGH_PRIORITY: 160,
    ActionTag.ULTIMATE_ACTION: 200,
    ActionTag.SUPPORT: 45,
}

#: 入场诉求权重
FIELD_CLAIM_SCORES = {
    FieldClaimLevel.LOW: 120,
    FieldClaimLevel.NORMAL: 240,
    FieldClaimLevel.HIGH: 420,
    FieldClaimLevel.CRITICAL: 700,
}

#: 站场偏好权重（无协作请求时）
FIELD_PREFERENCE_SCORES = {
    FieldPreference.MAIN_DPS: 120,
    FieldPreference.SUB_DPS: 40,
    FieldPreference.SUPPORT: -40,
    FieldPreference.SETUP_ONLY: -80,
}

#: 有协作请求时的站场偏好
FIELD_PREFERENCE_REQUEST_SCORES = {
    FieldPreference.MAIN_DPS: -80,
    FieldPreference.SUB_DPS: -20,
    FieldPreference.SUPPORT: 0,
    FieldPreference.SETUP_ONLY: -60,
}

#: 刚切过的角色再次被切的惩罚
SWITCH_COOLDOWN_PENALTY = 1000

#: STRICT 诉求的固定优先级，仅次于锁定路线
STRICT_CLAIM_PRIORITY = 999_999
ROUTE_PRIORITY = 1_000_000
