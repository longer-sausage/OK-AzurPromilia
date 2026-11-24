"""养成计算：角色 / 奇波 / 灵子 / 装备的材料、体力折算与导出。

```python
from src.core.growth import Dataset, Target, compute_plan, export_csv, export_png

data = Dataset.load_default()
plan = compute_plan(data, [Target("char_amber", to_level=80)], inventory={"尤尼": 20000})
print("\n".join(plan.summary_lines()))
export_csv(plan, "养成材料单.csv")
export_png(plan, "养成材料单.png")
```

**关于数据**：内置的 ``assets/data/growth.json`` 是**示例数据**（结构与真实数据一致，
数值不完整）。真实数值请从 promilia.wiki 的养成规划器导出后，用
``Dataset.from_file(路径)`` 载入。详见 [离线工具文档](../../docs/dev/offline_tools.md)。
"""

from src.core.growth.calc import (
    GrowthInputError,
    GrowthPlan,
    Target,
    TargetPlan,
    compute_plan,
    material_rows,
    target_rows,
)
from src.core.growth.export import export_csv, export_png
from src.core.growth.model import (
    BACKUP_FOLDER,
    DEFAULT_DATA_PATH,
    GROWTH_KINDS,
    MAX_TARGETS,
    Dataset,
    Dungeon,
    Entry,
    GrowthDataError,
    ImportResult,
    LevelSpan,
    Potential,
    StarStage,
    import_dataset,
)

__all__ = [
    "BACKUP_FOLDER",
    "DEFAULT_DATA_PATH",
    "GROWTH_KINDS",
    "MAX_TARGETS",
    "Dataset",
    "Dungeon",
    "Entry",
    "GrowthDataError",
    "GrowthInputError",
    "GrowthPlan",
    "ImportResult",
    "LevelSpan",
    "Potential",
    "StarStage",
    "Target",
    "TargetPlan",
    "compute_plan",
    "export_csv",
    "export_png",
    "import_dataset",
    "material_rows",
    "target_rows",
]
