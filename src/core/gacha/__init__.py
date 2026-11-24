"""抽卡分析：保底层 + 伯努利层概率模型。

```python
from src.core.gacha import PityBernoulliModel, GachaParams

model = PityBernoulliModel()                       # 内置社区参数
result = model.plan(copies=1, pity=0, budget=180)  # 期望 / 预算内概率 / 最坏
print(result.summary())
```

需要模拟大保底时把 ``target_rate`` 设成 1.0；自定义概率表用
``GachaParams(custom_rates=[...])``。详见 [模块文档](../../docs/dev/offline_tools.md)。
"""

from src.core.gacha.model import (
    DEFAULT_BASE_RATE,
    DEFAULT_HARD_PITY,
    GachaParams,
    GachaResult,
    PityBernoulliModel,
    parse_rate_table,
)

__all__ = [
    "DEFAULT_BASE_RATE",
    "DEFAULT_HARD_PITY",
    "GachaParams",
    "GachaResult",
    "PityBernoulliModel",
    "parse_rate_table",
]
