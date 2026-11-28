from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from threading import RLock
from typing import TYPE_CHECKING

import numpy as np

from src.char import BASE_CHAR_NAME, normalize_binding_name
from src.char.base_char import BaseChar
from src.combat.char_factory import CharFactory
from src.data.char_templates import CharTemplate, CharTemplateStore
from src.image.team_portraits import SLOT_COUNT, portrait_score, read_team_hud, slot_portrait, valid_frame

if TYPE_CHECKING:
    from src.combat.combat import Combat


@dataclass(frozen=True, eq=False)
class TeamSlot:
    portrait: np.ndarray | None = None
    template_id: str | None = None
    char_name: str = BASE_CHAR_NAME
    similarity: float = 0.0

    @property
    def confidence(self) -> float:
        return self.similarity if self.char_name != BASE_CHAR_NAME else 0.0


class TeamManager:
    """读取当前设备的一帧，独立匹配三个槽位。角色实例由各自的 Combat 持有。"""

    MATCH_THRESHOLD = 0.82
    REUSE_THRESHOLD = 0.98

    def __init__(self, store: CharTemplateStore | None = None, factory: CharFactory | None = None):
        self.store = store or CharTemplateStore()
        self.factory = factory or CharFactory()
        self.current_team = (TeamSlot(),) * SLOT_COUNT
        self._lock = RLock()

    @staticmethod
    def team_state(frame: np.ndarray | None) -> tuple[bool, int, int]:
        contrasts = read_team_hud(frame)
        count = next((i for i, contrast in enumerate(contrasts) if not contrast), SLOT_COUNT)
        if not count or any(contrasts[count:]):
            return False, -1, 0
        current = min(range(count), key=contrasts.__getitem__)
        return True, current, count

    def _match(self, portrait: np.ndarray, templates: tuple[CharTemplate, ...], capture: bool,
               extra_names: Collection[str]) -> TeamSlot:
        best = TeamSlot(portrait)
        for template in templates:
            if template.image is None:
                continue
            name = template.char_name
            if name not in extra_names:
                name = self.factory.resolve_name(name)
            threshold = self.REUSE_THRESHOLD if name == BASE_CHAR_NAME else self.MATCH_THRESHOLD
            score = portrait_score(portrait, template.image)
            if score >= threshold and score > best.similarity:
                # 截图中的新外观有独立 UUID，用户改绑时只影响该头像。
                key = None if capture and score < self.REUSE_THRESHOLD else template.uuid
                best = TeamSlot(portrait, key, name, score)
        return best

    def _read_slots(self, frame: np.ndarray | None, *, capture: bool = False,
                    extra_names: Collection[str] = ()) -> list[TeamSlot]:
        if not valid_frame(frame):
            return [TeamSlot()] * SLOT_COUNT
        templates = self.store.templates()
        slots = []
        for i, contrast in enumerate(read_team_hud(frame)):
            slot = self._match(slot_portrait(frame, i), templates, capture, extra_names)
            # 徽标只用于判断能否收录新头像。已有模板也可在按键提示隐藏时识别。
            slots.append(slot if contrast or slot.similarity > 0 else TeamSlot())
        return slots

    def observe(self, frame: np.ndarray | None, *, capture: bool = False) -> tuple[TeamSlot, ...]:
        """仅在用户截图时收录新头像，已有匹配可预填角色名。"""
        with self._lock:
            slots = self._read_slots(frame, capture=capture)
            if capture:
                pending = [i for i, slot in enumerate(slots) if slot.portrait is not None and slot.template_id is None]
                keys = self.store.add([(slots[i].portrait, slots[i].char_name) for i in pending])
                for i, key in zip(pending, keys, strict=True):
                    slots[i] = replace(slots[i], template_id=key, similarity=slots[i].similarity or 1.0)
            self.current_team = tuple(slots)
            return self.current_team

    def bind(self, key: str, name: str | None) -> None:
        name = normalize_binding_name(name)
        if name != BASE_CHAR_NAME and self.factory.get_spec(name) is None:
            raise ValueError(f"Unknown Char: {name}")
        with self._lock:
            self.store.bind(key, name)
            self.current_team = tuple(
                replace(slot, char_name=name)
                if slot.template_id == key else slot for slot in self.current_team
            )

    def delete(self, key: str) -> None:
        with self._lock:
            self.store.delete(key)
            self.current_team = tuple(TeamSlot(slot.portrait) if slot.template_id == key else slot
                                      for slot in self.current_team)

    def instantiate(self, combat: Combat, frame: np.ndarray | None, count: int,
                    previous: list[BaseChar]) -> list[BaseChar]:
        local_names = {name for name in combat.char_overrides if self.factory.get_spec(name) is None}
        # 独立实现也能匹配同名的已有绑定，只在声明了它的战斗中使用。
        observed = self._read_slots(frame, extra_names=local_names) if local_names else self.observe(frame)
        slots = observed[:count]
        combat.validate_team(slots)

        return [
            self.factory.create(combat, i, slot.char_name, slot.confidence, previous[i] if i < len(previous) else None,
                                behavior=combat.char_overrides.get(slot.char_name))
            for i, slot in enumerate(slots)
        ]


team_manager = TeamManager()

