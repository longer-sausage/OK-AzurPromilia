"""养成数据的表格导入：把「从 wiki 抄出来的一张表」变成 ``assets/data/growth.json``。

为什么要这一层：真实数值只能从 promilia.wiki 导（本站不可达），用户手工填 JSON 很容易
漏字段、写错类型，报错还看不懂。表格（CSV/TSV，Excel 直接另存）是最顺手的中间格式。

**一句话格式**：一行 = 一个条目的一段等级区间。

```csv
key,name,kind,max_level,from,to,exp,credits,materials
char_amber,琥珀,character,80,1,20,4000,2400,风之碎晶=3;星辉粉尘=1
char_amber,琥珀,character,80,21,40,12000,7200,风之碎晶=6
pet_gale,风吟,pet,60,1,30,9000,5400,
```

约定：

- 第一行是表头，**列名大小写与顺序都不敏感**，多余的列会被忽略；
- 同一 ``key`` 的多行会合并成一个条目（等级区间按 ``from`` 排序）；
- ``materials`` 用 ``名字=数量``，多个用 ``;`` 或 ``|`` 分隔；留空表示这段不要材料；
- ``kind`` 只能是 character / pet / spirit / equipment（写错会指出第几行）；
- ``name`` 可留空，默认用 ``key``；
- 表格里出现的材料会自动登记进 ``materials``（``category`` 取 ``other``，可事后手改）。

命令行：

```bash
uv run python tools/import_growth.py 我的养成表.csv --dry-run   # 只校验，不写盘
uv run python tools/import_growth.py 我的养成表.csv            # 校验 → 备份 → 覆盖
```
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from pathlib import Path

from src.core.growth.model import Dataset, GrowthDataError

KINDS = ("character", "pet", "spirit", "equipment")
REQUIRED_COLUMNS = ("key", "kind", "max_level", "from", "to", "exp")
OPTIONAL_COLUMNS = ("name", "credits", "materials", "rarity")
MATERIAL_PAIR_SPLIT = (";", "|", "；")


class TableImportError(GrowthDataError):
    """表格格式不对（带行号，方便直接去改那一行）。"""


def _normalize_header(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "_")


def _read_rows(text: str) -> tuple[list[str], list[list[str]]]:
    """按 CSV/TSV 读出表头与数据行（自动判断分隔符）。"""
    stripped = text.lstrip("\ufeff")
    if not stripped.strip():
        raise TableImportError("表格是空的")
    sample = stripped.splitlines()[0]
    delimiter = "\t" if "\t" in sample and "," not in sample else ","
    reader = csv.reader(io.StringIO(stripped), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise TableImportError("表格没有内容行")
    header = [_normalize_header(cell) for cell in rows[0]]
    return header, rows[1:]


def _number(value: str, row: int, column: str) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw.replace(",", ""))
    except ValueError as exc:
        raise TableImportError(f"第 {row} 行的 {column} 不是数字：{value!r}") from exc


def _materials(value: str, row: int) -> dict[str, float]:
    raw = str(value or "").strip()
    if not raw:
        return {}
    for separator in MATERIAL_PAIR_SPLIT[1:]:
        raw = raw.replace(separator, MATERIAL_PAIR_SPLIT[0])
    result: dict[str, float] = {}
    for piece in raw.split(MATERIAL_PAIR_SPLIT[0]):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise TableImportError(f"第 {row} 行的材料写法应是「名字=数量」：{piece!r}")
        name, _, amount = piece.partition("=")
        name = name.strip()
        if not name:
            raise TableImportError(f"第 {row} 行有空的材料名")
        result[name] = result.get(name, 0.0) + _number(amount, row, f"材料 {name}")
    return result


def parse_table(text: str) -> Dataset:
    """把表格文本解析成 :class:`Dataset`（不写盘）。"""
    header, rows = _read_rows(text)
    if not header:
        raise TableImportError("缺少表头")
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise TableImportError(f"缺少必需的列：{', '.join(missing)}（需要 {', '.join(REQUIRED_COLUMNS)}）")
    index = {column: header.index(column) for column in header if column}

    def cell(row: Iterable[str], column: str) -> str:
        position = index.get(column)
        if position is None:
            return ""
        values = list(row)
        return values[position] if position < len(values) else ""

    entries: dict[str, dict] = {}
    materials: dict[str, dict] = {}
    for offset, row in enumerate(rows, start=2):  # 第 1 行是表头
        key = cell(row, "key").strip()
        if not key:
            raise TableImportError(f"第 {offset} 行缺少 key")
        kind = cell(row, "kind").strip().lower()
        if kind not in KINDS:
            raise TableImportError(
                f"第 {offset} 行的 kind 必须是 {'/'.join(KINDS)}，收到 {kind!r}"
            )
        start = _number(cell(row, "from"), offset, "from")
        end = _number(cell(row, "to"), offset, "to")
        if end <= start:
            raise TableImportError(f"第 {offset} 行的区间无效：from={start:g} 不小于 to={end:g}")
        max_level = _number(cell(row, "max_level"), offset, "max_level")
        if max_level <= 0:
            raise TableImportError(f"第 {offset} 行的 max_level 必须大于 0")
        span_materials = _materials(cell(row, "materials"), offset)
        for name in span_materials:
            materials.setdefault(name, {"category": "other"})

        entry = entries.setdefault(
            key,
            {
                "key": key,
                "name": cell(row, "name").strip() or key,
                "kind": kind,
                "rarity": int(_number(cell(row, "rarity"), offset, "rarity")) or None,
                "max_level": int(max_level),
                "levels": [],
            },
        )
        entry["max_level"] = max(entry["max_level"], int(max_level))
        entry["levels"].append(
            {
                "from": int(start),
                "to": int(end),
                "exp": _number(cell(row, "exp"), offset, "exp"),
                "credits": _number(cell(row, "credits"), offset, "credits"),
                "materials": span_materials,
            }
        )

    payload = {
        "version": 1,
        "note": "由表格导入（tools/import_growth.py）；数值来源请自行核对。",
        "materials": materials,
        "exp_items": {},
        "dungeons": [],
        "aliases": {},
        "entries": list(entries.values()),
    }
    for entry in payload["entries"]:
        entry["levels"].sort(key=lambda span: span["from"])
        if entry["rarity"] is None:
            entry.pop("rarity", None)
    return Dataset.from_dict(payload)


def import_table(
    source: str | Path,
    target: str | Path | None = None,
    backup_dir: str | Path | None = None,
):
    """校验表格 → 写临时 JSON → 走 ``import_dataset``（备份 + 原子替换）。"""
    import tempfile

    from src.core.growth.model import ImportResult, import_dataset

    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return ImportResult(ok=False, message=f"读不了表格：{exc}")
    try:
        dataset = parse_table(text)
    except GrowthDataError as exc:
        return ImportResult(ok=False, message=str(exc))

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "growth.json"
        staged.write_text(dataset.to_json(), encoding="utf-8")
        return import_dataset(staged, target=target, backup_dir=backup_dir)


def sample_table() -> str:
    """给用户照着改的示例表（两行就够看懂）。"""
    return (
        "key,name,kind,max_level,from,to,exp,credits,materials\n"
        "char_amber,琥珀,character,80,1,20,4000,2400,风之碎晶=3;星辉粉尘=1\n"
        "char_amber,琥珀,character,80,21,40,12000,7200,风之碎晶=6\n"
    )


def display_rows(dataset: Dataset) -> list[str]:
    """给 CLI ``--dry-run`` 用：把解析结果说成人话。"""
    lines = []
    for entry in dataset.entries.values():
        spans = "、".join(f"{span.start}-{span.end}" for span in entry.levels)
        lines.append(f"  {entry.key}（{entry.name}，{entry.kind}，满级 {entry.max_level}）：{spans}")
    if dataset.materials:
        lines.append(f"  材料：{'、'.join(dataset.materials)}")
    return lines
