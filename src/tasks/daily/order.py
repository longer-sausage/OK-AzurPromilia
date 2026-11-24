"""一条龙的执行顺序：把「固定写死的 3 项」变成用户可配、可拖动排序。

配置项 ``任务顺序`` 存的是逗号分隔的任务名（就是 ``build_task_plan`` 里
元素的第一项，同时也是配置开关的键名）。排序规则：

- 只认已知任务名，未知项直接忽略（换版本删掉任务时不会崩）；
- 配了但漏掉的任务**追加在末尾**，保持原有相对顺序 —— 新增任务不会因为
  老用户的顺序串里没有它就被静默跳过；
- 空串或全是未知项时退回默认顺序（即代码里声明清单的顺序）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

ORDER_KEY = "任务顺序"
SEPARATORS = (",", "，", "、", ";", "；", "|")

PlanItem = tuple


def split_order(raw: str) -> list[str]:
    """把配置串切成有序任务名列表（去空、去重、保序）。"""
    text = str(raw or "")
    for separator in SEPARATORS[1:]:
        text = text.replace(separator, SEPARATORS[0])
    names: list[str] = []
    for part in text.split(SEPARATORS[0]):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return names


def _names_of(plan_items: Iterable[PlanItem]) -> list[str]:
    return [str(item[0]) for item in plan_items]


def order_plan_items(plan_items: Sequence[PlanItem], order: str | Sequence[str] | None) -> list[PlanItem]:
    """按 ``order`` 重排任务清单；未列出的项追加在末尾。"""
    items = list(plan_items)
    if not items:
        return []

    requested = split_order(order) if isinstance(order, str) or order is None else [str(name) for name in order]
    by_name = {str(item[0]): item for item in items}
    ordered: list[PlanItem] = []
    used: set[str] = set()

    for name in requested:
        item = by_name.get(name)
        if item is None or name in used:
            continue
        ordered.append(item)
        used.add(name)

    for item in items:
        name = str(item[0])
        if name not in used:
            ordered.append(item)
            used.add(name)
    return ordered


def order_text(plan_items: Sequence[PlanItem]) -> str:
    """把当前顺序序列化成配置串。"""
    return ",".join(_names_of(plan_items))


def move_item(names: Sequence[str], name: str, offset: int) -> list[str]:
    """把 ``name`` 上移/下移 ``offset`` 位（GUI 的箭头按钮用）。"""
    result = list(names)
    if name not in result:
        return result
    index = result.index(name)
    target = max(0, min(len(result) - 1, index + int(offset)))
    if target == index:
        return result
    result.insert(target, result.pop(index))
    return result


def available_names(plan_items: Sequence[PlanItem]) -> list[str]:
    return _names_of(plan_items)
