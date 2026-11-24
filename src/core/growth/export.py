"""养成结果的导出：CSV（Excel 直接开）与 PNG 卡片（发群里用）。

PNG 卡片固定 1280 宽，表头 + 斑马纹，中文字体优先用系统里的雅黑/黑体，
找不到时退回 Pillow 默认字体（此时中文会显示成方块，函数会把这件事写进返回值说明）。
"""

from __future__ import annotations

import csv
import time
from collections.abc import Sequence
from pathlib import Path

from src.core.file_naming import ensure_suffix
from src.core.growth.calc import GrowthPlan, material_rows, target_rows

CARD_WIDTH = 1280
ROW_HEIGHT = 34
PADDING = 32
HEADER_HEIGHT = 96
FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)
MATERIAL_COLUMNS = ("材料", "需求", "拥有", "缺口", "体力", "次数", "来源")
TARGET_COLUMNS = ("类型", "目标", "等级", "份数", "阶段", "经验", "尤尼")


def export_csv(plan: GrowthPlan, path: str | Path) -> Path:
    """把材料单与目标表一起写成 CSV（两段，中间空一行）。

    扩展名由这里补（用户手输 `养成材料单` 也会得到 `.csv`），返回补全后的路径。
    """
    target = ensure_suffix(path, ".csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["养成目标"])
        writer.writerow(list(TARGET_COLUMNS))
        for row in target_rows(plan):
            writer.writerow([_cell(row.get(column, "")) for column in TARGET_COLUMNS])

        writer.writerow([])
        writer.writerow(["材料清单"])
        writer.writerow(list(MATERIAL_COLUMNS))
        for row in material_rows(plan):
            writer.writerow([_cell(row.get(column, "")) for column in MATERIAL_COLUMNS])

        writer.writerow([])
        writer.writerow(["汇总"])
        writer.writerow(["总经验", _cell(plan.total_exp)])
        writer.writerow(["总尤尼", _cell(plan.total_credits)])
        writer.writerow(["总体力", _cell(round(plan.total_stamina, 1))])
        writer.writerow(["副本次数", plan.total_runs])
        for name, count in plan.exp_items.items():
            writer.writerow([f"经验道具 {name}", count])
        for name, count in plan.crystals.items():
            writer.writerow([f"货币 {name}", count])
    return target


def _cell(value) -> str:
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.1f}"
    return str(value)


def export_png(plan: GrowthPlan, path: str | Path, title: str = "养成材料单") -> Path:
    """渲染 1280 宽的材料单卡片，返回文件路径。"""
    from PIL import Image, ImageDraw

    rows = material_rows(plan)
    targets = target_rows(plan)
    height = HEADER_HEIGHT + (len(targets) + len(rows) + 4) * ROW_HEIGHT + PADDING
    image = Image.new("RGB", (CARD_WIDTH, max(240, height)), "white")
    draw = ImageDraw.Draw(image)

    font_regular, font_bold = _load_fonts()

    # 标题
    draw.rectangle((0, 0, CARD_WIDTH, HEADER_HEIGHT), fill=(36, 41, 51))
    draw.text((PADDING, 22), title, font=font_bold, fill=(255, 255, 255))
    subtitle = (
        f"目标 {len(targets)} 个 · 经验 {plan.total_exp:,.0f} · 尤尼 {plan.total_credits:,.0f}"
        f" · 体力约 {plan.total_stamina:,.0f}"
    )
    draw.text((PADDING, 58), subtitle, font=font_regular, fill=(198, 205, 219))

    y = HEADER_HEIGHT
    columns = ("材料", "需求", "缺口", "体力", "次数")
    widths = (520, 170, 170, 170, 170)
    y = _draw_row(draw, columns, widths, y, font_bold, fill=(236, 240, 248), text_color=(28, 32, 40))
    for index, row in enumerate(rows):
        values = (
            str(row["材料"]),
            _cell(row["需求"]),
            _cell(row["缺口"]),
            _cell(row["体力"]),
            str(row["次数"]) if row["次数"] else "-",
        )
        fill = (255, 255, 255) if index % 2 == 0 else (246, 248, 252)
        color = (170, 60, 60) if row["缺口"] else (28, 32, 40)
        y = _draw_row(draw, values, widths, y, font_regular, fill=fill, text_color=color)

    if plan.exp_items or plan.crystals:
        y += ROW_HEIGHT // 2
        extras = []
        if plan.exp_items:
            extras.append("经验道具：" + "、".join(f"{name} ×{count}" for name, count in plan.exp_items.items()))
        if plan.crystals:
            extras.append("货币：" + "、".join(f"{name} ×{count}" for name, count in plan.crystals.items()))
        for line in extras:
            draw.text((PADDING, y), line, font=font_regular, fill=(60, 66, 80))
            y += ROW_HEIGHT

    if plan.no_source:
        draw.text(
            (PADDING, y),
            "无稳定来源：" + "、".join(plan.no_source),
            font=font_regular,
            fill=(170, 60, 60),
        )
        y += ROW_HEIGHT

    target = ensure_suffix(path, ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return target


def _draw_row(draw, values: Sequence[str], widths: Sequence[int], y: int, font,
              fill, text_color) -> int:
    from PIL import ImageDraw  # noqa: F401 - 仅用于类型提示

    total = sum(widths)
    draw.rectangle((PADDING, y, PADDING + total, y + ROW_HEIGHT), fill=fill)
    x = PADDING + 12
    for value, width in zip(values, widths, strict=False):
        draw.text((x, y + 8), str(value), font=font, fill=text_color)
        x += width
    return y + ROW_HEIGHT


def _load_fonts():
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, 18), ImageFont.truetype(candidate, 26)
        except OSError:
            continue
    default = ImageFont.load_default()
    return default, default


def default_filename(prefix: str = "养成材料单") -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
