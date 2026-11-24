"""养成数据模型：把 promilia.wiki 的养成规划数据映射成本项目的数据结构。

设计原则：**游戏规则放在数据里，代码只做聚合**。角色/奇波/灵子/装备的每一段
等级需要多少经验、多少尤尼、哪些材料，全部由 ``assets/data/growth.json`` 描述，
这样换了版本、换了数值只需要换数据文件，不用改代码。

数据文件结构::

    {
      "version": 1,
      "note": "数据来源与版本说明",
      "materials": {"普通的灵粹": {"category": "exp"}},
      "exp_items": {"小型灵粹": 100, "普通的灵粹": 500},
      "dungeons": [
        {"name": "灵粹副本", "stamina": 20, "drops": {"普通的灵粹": 8}}
      ],
      "aliases": {"星脉结晶": {"大星脉结晶": 5}},
      "entries": [
        {
          "key": "char_amber", "name": "琥珀", "kind": "character", "rarity": 5,
          "max_level": 80,
          "levels": [
            {"from": 1, "to": 20, "exp": 5000, "credits": 3000, "materials": {"普通的灵粹": 5}}
          ],
          "potentials": [
            {"name": "生命", "levels": [{"level": 1, "exp": 100}, ...]}
          ],
          "star_stages": [
            {"star": 1, "copies": 1, "materials": {"星脉结晶": 10}}
          ]
        }
      ]
    }

**内置数据是示例，不是完整数据**：真实数值请从 promilia.wiki 的养成规划器导出后
用 ``Dataset.from_file`` 导入（或替换 ``assets/data/growth.json``）。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GROWTH_KINDS = ("character", "pet", "spirit", "equipment")
DEFAULT_DATA_PATH = Path("assets") / "data" / "growth.json"
BACKUP_FOLDER = Path("assets") / "data" / "backups"
MAX_TARGETS = 12


class GrowthDataError(ValueError):
    """养成数据文件不合法。"""


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _materials(value: Any, path: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GrowthDataError(f"{path} 必须是对象")
    result = {}
    for name, amount in value.items():
        number = _as_float(amount)
        if number > 0:
            result[str(name)] = number
    return result


@dataclass(frozen=True)
class LevelSpan:
    """一段等级区间（通常对应一次突破）。"""

    start: int
    end: int
    exp: float = 0.0
    credits: float = 0.0
    materials: Mapping[str, float] = field(default_factory=dict)

    def overlap(self, start: int, end: int) -> float:
        """与 [start, end] 的重叠比例（0..1），用于按比例切分经验。"""
        low = max(self.start, start)
        high = min(self.end, end)
        if high <= low:
            return 0.0
        span = self.end - self.start
        if span <= 0:
            return 1.0 if start <= self.start <= end else 0.0
        return (high - low) / span


@dataclass(frozen=True)
class PotentialLevel:
    level: int
    exp: float = 0.0


@dataclass(frozen=True)
class Potential:
    name: str
    levels: tuple[PotentialLevel, ...] = ()

    def exp_to(self, level: int) -> float:
        return sum(item.exp for item in self.levels if item.level <= level)


@dataclass(frozen=True)
class StarStage:
    star: int
    copies: int = 0
    materials: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Entry:
    key: str
    name: str
    kind: str = "character"
    rarity: int = 5
    max_level: int = 1
    levels: tuple[LevelSpan, ...] = ()
    potentials: tuple[Potential, ...] = ()
    star_stages: tuple[StarStage, ...] = ()

    def spans_between(self, start: int, end: int) -> list[tuple[LevelSpan, float]]:
        """返回 [start, end] 覆盖的等级段及重叠比例。"""
        result = []
        for span in self.levels:
            ratio = span.overlap(start, end)
            if ratio > 0:
                result.append((span, ratio))
        return result

    def potential_exp(self, level: int) -> float:
        return sum(potential.exp_to(level) for potential in self.potentials)

    def copies_for_star(self, star: int) -> int:
        return sum(stage.copies for stage in self.star_stages if stage.star <= star)

    def star_materials(self, star: int) -> dict[str, float]:
        result: dict[str, float] = {}
        for stage in self.star_stages:
            if stage.star > star:
                continue
            for name, amount in stage.materials.items():
                result[name] = result.get(name, 0.0) + amount
        return result

    def to_dict(self) -> dict:
        """序列化回 ``from_dict`` 能读的形状（等级段 / 潜能 / 升星都保留）。"""
        data: dict = {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "max_level": self.max_level,
        }
        if self.rarity:
            data["rarity"] = self.rarity
        data["levels"] = [
            {
                "from": span.start,
                "to": span.end,
                "exp": span.exp,
                "credits": span.credits,
                "materials": dict(span.materials),
            }
            for span in self.levels
        ]
        if self.potentials:
            data["potentials"] = [
                {
                    "name": potential.name,
                    "levels": [
                        {"level": item.level, "exp": item.exp} for item in potential.levels
                    ],
                }
                for potential in self.potentials
            ]
        if self.star_stages:
            data["star_stages"] = [
                {
                    "star": stage.star,
                    "copies": stage.copies,
                    "materials": dict(stage.materials),
                }
                for stage in self.star_stages
            ]
        return data


@dataclass(frozen=True)
class Dungeon:
    name: str
    stamina: float = 0.0
    drops: Mapping[str, float] = field(default_factory=dict)

    def yield_per_stamina(self, material: str) -> float:
        drop = self.drops.get(material, 0.0)
        if drop <= 0 or self.stamina <= 0:
            return 0.0
        return drop / self.stamina


@dataclass
class Dataset:
    entries: dict[str, Entry] = field(default_factory=dict)
    materials: dict[str, str] = field(default_factory=dict)  # 材料 -> 分类
    exp_items: dict[str, float] = field(default_factory=dict)
    dungeons: tuple[Dungeon, ...] = ()
    aliases: dict[str, dict[str, float]] = field(default_factory=dict)
    version: int = 1
    note: str = ""

    # ── 载入 ─────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Dataset:
        if not isinstance(data, Mapping):
            raise GrowthDataError("养成数据顶层必须是 JSON 对象")
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise GrowthDataError("养成数据缺少 entries")

        entries: dict[str, Entry] = {}
        for index, item in enumerate(raw_entries):
            if not isinstance(item, Mapping):
                raise GrowthDataError(f"entries[{index}] 必须是对象")
            entry = _parse_entry(item, f"entries[{index}]")
            if entry.key in entries:
                raise GrowthDataError(f"重复的目标 key: {entry.key}")
            entries[entry.key] = entry

        materials = {}
        for name, info in (data.get("materials") or {}).items():
            category = ""
            if isinstance(info, Mapping):
                category = str(info.get("category", "") or "")
            materials[str(name)] = category

        exp_items = {}
        for name, value in (data.get("exp_items") or {}).items():
            amount = _as_float(value)
            if amount > 0:
                exp_items[str(name)] = amount

        dungeons = []
        for index, item in enumerate(data.get("dungeons") or []):
            if not isinstance(item, Mapping):
                raise GrowthDataError(f"dungeons[{index}] 必须是对象")
            dungeons.append(
                Dungeon(
                    name=str(item.get("name", f"副本{index + 1}")),
                    stamina=_as_float(item.get("stamina")),
                    drops=_materials(item.get("drops"), f"dungeons[{index}].drops"),
                )
            )

        aliases = {}
        for name, mapping in (data.get("aliases") or {}).items():
            if isinstance(mapping, Mapping):
                aliases[str(name)] = {str(k): _as_float(v) for k, v in mapping.items() if _as_float(v) > 0}

        return cls(
            entries=entries,
            materials=materials,
            exp_items=exp_items,
            dungeons=tuple(dungeons),
            aliases=aliases,
            version=_as_int(data.get("version"), 1),
            note=str(data.get("note", "") or ""),
        )

    @classmethod
    def from_json(cls, text: str) -> Dataset:
        try:
            return cls.from_dict(json.loads(text))
        except ValueError as exc:
            if isinstance(exc, GrowthDataError):
                raise
            raise GrowthDataError(f"养成数据不是合法 JSON: {exc}") from exc

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_DATA_PATH) -> Dataset:
        file_path = Path(path)
        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GrowthDataError(f"读不到养成数据: {exc}") from exc
        return cls.from_json(text)

    @classmethod
    def load_default(cls) -> Dataset:
        return cls.from_file(DEFAULT_DATA_PATH)

    # ── 导出 ─────────────────────────────────────────────
    def to_dict(self) -> dict:
        """序列化回 ``from_dict`` 能读的形状（用于导入器与「另存为」）。

        之前只有 ``from_dict`` 没有反向：想写回 JSON 只能靠外部自己拼，
        表格导入（``table_import``）因此拿不到可写盘的数据。
        """
        return {
            "version": self.version,
            "note": self.note,
            "materials": {name: {"category": category} for name, category in self.materials.items()},
            "exp_items": dict(self.exp_items),
            "dungeons": [
                {
                    "name": dungeon.name,
                    "stamina": dungeon.stamina,
                    "drops": dict(dungeon.drops),
                }
                for dungeon in self.dungeons
            ],
            "aliases": {name: dict(mapping) for name, mapping in self.aliases.items()},
            "entries": [entry.to_dict() for entry in self.entries.values()],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    # ── 查询 ─────────────────────────────────────────────
    def entry(self, key: str) -> Entry:
        try:
            return self.entries[key]
        except KeyError as exc:
            raise GrowthDataError(f"没有这个养成目标: {key}") from exc

    def entries_of(self, kind: str) -> list[Entry]:
        return [entry for entry in self.entries.values() if entry.kind == kind]

    def best_source(self, material: str) -> tuple[float, Dungeon] | None:
        """每单位材料所需体力最少的副本。"""
        best: tuple[float, Dungeon] | None = None
        for dungeon in self.dungeons:
            rate = dungeon.yield_per_stamina(material)
            if rate <= 0:
                continue
            stamina_per_unit = 1.0 / rate
            if best is None or stamina_per_unit < best[0]:
                best = (stamina_per_unit, dungeon)
        return best

    def kinds_summary(self) -> dict[str, int]:
        summary = dict.fromkeys(GROWTH_KINDS, 0)
        for entry in self.entries.values():
            summary[entry.kind] = summary.get(entry.kind, 0) + 1
        return summary


def _parse_entry(data: Mapping[str, Any], path: str) -> Entry:
    key = str(data.get("key", "") or "").strip()
    if not key:
        raise GrowthDataError(f"{path} 缺少 key")
    kind = str(data.get("kind", "character") or "character")
    if kind not in GROWTH_KINDS:
        raise GrowthDataError(f"{path}.kind 只能是 {', '.join(GROWTH_KINDS)}")

    levels = []
    for index, span in enumerate(data.get("levels") or []):
        if not isinstance(span, Mapping):
            raise GrowthDataError(f"{path}.levels[{index}] 必须是对象")
        start = _as_int(span.get("from"), 1)
        end = _as_int(span.get("to"), start)
        if end < start:
            raise GrowthDataError(f"{path}.levels[{index}] 的 to 小于 from")
        levels.append(
            LevelSpan(
                start=start,
                end=end,
                exp=_as_float(span.get("exp")),
                credits=_as_float(span.get("credits")),
                materials=_materials(span.get("materials"), f"{path}.levels[{index}].materials"),
            )
        )

    potentials = []
    for index, potential in enumerate(data.get("potentials") or []):
        if not isinstance(potential, Mapping):
            raise GrowthDataError(f"{path}.potentials[{index}] 必须是对象")
        levels_raw = potential.get("levels") or []
        potentials.append(
            Potential(
                name=str(potential.get("name", f"潜能{index + 1}")),
                levels=tuple(
                    PotentialLevel(
                        level=_as_int(item.get("level"), item_index + 1),
                        exp=_as_float(item.get("exp")),
                    )
                    for item_index, item in enumerate(levels_raw)
                    if isinstance(item, Mapping)
                ),
            )
        )

    star_stages = []
    for index, stage in enumerate(data.get("star_stages") or []):
        if not isinstance(stage, Mapping):
            raise GrowthDataError(f"{path}.star_stages[{index}] 必须是对象")
        star_stages.append(
            StarStage(
                star=_as_int(stage.get("star")),
                copies=_as_int(stage.get("copies")),
                materials=_materials(stage.get("materials"), f"{path}.star_stages[{index}].materials"),
            )
        )

    max_level = _as_int(data.get("max_level")) or max((span.end for span in levels), default=1)
    return Entry(
        key=key,
        name=str(data.get("name", key) or key),
        kind=kind,
        rarity=_as_int(data.get("rarity"), 5),
        max_level=max_level,
        levels=tuple(sorted(levels, key=lambda span: span.start)),
        potentials=tuple(potentials),
        star_stages=tuple(sorted(star_stages, key=lambda stage: stage.star)),
    )


# ── 导入（把 wiki 导出的 JSON 换成内置数据） ──────────────
@dataclass
class ImportResult:
    ok: bool = False
    path: Path | None = None
    backup: Path | None = None
    message: str = ""
    summary: dict[str, int] = field(default_factory=dict)

    def describe(self) -> str:
        if not self.ok:
            return f"导入失败：{self.message}"
        detail = "、".join(f"{name} {count}" for name, count in self.summary.items() if count) or "没有条目"
        backup = f"；原文件备份在 {self.backup}" if self.backup else ""
        return f"已导入：{detail}{backup}"


def import_dataset(
    source: str | Path,
    target: str | Path | None = None,
    backup_dir: str | Path | None = None,
) -> ImportResult:
    """校验并导入一份养成数据（默认覆盖 ``assets/data/growth.json``）。

    先 ``Dataset.from_file`` 校验，**校验不过一个字节都不写**；通过后把原文件备份到
    ``assets/data/backups/growth_<时间戳>.json``，再原子替换。
    """
    source_path = Path(source)
    target_path = Path(target) if target is not None else DEFAULT_DATA_PATH
    try:
        dataset = Dataset.from_file(source_path)
    except GrowthDataError as exc:
        return ImportResult(ok=False, message=str(exc))

    backup: Path | None = None
    try:
        if target_path.is_file():
            # 备份跟目标文件放一起（默认目标就是 assets/data/growth.json → assets/data/backups），
            # 这样把数据导到别的路径时不会把备份写进仓库目录。
            folder = Path(backup_dir) if backup_dir is not None else target_path.parent / "backups"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            backup = folder / f"growth_{stamp}.json"
            index = 0
            while backup.exists():
                index += 1
                backup = folder / f"growth_{stamp}_{index}.json"
            shutil.copy2(target_path, backup)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = target_path.with_suffix(".json.tmp")
        shutil.copy2(source_path, tmp)
        os.replace(tmp, target_path)
    except OSError as exc:
        return ImportResult(ok=False, message=f"写入失败: {exc}", backup=backup)

    return ImportResult(ok=True, path=target_path, backup=backup, summary=dataset.kinds_summary())
