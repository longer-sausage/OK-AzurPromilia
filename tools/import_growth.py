"""把养成数据的表格（CSV/TSV）导入成 ``assets/data/growth.json``。

```bash
uv run python tools/import_growth.py --sample > 我的养成表.csv   # 导出示例表
uv run python tools/import_growth.py 我的养成表.csv --dry-run   # 只校验
uv run python tools/import_growth.py 我的养成表.csv            # 备份并覆盖
```

表格格式见 ``src/core/growth/table_import.py`` 顶部说明：一行 = 一个条目的一段等级区间。
真实数值可从 promilia.wiki 的养成规划器抄成表格（本站不可直接访问，需要你手工导出）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.growth.model import DEFAULT_DATA_PATH, GrowthDataError  # noqa: E402
from src.core.growth.table_import import (  # noqa: E402
    display_rows,
    import_table,
    parse_table,
    sample_table,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把养成数据表格导入成 growth.json")
    parser.add_argument("source", nargs="?", help="表格文件（CSV 或 TSV）")
    parser.add_argument(
        "--target",
        default=str(DEFAULT_DATA_PATH),
        help=f"写入目标（默认 {DEFAULT_DATA_PATH}）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只校验，不写盘")
    parser.add_argument("--sample", action="store_true", help="输出示例表（可以重定向到文件）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.sample:
        sys.stdout.write(sample_table())
        return 0

    if not args.source:
        print("需要指定表格文件（或用 --sample 看格式）", file=sys.stderr)
        return 2

    source = Path(args.source)
    if not source.is_file():
        print(f"找不到表格：{source}", file=sys.stderr)
        return 2

    try:
        dataset = parse_table(source.read_text(encoding="utf-8-sig"))
    except (GrowthDataError, OSError) as exc:
        print(f"❌ 表格有问题：{exc}", file=sys.stderr)
        return 1

    print("解析结果：")
    for line in display_rows(dataset):
        print(line)

    if args.dry_run:
        print("\n✅ 校验通过（--dry-run，没有写盘）")
        return 0

    result = import_table(source, target=args.target)
    if not result.ok:
        print(f"\n❌ {result.message}", file=sys.stderr)
        return 1
    detail = "、".join(f"{name} {count}" for name, count in result.summary.items() if count)
    print(f"\n✅ 已导入：{detail}")
    print(f"   写入 {result.path}")
    if result.backup:
        print(f"   原文件备份 {result.backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
