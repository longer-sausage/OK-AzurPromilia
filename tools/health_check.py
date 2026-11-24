"""安装自检命令行：``uv run python tools/health_check.py``。

用法::

    uv run python tools/health_check.py              # 检查当前目录
    uv run python tools/health_check.py --root .     # 指定仓库根目录
    uv run python tools/health_check.py --strict     # 有提示（warning）也返回非 0

退出码：0 = 没问题；1 = 有 error（--strict 下 warning 也算）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.health_check import run_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ok-ap 安装自检")
    parser.add_argument("--root", default=".", help="仓库根目录（默认当前目录）")
    parser.add_argument("--configs", default=None, help="配置目录（默认 <root>/configs）")
    parser.add_argument("--strict", action="store_true", help="有 warning 也返回非 0")
    args = parser.parse_args(argv)

    report = run_all(args.root, args.configs)
    for line in report.lines():
        print(line)

    if not report.ok:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
