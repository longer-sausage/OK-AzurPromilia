"""通用角色：技能 → 大招 → 普攻，不声明额外诉求。"""

from __future__ import annotations

from src.combat.character import BaseCombatChar


class GenericChar(BaseCombatChar):
    """所有角色都能用的默认出招表（继承基类实现）。"""

    char_id = "generic"
    cn_name = "通用角色"
