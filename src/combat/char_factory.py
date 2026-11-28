from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from importlib import import_module
from inspect import getmembers, isabstract, isclass
from pkgutil import walk_packages
from typing import TYPE_CHECKING, Any

from src import char
from src.char import BASE_CHAR_NAME, normalize_binding_name
from src.char.base_char import BaseChar

if TYPE_CHECKING:
    from src.combat.combat import Combat


# 名称翻译也用于显示旧绑定。可选角色由 src.char 中的专属类决定。
CHAR_DISPLAY_NAMES = {
    BASE_CHAR_NAME: "默认",
    "Starborn": "星临者",
    "Abby": "阿比",
    "Anais": "艾妮丝",
    "Cathbelle": "姬瑟贝露",
    "Faridah": "法兰塔",
    "HanYouyou": "寒悠悠",
    "Katalua": "卡塔露",
    "LanXiangzi": "兰香子",
    "Lily": "莉莉",
    "Luruka": "璐璐卡",
    "Maple": "枫糖",
    "Metsa": "米砂",
    "MinamotoChiyo": "源千代",
    "Mitty": "米蒂",
    "Morwyn": "末音",
    "Nono": "诺诺",
    "Peipei": "佩佩",
    "Pengpeng": "芃芃",
    "Ruby": "红宝石",
    "Shalle": "夏儿",
    "Symphoria": "西芙莉雅",
    "Terara": "忒拉拉",
    "TushanXiaoyu": "涂山小玉",
}


def _char_classes() -> Iterator[type[BaseChar]]:
    """查找角色模块自己定义的公开、可实例化的 BaseChar 子类。"""
    prefix = char.__name__ + "."
    for module_info in walk_packages(char.__path__, prefix):
        parts = module_info.name.removeprefix(prefix).split(".")
        if module_info.name == BaseChar.__module__ or any(part.startswith("_") for part in parts):
            continue
        module = import_module(module_info.name)
        for name, cls in getmembers(module, isclass):
            if name.startswith("_") or cls.__module__ != module.__name__ or name != cls.__name__:
                continue
            if issubclass(cls, BaseChar) and not isabstract(cls):
                yield cls


@dataclass(frozen=True)
class CharSpec:
    canonical_name: str
    display_msgid: str
    cls: type[BaseChar]
    kwargs: dict[str, Any]


class CharFactory:
    """按专属角色类的稳定类名提供选项并实例化，未知槽位使用基类。"""

    def __init__(self, default_char_cls: type[BaseChar] = BaseChar) -> None:
        self.default_char_cls = default_char_cls
        self._entries: dict[str, CharSpec] = {}
        for cls in _char_classes():
            name = cls.__name__
            if normalize_binding_name(name) == BASE_CHAR_NAME or name in self._entries:
                raise ValueError(f"Reserved or duplicate Char name: {name!r} in {cls.__module__}")
            self._entries[name] = CharSpec(name, CHAR_DISPLAY_NAMES.get(name, name), cls, {})

    def register(self, name: str, cls: type[BaseChar], *, display_msgid: str | None = None,
                 **kwargs: Any) -> CharSpec:
        """为已发现的专属类配置显示名或构造参数。"""
        previous = self.get_spec(name) if isinstance(name, str) else None
        if previous is None or previous.cls is not cls:
            raise ValueError(f"No matching Char class in src.char for {name!r}")
        spec = CharSpec(name, display_msgid or previous.display_msgid, cls, kwargs)
        self._entries[name] = spec
        return spec

    @property
    def characters(self) -> tuple[CharSpec, ...]:
        return tuple(self._entries.values())

    def get_spec(self, name: str | None) -> CharSpec | None:
        return self._entries.get(name) if name else None

    def resolve_name(self, name: str | None) -> str:
        spec = self.get_spec(name)
        return spec.canonical_name if spec else BASE_CHAR_NAME

    def display_msgid(self, name: str | None) -> str:
        if spec := self.get_spec(name):
            return spec.display_msgid
        name = normalize_binding_name(name)
        return CHAR_DISPLAY_NAMES.get(name, name)

    def create(self, combat: Combat, index: int, name: str | None, confidence: float,
               previous: BaseChar | None = None) -> BaseChar:
        spec = self.get_spec(name)
        cls = spec.cls if spec else self.default_char_cls
        key = spec.canonical_name if spec else BASE_CHAR_NAME
        score = confidence if spec else 0.0
        if type(previous) is cls and previous.combat is combat and previous.index == index and previous.char_name == key:
            previous.confidence = score
            return previous
        return cls(combat, index, char_name=key, confidence=score, **(spec.kwargs if spec else {}))
