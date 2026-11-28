"""自动战斗框架。

分层：

- ``profile``        游戏事实（按键/区域/颜色/节奏），换游戏只改这里；
- ``recognition``   识别层（血条、锁定星形、技能就绪、当前角色、模板库）；
- ``detection``      多信号入战判定 + 血量 + 结算；
- ``enums/types``    动作、计划、诉求、切人决策等数据模型；
- ``requests/state/context``  角色协作请求、planner 状态与角色侧 API；
- ``planner``        调度器：这一拍做什么、该切给谁；
- ``character``      角色基类（动作表 + 诉求 + 入场保护）；
- ``objectives``     战斗目标：全自动 / 捉宠（只差终止条件）；
- ``dungeon_gate``   副本门禁：已解锁游戏自带自动战斗时让权；
- ``base_combat_task``  把以上串成一拍。

上层任务见 ``src/tasks/trigger/auto_combat_task.py``。
"""

from src.combat.base_combat_task import BaseCombatTask
from src.combat.character import BaseCombatChar
from src.combat.context import CombatContext
from src.combat.detection import CombatChecker, CombatObservation
from src.combat.dungeon_gate import DungeonGate
from src.combat.enums import (
    ActionSlot,
    ActionTag,
    CombatMode,
    FieldClaimLevel,
    FieldPreference,
    Role,
    StopReason,
)
from src.combat.objectives import CaptureObjective, CombatObjective, FullAutoObjective
from src.combat.planner import CombatPlanner
from src.combat.types import (
    ActionResult,
    ActionSpec,
    CombatPlan,
    FieldClaim,
    RoleProfile,
    RouteStep,
    SwitchDecision,
    SwitchInGuard,
)

__all__ = [
    "ActionResult",
    "ActionSlot",
    "ActionSpec",
    "ActionTag",
    "BaseCombatChar",
    "BaseCombatTask",
    "CaptureObjective",
    "CombatChecker",
    "CombatContext",
    "CombatMode",
    "CombatObjective",
    "CombatObservation",
    "CombatPlan",
    "CombatPlanner",
    "DungeonGate",
    "FieldClaim",
    "FieldClaimLevel",
    "FieldPreference",
    "FullAutoObjective",
    "Role",
    "RoleProfile",
    "RouteStep",
    "StopReason",
    "SwitchDecision",
    "SwitchInGuard",
]
