from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.combat.base_char import BaseChar

_RESOLUTION_SUFFIXES = ("_4k", "_2k")


def _normalize_template_name(name: str | Enum) -> str:
    return str(name.value) if isinstance(name, Enum) else str(name)


@dataclass(frozen=True)
class CharSpec:
    """单个角色的注册元数据。"""

    cls: type[BaseChar]
    canonical_name: str
    template_names: tuple[str, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)


class CharFactory:
    """角色模板注册表与按队伍槽位识别/实例化工厂。"""

    def __init__(
        self,
        *,
        match_threshold: float = 0.6,
        recheck_confidence: float = 0.92,
        default_char_cls: type[BaseChar] = BaseChar,
    ) -> None:
        self._entries: dict[str, CharSpec] = {}
        self.match_threshold = match_threshold
        self.recheck_confidence = recheck_confidence
        self.default_char_cls = default_char_cls

    def register(
        self,
        template_names: str | Enum | Sequence[str | Enum],
        cls: type[BaseChar],
        **kwargs: Any,
    ) -> CharSpec:
        """注册角色类与其对应的一个或多个头像/识别模板名（首个为规范名）。"""
        raw_names = (template_names,) if isinstance(template_names, (str, Enum)) else tuple(template_names)
        names = tuple(_normalize_template_name(name) for name in raw_names)
        if not names or any(not name for name in names):
            raise ValueError("template_names must contain at least one non-empty template name")
        spec = CharSpec(cls=cls, canonical_name=names[0], template_names=names, kwargs=dict(kwargs))
        for name in names:
            self._entries[name] = spec
        return spec

    def clear(self) -> None:
        self._entries.clear()

    @property
    def template_names(self) -> tuple[str, ...]:
        return tuple(self._entries.keys())

    def get_spec(self, name: str | None) -> CharSpec | None:
        if not name:
            return None
        spec = self._entries.get(name)
        if spec is not None:
            return spec
        for suffix in _RESOLUTION_SUFFIXES:
            if name.endswith(suffix):
                spec = self._entries.get(name[: -len(suffix)])
                if spec is not None:
                    return spec
        return None

    def _find_spec(self, task: Any, box: Any, spec: CharSpec) -> Any:
        if len(spec.template_names) == 1:
            return task.find_one(spec.template_names[0], box=box, threshold=self.match_threshold)
        return task.find_best_match_in_box(box, list(spec.template_names), threshold=self.match_threshold)

    def _instantiate(self, task: Any, index: int, spec: CharSpec, confidence: float) -> BaseChar:
        return spec.cls(task, index, char_name=spec.canonical_name, confidence=confidence, **spec.kwargs)

    def get_char_by_pos(
        self,
        task: Any,
        box: Any,
        index: int,
        old_char: BaseChar | None = None,
    ) -> BaseChar:
        """识别指定槽位框内的角色；优先复检高置信度缓存角色，未命中时回退旧角色或默认基类。"""
        if old_char is not None and old_char.confidence > self.recheck_confidence:
            spec = self.get_spec(old_char.char_name)
            if spec is not None:
                match = self._find_spec(task, box, spec)
                if match is not None:
                    if not isinstance(old_char, spec.cls):
                        return self._instantiate(task, index, spec, match.confidence)
                    old_char.index = index
                    old_char.char_name = spec.canonical_name
                    old_char.confidence = float(match.confidence)
                    return old_char

        if self._entries:
            match = task.find_best_match_in_box(box, list(self._entries.keys()), threshold=self.match_threshold)
            if match is not None:
                spec = self.get_spec(getattr(match, "name", None))
                if spec is not None:
                    return self._instantiate(task, index, spec, match.confidence)

        if old_char is not None:
            old_char.index = index
            return old_char

        return self.default_char_cls(task, index, char_name="unknown", confidence=0.0)
