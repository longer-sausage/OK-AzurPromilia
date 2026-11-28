"""内置角色脚本注册表。"""

from __future__ import annotations

from src.combat.character import BaseCombatChar
from src.combat.characters.generic import GenericChar
from src.combat.characters.sample_support import SampleSupportChar

CHARACTER_CLASSES: dict[str, type[BaseCombatChar]] = {
    "generic": GenericChar,
    "sample_support": SampleSupportChar,
}


def build_character(task, index: int, char_id: str = "") -> BaseCombatChar:
    """按脚本 id 造角色；未知 id 回落通用角色。"""
    key = char_id or "generic"
    char_class = CHARACTER_CLASSES.get(key)
    if char_class is None:
        task.log_warning(f"未知角色脚本 {key}，回落到通用角色")
        char_class = GenericChar
        key = "generic"
    return char_class(task, index, impl_id=key)


def character_ids() -> list[str]:
    return sorted(CHARACTER_CLASSES)
