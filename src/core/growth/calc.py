"""养成计算：把若干目标换算成一张材料单、体力折算与会话晶体。

输入：数据集（:mod:`src.core.growth.model`）+ 目标列表 + 库存。
输出：:class:`GrowthPlan`，包含

- 每个目标需要多少经验 / 尤尼 / 材料、跨几个阶段、升星要几份本体；
- 合并后的材料总需求、库存缺口；
- 每种材料「≈ 多少体力 / 多少次副本」，无稳定来源的单独列出；
- 可兑换货币（如星脉结晶）按「大结晶优先」凑法；
- 用哪种经验道具各多少个（贪心用大的）。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from src.core.growth.model import MAX_TARGETS, Dataset, Entry

UNLIMITED_POTENTIAL_LEVEL = 0


class GrowthInputError(ValueError):
    """目标列表或库存不合法。"""


@dataclass(frozen=True)
class Target:
    """一个养成目标。"""

    entry_key: str
    from_level: int = 1
    to_level: int | None = None      # None 表示练到满级
    copies: int = 1
    potential_level: int = 0         # 0 表示不算潜能；奇波 6 条潜能统一按这个等级
    star: int = 0                    # 0 表示不算升星

    def normalized(self, entry: Entry) -> Target:
        start = max(1, int(self.from_level))
        end = entry.max_level if self.to_level is None else int(self.to_level)
        end = max(start, min(end, entry.max_level))
        try:
            copies = int(self.copies)
        except (TypeError, ValueError) as exc:
            raise GrowthInputError(f"份数必须是整数，收到 {self.copies!r}") from exc
        if copies < 1:
            # 0 份 / 负数份都是输入错误：静默当 1 份会让人误以为算的是 0 份
            raise GrowthInputError(f"份数至少为 1，收到 {self.copies!r}")
        return Target(
            entry_key=self.entry_key,
            from_level=start,
            to_level=end,
            copies=copies,
            potential_level=max(0, int(self.potential_level)),
            star=max(0, min(int(self.star), max((stage.star for stage in entry.star_stages), default=0))),
        )


@dataclass
class TargetPlan:
    entry: Entry
    target: Target
    exp: float = 0.0
    credits: float = 0.0
    potential_exp: float = 0.0
    materials: dict[str, float] = field(default_factory=dict)
    stages: int = 0
    copies: int = 1
    star_copies: int = 0

    @property
    def label(self) -> str:
        return f"{self.entry.name} Lv{self.target.from_level}→{self.target.to_level}"

    def to_row(self) -> dict:
        return {
            "类型": self.entry.kind,
            "目标": self.entry.name,
            "等级": f"{self.target.from_level}→{self.target.to_level}",
            "份数": self.copies,
            "阶段": self.stages,
            "经验": self.exp,
            "尤尼": self.credits,
            "潜能经验": self.potential_exp,
        }


@dataclass
class GrowthPlan:
    targets: list[TargetPlan] = field(default_factory=list)
    materials: dict[str, float] = field(default_factory=dict)
    inventory: dict[str, float] = field(default_factory=dict)
    gaps: dict[str, float] = field(default_factory=dict)
    stamina: dict[str, float] = field(default_factory=dict)
    runs: dict[str, int] = field(default_factory=dict)
    no_source: list[str] = field(default_factory=list)
    crystals: dict[str, int] = field(default_factory=dict)
    exp_items: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def total_exp(self) -> float:
        return sum(plan.exp for plan in self.targets)

    @property
    def total_credits(self) -> float:
        return sum(plan.credits for plan in self.targets)

    @property
    def total_potential_exp(self) -> float:
        return sum(plan.potential_exp for plan in self.targets)

    @property
    def total_stamina(self) -> float:
        return sum(self.stamina.values())

    @property
    def total_runs(self) -> int:
        return sum(self.runs.values())

    def gap_of(self, material: str) -> float:
        return self.gaps.get(material, 0.0)

    def summary_lines(self) -> list[str]:
        lines = [
            f"目标 {len(self.targets)} 个：需要经验 {self.total_exp:,.0f}、尤尼 {self.total_credits:,.0f}",
        ]
        if self.total_potential_exp:
            lines.append(f"潜能经验 {self.total_potential_exp:,.0f}")
        if self.materials:
            lines.append(f"材料 {len(self.materials)} 种，合计体力约 {self.total_stamina:,.0f}（{self.total_runs} 次副本）")
        for name in self.no_source:
            lines.append(f"⚠ {name} 没有稳定来源，未计入体力")
        return lines


# ── 计算 ─────────────────────────────────────────────────
def compute_plan(
    dataset: Dataset,
    targets: Iterable[Target],
    inventory: Mapping[str, float] | None = None,
    max_targets: int = MAX_TARGETS,
) -> GrowthPlan:
    """汇总多个目标的养成需求。"""
    target_list = list(targets)
    if not target_list:
        raise GrowthInputError("至少要选一个养成目标")
    if len(target_list) > max_targets:
        raise GrowthInputError(f"最多同时计算 {max_targets} 个目标，当前 {len(target_list)} 个")

    plan = GrowthPlan()
    materials: dict[str, float] = {}

    for raw_target in target_list:
        if isinstance(raw_target, Target):
            target_in = raw_target
        elif isinstance(raw_target, str):
            # 允许直接写条目 key，省掉包一层 Target
            target_in = Target(raw_target)
        else:
            raise GrowthInputError(
                f"养成目标必须是 Target 或条目 key（字符串），收到 {type(raw_target).__name__}"
            )
        entry = dataset.entry(target_in.entry_key)
        target = target_in.normalized(entry)
        if target.to_level <= target.from_level and not target.potential_level and not target.star:
            plan.notes.append(f"{entry.name} 没有需要养成的部分，已跳过")
            continue

        item = TargetPlan(entry=entry, target=target, copies=target.copies)
        span_count = 0
        for span, ratio in entry.spans_between(target.from_level, target.to_level):
            span_count += 1
            item.exp += span.exp * ratio * target.copies
            item.credits += span.credits * ratio * target.copies
            for name, amount in span.materials.items():
                _add(item.materials, name, amount * ratio * target.copies)

        if target.potential_level > 0 and entry.potentials:
            item.potential_exp = entry.potential_exp(target.potential_level) * target.copies
            item.exp += item.potential_exp

        if target.star > 0 and entry.star_stages:
            item.star_copies = entry.copies_for_star(target.star) * target.copies
            for name, amount in entry.star_materials(target.star).items():
                _add(item.materials, name, amount * target.copies)

        item.stages = span_count
        plan.targets.append(item)
        for name, amount in item.materials.items():
            _add(materials, name, amount)

    plan.materials = materials
    plan.inventory = {str(k): float(v) for k, v in (inventory or {}).items() if float(v or 0) > 0}
    plan.gaps = {
        name: max(0.0, amount - plan.inventory.get(name, 0.0))
        for name, amount in materials.items()
    }

    _compute_stamina(dataset, plan)
    _compute_crystals(dataset, plan)
    _compute_exp_items(dataset, plan)
    return plan


def _add(bucket: dict[str, float], key: str, amount: float) -> None:
    if amount:
        bucket[key] = bucket.get(key, 0.0) + amount


def _compute_stamina(dataset: Dataset, plan: GrowthPlan) -> None:
    for name, amount in plan.materials.items():
        source = dataset.best_source(name)
        if source is None:
            plan.no_source.append(name)
            continue
        stamina_per_unit, dungeon = source
        plan.stamina[name] = stamina_per_unit * amount
        drop = dungeon.drops.get(name, 0.0)
        plan.runs[name] = math.ceil(amount / drop) if drop > 0 else 0


def _compute_crystals(dataset: Dataset, plan: GrowthPlan) -> None:
    """可兑换货币按「大单位优先」凑：先换成大单位，余数用原单位。

    用的是**缺口**而不是总需求：库存里已有的原单位会先抵扣，避免算多。
    """
    for base, mapping in dataset.aliases.items():
        if base not in plan.materials:
            continue
        need = plan.gaps.get(base, plan.materials[base])
        remaining = need
        for big, factor in sorted(mapping.items(), key=lambda item: -item[1]):
            if factor <= 0:
                continue
            count = int(remaining // factor)
            if count:
                plan.crystals[big] = count
                remaining -= count * factor
        if remaining > 0:
            plan.crystals[base] = math.ceil(remaining)


def _compute_exp_items(dataset: Dataset, plan: GrowthPlan) -> None:
    remaining = plan.total_exp
    if remaining <= 0 or not dataset.exp_items:
        return
    for name, value in sorted(dataset.exp_items.items(), key=lambda item: -item[1]):
        if value <= 0:
            continue
        count = int(remaining // value)
        if count:
            plan.exp_items[name] = count
            remaining -= count * value
    if remaining > 0 and plan.exp_items:
        # 最小的道具兜底，保证总数覆盖需求
        smallest = min(dataset.exp_items.items(), key=lambda item: item[1])
        plan.exp_items[smallest[0]] = plan.exp_items.get(smallest[0], 0) + 1


# ── 导出用视图 ───────────────────────────────────────────
def material_rows(plan: GrowthPlan) -> list[dict]:
    """材料单行：需求 / 拥有 / 缺口 / 体力 / 次数。"""
    rows = []
    for name in sorted(plan.materials):
        need = plan.materials[name]
        have = plan.inventory.get(name, 0.0)
        rows.append(
            {
                "材料": name,
                "需求": need,
                "拥有": have,
                "缺口": max(0.0, need - have),
                "体力": round(plan.stamina.get(name, 0.0), 1),
                "次数": plan.runs.get(name, 0),
                "来源": "无稳定来源" if name in plan.no_source else "",
            }
        )
    return rows


def target_rows(plan: GrowthPlan) -> list[dict]:
    return [item.to_row() for item in plan.targets]
