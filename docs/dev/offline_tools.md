# 离线工具：养成计算 / 抽卡分析 / 记账报表（批次 C）

这三个模块都不碰游戏、不截图、不发输入，纯计算与文件读写，因此可以完整离线测试：

| 模块 | 目录 | 作用 |
|------|------|------|
| 养成计算 | `src/core/growth/` | 目标 → 经验/尤尼/材料/体力/货币，库存缺口，多目标合并，CSV/PNG 导出 |
| 抽卡分析 | `src/core/gacha/` | 保底层 + 伯努利层概率模型：期望抽数、预算内完成概率、最坏情况 |
| 记账报表 | `src/core/ledger.py` | 结算文字 → `ledger.jsonl`；任务结果 → `runs.jsonl`；两者都能导出 CSV |

## 1. 养成计算

### 数据在数据文件里，代码只做聚合

`assets/data/growth.json` 描述每种目标每一段等级需要什么。**内置数据是示例**
（结构与真实数据一致、数值不完整）；真实数值请从 promilia.wiki 的养成规划器导出后
用 `Dataset.from_file(路径)` 载入。

### 导入真实数据

**推荐：表格导入**（`tools/import_growth.py`）——把 wiki 上的表抄成 CSV/TSV 就行，
Excel 另存为 CSV 也可以，不用手写 JSON：

```bash
uv run python tools/import_growth.py --sample > 我的养成表.csv   # 导出示例表照着改
uv run python tools/import_growth.py 我的养成表.csv --dry-run    # 只校验，先看解析结果
uv run python tools/import_growth.py 我的养成表.csv              # 备份 + 覆盖
```

表格一行 = 一个条目的一段等级区间：

```csv
key,name,kind,max_level,from,to,exp,credits,materials
char_amber,琥珀,character,80,1,20,4000,2400,风之碎晶=3;星辉粉尘=1
char_amber,琥珀,character,80,21,40,12000,7200,风之碎晶=6
```

- 列名大小写/顺序不敏感，多余的列忽略；同一 `key` 的多行自动合并成一个条目；
- `materials` 用 `名字=数量`，多个用 `;`（或 `|`）分隔，留空表示这段不要材料；
- 报错会指到**第几行、哪一列**（「第 3 行的 exp 不是数字」），改一行就能过；
- 表格里出现的材料会自动登记进 `materials`（分类先记 `other`，可事后手改）。

**JSON 直投**：「离线工具 → 养成」页的**「导入数据」**按钮也认 `.csv/.tsv`——
按扩展名自动分流（表格走 `table_import`，JSON 走 `import_dataset`），
所以命令行和界面是同一套解析与校验。代码里直接调 `import_dataset(路径)`：

1. 先 `Dataset.from_file` **完整校验**，校验不过一个字节都不写（不会出现「导入一半」）；
2. 通过后把原文件备份到**目标文件同级的 `backups/`** 下（默认目标 = `assets/data/growth.json`，
   所以就是 `assets/data/backups/growth_<时间戳>.json`；导入到别的路径不会往仓库里写备份），
   同秒重名自动加序号；
3. 再原子替换 `assets/data/growth.json`，页面自动重新载入条目。

返回的 `ImportResult.describe()` 会写清「导入了什么、备份在哪」。
`Dataset.to_json()` 可以做反向导出（当前数据 → JSON），导入器就是用它写盘的。

```json
{
  "materials": {"星脉结晶": {"category": "currency"}},
  "exp_items": {"小型灵粹": 100, "普通的灵粹": 500, "浓缩灵粹": 2000},
  "dungeons": [{"name": "灵粹试炼", "stamina": 20, "drops": {"普通的灵粹": 8}}],
  "aliases": {"星脉结晶": {"大星脉结晶": 5}},
  "entries": [
    {
      "key": "char_amber", "name": "琥珀", "kind": "character", "max_level": 80,
      "levels": [{"from": 1, "to": 20, "exp": 4000, "credits": 2400,
                  "materials": {"风之碎晶": 3}}],
      "potentials": [{"name": "生命", "levels": [{"level": 1, "exp": 100}]}],
      "star_stages": [{"star": 1, "copies": 1, "materials": {"星脉结晶": 10}}]
    }
  ]
}
```

四种类型：`character`（角色）、`pet`（奇波）、`spirit`（灵子）、`equipment`（装备）。
装备的「强化」走 `levels`，「升星」走 `star_stages`（`copies` 是该阶段需要几份本体）。

### 计算规则

- **等级区间按比例切分**：目标 `21→40` 只取第 2 段的经验/材料；`1→999` 会被夹到 `max_level`。
- **份数**：`copies` 会同时放大经验、尤尼、材料与升星本体数。
- **奇波潜能**：`potential_level` 是「所有潜能统一练到几级」，6 条潜能会一起计入经验。
- **升星**：`star` 会累计到该星级的全部 `star_stages`（本体份数 + 材料）。
- **库存**：`inventory` 只影响「缺口」，不影响「需求」。
- **体力折算**：每种材料取 `drops/stamina` 最优的副本 → `体力 = 需求 × 每单位体力`，
  次数按副本掉落向上取整；没有任何副本掉落的材料记进 `no_source`，**不硬算**。
- **货币凑法**：`aliases` 里有兑换比的材料按「大单位优先」凑，余数用原单位，
  且用**缺口**而不是总需求计算（库存里的原单位先抵扣）。
- **经验道具**：贪心用最大的道具，余数用小的兜底。
- 一次最多合并 **12** 个目标。

### 导出

```python
from src.core.growth import Dataset, Target, compute_plan, export_csv, export_png

plan = compute_plan(Dataset.load_default(), [Target("char_amber")], inventory={"尤尼": 20000})
export_csv(plan, "养成材料单.csv")   # 目标表 + 材料单 + 汇总，utf-8-sig 供 Excel 直接打开
export_png(plan, "养成材料单.png")   # 1280 宽卡片，表头 + 斑马纹，缺口标红
```

PNG 用系统里的雅黑/黑体渲染中文；找不到字体时退回 Pillow 默认字体（中文会变方块）。

## 2. 抽卡分析

### 模型

`PityBernoulliModel` = **保底层**（第 i 抽出最高稀有度的概率）+ **伯努利层**
（这次是不是目标，概率 `target_rate`）。

内置参数（社区数值，游戏未公开官方概率表）：

| 项 | 值 |
|----|----|
| 基础概率 | 0.8% |
| 软保底 1 | 第 71 抽起，每抽 +2 个百分点（71 抽 = 2.8%） |
| 软保底 2 | 第 81 抽起，每抽 +8 个百分点（81 抽 = 28.8%） |
| 硬保底 | 第 90 抽 = 100% |

算出来的关键结论（`tests/test_gacha_model.py` 固化为回归基准）：

- 单份目标、`target_rate=100%`：**期望 58.3 抽**，90 抽内必出，最坏 90 抽；
- 单份目标、`target_rate=50%`：**期望 110.8 抽**，180 抽内完成 **82.85%**（与社区公开值一致）；
- `target_rate<100%` 时**不存在有限最坏情况**，结果里会如实说明而不是编一个数。

### 用法

```python
from src.core.gacha import PityBernoulliModel, GachaParams, parse_rate_table

model = PityBernoulliModel()                       # 默认 100% 出目标
print(model.plan(copies=1, pity=0, budget=180).summary())

half = PityBernoulliModel(target_rate=0.5)         # 小保底
half.probability_within(180, copies=1)

custom = PityBernoulliModel(GachaParams(custom_rates=parse_rate_table("2.8%,28.8%,100%")))
```

**已知局限**：模型不追踪游戏里的「大保底状态」。需要模拟大保底就把 `target_rate`
设成 1.0；已经从某个垫抽数开始就用 `pity`。`custom_rates` 的长度必须等于
`hard_pity`，且最后一抽必须是 100%。

`parse_rate_table()` 会帮你挡住抄错的数值（否则贴错一个数就是一堆看起来很像样的错结论）：

| 输入 | 结果 |
|------|------|
| `2.8%, 28.8%, 100%` | ✅ 解析成 `[0.028, 0.288, 1.0]` |
| `150%` / `-5%` | ❌ 「概率必须在 0%~100% 之间」 |
| `50%, 20%, 100%` | ❌ 「概率必须单调不减」 |
| `50%, 60%` | ❌ 「最后一项必须是 100%（硬保底）」 |

### 其它输入的边界

| 输入 | 行为 |
|------|------|
| 养成「份数」填 0 或负数 | `GrowthInputError`（不会静默当 1 份算） |
| 养成目标写成条目 key 字符串 | 接受（省掉包一层 `Target`）；其它类型给出明确错误 |
| 运行报表 `seconds` 为负 / 非数字 | 夹到 0（系统时间跳变不该把平均用时带偏，任务栏与 HUD 都读它） |

## 3. 记账与运行报表

### 界面：「离线工具」页

`src/gui/offline_tools_tab.py`，三张卡片：

- **养成计算**：选目标（按 角色/奇波/灵子/装备 分组）→ 设起始等级 / 目标等级 / 份数 /
  潜能等级 / 升星 → 「添加目标」（可加多个，最多 12 个）→ 填库存 → 「计算」→
  材料单（需求/缺口/体力/次数/无来源标注）→ 「导出 CSV」/「导出 PNG」。
- **抽卡分析**：目标份数 / 已垫抽数 / 预算抽数 / 出目标概率（100% 或 50%）/
  可选自定义概率表 → 「计算」→ 期望、预算内概率、最坏情况与软保底关键点。
- **记账与报表**：粘贴结算文字 → 「记账」；显示今日/累计收支、运行报表统计与今日明细；
  可导出记账 CSV 与报表 CSV。

这一页**不发键鼠、不截图**，只读文件与算数，所以能在界面线程里直接跑。
需要 OCR 的「识别当前画面记账」放在任务里（见下）。

### 结算记账任务

`src/tasks/onetime/settlement_task.py`（「结算记账」，`support_schedule_task=True`）：

1. `ocr_lines()` 把当前画面的 OCR 结果**按视觉行**拼回去 —— 框架返回的文本框是散装的，
   「尤尼」和「×1,500」是两个框，不聚类就会拆行、解析不到数量；
2. `parse_settlement_text()` 解析成收支条目；
3. 写进 `ledger.jsonl`，并报出今日与累计收支。

配置项：`记录来源`（写进 ledger 的来源标记）、`最低置信度`（低于该值的 OCR 文本丢弃）。

### 结算文字解析规则

`parse_settlement_text()` 支持这些写法（`×` 也可以写成 `x` / `*`）：

```text
获得的奖励：尤尼 ×1,500      → 收入 尤尼 1500
12 × 波力果                  → 收入 波力果 12
普通的灵粹*24                → 收入 普通的灵粹 24
消耗：星脉结晶 ×3            → 支出 星脉结晶 3
```

- 含「消耗 / 花费 / 扣除 / 支出 / 使用」的行记为**支出**，其余记为收入；
- 同一条条目被两个正则同时匹配时按位置去重；**纯数字的“名字”会被拒绝**
  （否则「材料2 ×3」会被「数量在前」再匹配一次，凭空多出一条）；
- 一条都没解析出来会抛 `SettlementFormatError`，不会静默记 0。

存储是 `ledger.jsonl`（一行一条 JSON，追加写，坏行读取时跳过）：

```python
from src.core.ledger import Ledger

ledger = Ledger("ledger.jsonl")
ledger.record_text(ocr_text, source="结算界面")
ledger.totals()          # {'income': ..., 'expense': ..., 'net': ...}
ledger.today_summary()   # 按物品的今日收支
ledger.export_csv("ledger.csv")
```

### 运行报表（已接进一条龙、流程与队列）

`RunLog` 写 `runs.jsonl`。两个来源：

- `DailyTaskRunner.execute_task()`：每个任务项前后用**暂停感知**的 `active_time()` 计时；
- `FlowTask.run()`：跑完一次流程 / 队列（含轮椅方案）记一条，名字用流程/队列文件名。
  之前只有日常会写，流程跑完在报表里查不到、任务栏进度也永远估不出来。

```python
log.stats("家园每日")               # count / success / failed / success_rate / avg_seconds / total_seconds
log.by_task()                       # {任务: 上面那些指标}
log.failure_notes(limit=8)          # [(失败原因, 次数)]，按次数降序，没记原因的归到「未记录原因」
log.report_lines()                  # 给 UI 直接渲染的多行报表（总览 + 按任务 + 失败原因）
log.estimate_seconds("家园每日")    # 任务栏「预计剩余」用
log.export_csv("runs.csv")
```

失败时会顺带把**失败原因**写进记录（`DailyTaskRunner` 先 `set_task_failure` 再写报表，
所以报表里是具体原因而不是只有「返回 False」；`FlowTask` 抛异常时记
`异常类型: 消息`）；没有 `note` 字段的旧记录照样能读。

写报表失败**不会影响任务**（失败被吞掉，连磁盘满也一样返回执行结果），测试里固化了这一点。

两个文件都是**追加写 + 读取时容错**，所以手工编辑、断电截断都不会让统计整个崩掉。

## 4. 测试覆盖

| 测试 | 覆盖 |
|------|------|
| `tests/test_growth.py` | 数据载入与校验、区间比例切分、份数/潜能/升星、库存缺口、最优体力来源、无来源标注、大单位优先凑货币、经验道具贪心、12 目标上限、CSV/PNG 导出；导入数据（校验失败不写目标文件、成功时备份原文件、无原文件不备份、同秒备份名去重、源文件缺失） |
| `tests/test_growth_table_import.py` | 表格导入：**CSV/TSV、列名不敏感、多行合并、材料多种分隔符、按等级排序**；错误带行号（kind/数字/区间/材料写法/缺 key/max_level）；**导入写盘与备份、坏表一个字节不写**；`to_dict`/`to_json` 往返（含潜能与升星） |
| `tests/test_gacha_model.py` | 概率表数值、单调性、硬保底、垫抽、期望区间、预算内概率单调、50% 概率的 82.85% 基准、最坏情况、自定义概率表校验（**越界 / 非单调 / 缺硬保底都拒绝**） |
| `tests/test_ledger.py` | 四种结算写法、支出识别、去重、纯数字名字拒绝、无匹配报错、JSONL 坏行容错、按天/按物品汇总、CSV 导出、运行统计与用时估算；报表（失败原因持久化与分组排序、按任务分组统计、多任务/单任务/空报表文本、总用时） |
| `tests/test_flow_task_run_log.py` | 流程/队列跑完写报表（成功、返回 False、抛异常三种结局）、队列用队列名、空名回退到任务类型、写报表失败不影响执行、时钟回退、跑完能估时长 |
| `tests/test_ocr_lines.py` | OCR 文本框按行聚类、行内 x 排序、置信度过滤、异常吞掉、聚类后能被结算解析器吃下 |
| `tests/test_settlement_and_reporting.py` | 结算任务写入 ledger、来源取配置、长列表预览截断；一条龙每项写入 runs.jsonl、禁用项不记、报表失败不影响任务 |

```bash
uv run python -m unittest discover -s tests
```
