# 流程、队列与配置档（批次 B）

三层东西，互相独立又常一起用：

- **流程（flow）**：JSON 描述的步骤树，比键鼠录制更「可编程」，能嵌套循环和条件；
- **队列（queue）**：把「流程 / 一次性任务 / 触发任务」串成一条顺序队列，可以给每项设分钟预算；
- **配置档（profile）**：把 `configs/` 打成一个命名快照，切场景前一键换回来。

执行入口统一是任务 **「流程 / 队列」**（`src/tasks/onetime/flow_task.py`），
编辑与校验在 **「流程与队列」** 页（`src/gui/flow_tab.py`）。

## 1. 流程 JSON

文件位置 `configs/flows/<名字>.json`。

```json
{
  "name": "每日刷图",
  "description": "可选的说明",
  "version": 1,
  "steps": [
    {"type": "wait_template", "template": "main_ui", "timeout": 10},
    {"type": "click", "template": "commission_icon"},
    {"type": "wait", "seconds": 1.0},
    {"type": "loop", "times": 3, "steps": [
      {"type": "click", "x": 0.5, "y": 0.8},
      {"type": "wait_template", "template": "result_panel", "expect": "disappear", "settle": 0.5}
    ]},
    {"type": "if", "template": "confirm_button",
     "steps": [{"type": "action", "action": "back"}],
     "else": [{"type": "wait", "seconds": 0.2}]}
  ]
}
```

### 步骤类型

| type | 必填 | 可选 | 说明 |
|------|------|------|------|
| `move` | `direction` 或 `dx`/`dy` | `duration` | `direction` 是 `w/a/s/d` 组合（`wd` 表示右上）；`dx/dy` 是相对鼠标位移（转视角） |
| `click` | `x`+`y` 或 `template` | `times`、`duration`、`hold` | 比例坐标 0..1；给了 `template` 就等它出现并点它的中心；`duration` 是点击后等待，`hold` 是**按住多久再松开**（长按/蓄力） |
| `action` | `action` | `args` | 调用白名单里的任务方法，`args` 原样作为关键字参数 |
| `key` | `key` | `duration`、`times` | `duration` 是按住时长 |
| `wait` | `seconds` | — | 纯等待（暂停感知） |
| `wait_template` | `template` | `expect`、`settle`、`threshold`、`timeout` | `expect` = `appear`（默认）/ `disappear`；`settle` = 状态需连续保持的秒数 |
| `loop` | `times` 或 `until_template` 或 `until_not_template` | `max`、`timeout` | `times` 固定次数；`until_*` 是「直到出现 / 消失」，`max` 是安全上限 |
| `if` | `template` | `steps`、`else`、`threshold` | 命中走 `steps`，否则走 `else` |

公共字段：`timeout`（这一步的等待上限）、`comment`（备注）。

### 硬限制

| 限制 | 值 | 行为 |
|------|----|------|
| 总步数 | 500 | 超过直接**校验失败**，不会「尽力执行」 |
| 单层循环次数 | 1000 | `times` / `max` 必须在 1..1000 |
| 嵌套深度 | 4 | 超过即校验失败 |

另外：`type` 不认识、字段名拼错（例如把 `seconds` 写成 `secs`）、
比例坐标超范围、`loop` 同时给了两个条件 —— 都会被 `validate_flow()` 报成 error，
执行前先校验，有 error 就不跑。

### 执行语义

- **断点续跑**：每完成一个**顶层**步骤写一次进度到 `configs/flow_progress/<流程名>.json`；
  任务里勾「从断点继续」就从下一个顶层步骤开始。顶层步骤内部的循环/条件会**重新执行**，
  因为半途状态无法可靠重建。
- **时间预算**：`FlowExecutor(budget_seconds=…)` 除了在每一步之前检查，**长等待也会被预算截断**——
  `wait` 只睡到预算边界、`wait_template`/`click(template)` 的轮询里也检查预算。
  超了抛 `FlowBudgetExceeded`（`FlowError` 的子类），结果里 `budget_exceeded=True`、
  `summary()` 写「超出时间预算，已中止」。
  - 预算**不是步骤失败**：`continue_on_error=True` 也不会继续刷剩下的步骤
    （否则会一路撞预算、日志刷屏），整条流程直接中止；
  - 被打断的那一步**不算完成**，断点停在最后一个成功步骤，续跑会重跑它。
- **失败策略**：任务配置「失败后继续」= `continue_on_error`；失败项会记进
  `FlowRunResult.failed`，最后给出 `summary()`。
- 全部跑完会清掉断点，避免下次误跳过。

### 动作白名单

`{"type": "action", "action": "…"}` 只能调用
`src/core/flow/executor.py` 的 `ACTION_ALLOWLIST`（`back` / `ensure_main` / `dodge` /
`press_combat_key` / `click_relative` / `next_frame` / `sleep` / `wait_ui_stable` …）。

流程文件是用户可编辑的 JSON，**不能让任意字符串变成任意方法调用**。
需要新动作就往白名单里加一条，并在测试里补一个用例。

## 2. 录制动作 → 流程

### 界面里直接录制

「流程与队列 → 流程」卡片里有一行录制按钮：

1. **先连接游戏窗口**（录制按游戏窗口换算坐标，没连窗口坐标会退化成原始像素而被跳过）；
2. 点「开始录制动作」→ 按钮变成「停止录制并生成流程」，此时**任务在跑会被拒绝**
   （提示「有任务正在运行（xxx），先停止任务再录制」，避免把自动点击也录进去）；
3. 在游戏里正常操作（左键点击、按键；右键/滚轮/鼠标移动不会进流程）；
4. 点「停止录制并生成流程」→ 生成 `configs/flows/录制流程_<月日_时分秒>.json` 并刷新下拉框，
   状态栏显示「转换 N 步，跳过 M 个无法表达的输入」。

录制用的是框架的 `ok.ui.qt.tasks.RecordScript.recorder`（pynput 全局钩子），
和「编辑任务」里的录制是同一个录像器；本项目的区别是**产出流程 JSON**而不是 Python 代码。

### 手工转换（已有事件 JSON）

`src/core/flow/recording.py` 的 `convert_recording(events)`：
也可以把别处录到的 `{"events": [...]}` 文件用「从录制 JSON 生成」导进来。

- `mouse_down`/`mouse_up` 配对成 `click`：
  - 按住 **≥ 0.3 秒** → 写 `hold`（执行时真的 `mouse_down` → 等 → `mouse_up`），
    长按/蓄力不会被改成「点一下再等」；
  - 短按 → 时长写进 `duration`（点击后等待）；
- `key_down`/`key_up` 配对成 `key`，同样保留按住时长；
- 相邻动作之间超过 `gap_threshold`（默认 0.05s）的空档补一个 `wait` 步骤；
- **无法表达的输入如实计入 `skipped`**：右键、中键、滚轮、鼠标移动；
- **坐标异常的点击计入 `bad_coordinates` 并跳过**：录制器拿不到窗口尺寸时
  `normalize_coords` 会退回原始像素（如 `1200, 700`），那种值当不了比例坐标，
  硬塞进流程只会在校验时报「比例越界」，所以直接跳过并在 `notes` 里说明；
- 上限 **400 步**，超了截断并标记 `truncated`；
- 只按过没抬起的键、按下没抬起的鼠标，按短按/零时长处理并在 `notes` 里说明。

「长按」退化说明：任务没有 `mouse_down`/`mouse_up`（或拿不到画面尺寸）时，
`hold` 会退化成「点击 + 等待 `hold` 秒」，并往日志里写一行说明——**不静默改变语义**。

「流程与队列」页的「从录制 JSON 生成」接受两种格式：裸事件数组，或 `{"events": [...]}`。

## 3. 任务队列

文件位置 `configs/queues/<名字>.json`。

```json
{
  "name": "早上",
  "stop_on_failure": true,
  "items": [
    {"kind": "task", "ref": "DailyTask", "budget_minutes": 20, "name": "一条龙"},
    {"kind": "flow", "ref": "每日刷图", "budget_minutes": 10},
    {"kind": "scheme", "ref": "轮椅-深渊", "budget_minutes": 5}
  ]
}
```

| 字段 | 说明 |
|------|------|
| `kind` | `flow` / `task` / `assist` / `scheme` |
| `ref` | 流程名，或任务**类名**（也接受任务显示名） |
| `budget_minutes` | 这一项的预算；0 或缺省 = 不限 |
| `enabled` | 缺省 true；false 表示跳过（仍保留在队列里） |
| `stop_on_failure` | 队列级：失败即停（true）/ 失败后继续（false） |

`QueueRunner` 不知道任务怎么跑，靠调用方注入
`resolver(item) -> callable(budget_seconds) -> bool`，所以队列本身可以离线测试。

### 预算怎么算

预算有两层，都会被真的执行：

| 层 | 来源 | 行为 |
|----|------|------|
| **队列总预算** | 「流程 / 队列」任务的 `预算(分钟)` | 累计用时超了**就不再开始新项**，剩下的记「跳过：总预算已用完」 |
| **单项预算** | 队列项的 `budget_minutes` | 传给执行器的预算是 `min(单项预算, 总预算剩余)`；流程/方案会被它**真的打断** |

任务类（`task` / `assist`）没法中途打断——它不收预算参数，也不该被外部强杀。
所以对这类项改成**开始前用历史均值预估**（`RunLog.estimate_seconds`）：
剩余时间不够就不开始，日志写清「预算不足（预计需要 10 分钟，只剩 3 分钟）」。
预估拿不到（没跑过）就照常执行，不会因为「不知道」而跳过。

> 诚实边界：任务类一旦开始，就只会在结束后核对「是否超预算」（结果里标 `budget_exceeded`），
> 不会谎称已经停下。想真正可控，就把那件事写成流程。

队列项类型映射（`FlowTask._queue_resolver`）：

- `flow` → 作为子流程执行（预算透传）；
- `task` → 在 `executor.onetime_tasks` 里按类名/显示名找实例并调 `run()`
  （`run()` 返回 `False` 才算失败，与 `DailyTaskRunner` 的约定一致）；
- `assist` → 在 `executor.trigger_tasks` 里找实例并调一次 `run()`。
  **持续辅助**应在触发任务列表里启用，不要指望队列「持续」某件事；
- `scheme`（轮椅方案）→ 读 `configs/schemes`，拼装成流程后交给同一个执行器。

## 4. 配置档

`src/core/profile_store.py`，目录 `configs/profiles/`。

```text
configs/profiles/
├── 工作/
│   ├── profile.json      # 名字、保存时间、文件清单（相对路径 + 大小）
│   └── files/…           # 与 configs/ 相对路径一致的快照
└── .backup_20261124_011530/   # 应用配置档前的自动备份
```

- **快照范围** = `configs/` 下除 `profiles/` 之外的全部文件（递归，含 `queues/`、`flows/`）；
- **应用前自动备份**当前的 `configs/`；
- 应用时只覆盖档里存在的文件，**不删除档里没有的文件**（避免误删），
  返回值 `extra` 如实报告「当前有、档里没有」的文件数；
- 名字校验拒绝空、`.` 开头、以及 `\/:*?"<>|` —— 防止路径逃逸；
- 全部写盘都是 `.tmp` + `os.replace`。

### 失败时不会留下半套配置

这两条是配置档最该保证、也最容易漏的（都是「写到一半断了」）：

| 场景 | 现在的行为 |
|------|-----------|
| **保存中途失败**（磁盘满、文件被占用） | 先写 `files.new`，全部成功才替换旧快照；失败时删掉暂存目录，**旧档原封不动** |
| **应用中途失败** | 已自动备份当前配置 → 按备份把覆盖过的文件写回去（新增的文件删掉），抛 `ProfileError` 并说明「已按备份回滚」 |
| `profile.json` 被手改坏（含 `../..`） | 应用前校验每个相对路径，越界直接拒绝（不会写到 `configs/` 外面） |

## 5. 任务「流程 / 队列」

`src/tasks/onetime/flow_task.py`，注册在 `src/config.py` 的 `onetime_tasks`，
并声明 `support_schedule_task = True`（可被 Windows 计划任务触发）。

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `任务类型` | 执行流程 | 执行流程 / 执行队列 |
| `流程文件` | （空） | `configs/flows` 下的名字（不带 `.json`） |
| `队列文件` | （空） | `configs/queues` 下的名字 |
| `从断点继续` | True | 只对流程生效 |
| `失败后继续` | False | 流程单步失败后是否继续 |
| `预算(分钟)` | 30 | 0 = 不限 |

命令行也能跑：`ok-ap.exe -t 5`（序号按 `onetime_tasks` 顺序，`FlowTask` 目前是第 5 个）。

## 6. 「流程与队列」页

`src/gui/flow_tab.py`（懒加载）：

- **流程**：下拉选文件 → 预览结构化步骤树（含嵌套缩进）→ 「校验」列出全部问题 →
  导入 JSON / 从录制 JSON 生成 / **安装示例流程** / 删除 / 打开目录；下方显示断点进度。
- **任务队列**：下拉选队列 → 预览每项的类型/预算/是否禁用 → 校验 / 新建空模板 / 删除。
- **配置档**：下拉选档 → 保存当前配置 / 应用 / 删除；状态行说明备份位置。

**这一页不发键鼠**：执行统一走任务体系（暂停、停止、调度、账号上下文都在框架里），
避免界面线程和正在跑的任务抢输入。

## 7. 示例资源（流程 / 队列 / 路线）

「流程与队列」页的「安装示例」把 `assets/{flow,queue,route}_templates/` 里的示例一次装进
`configs/flows|queues|routes/`（**不覆盖同名**——你可能已经改过一份）。每份示例在安装前都会
用**对应的模型**校验一遍（流程 `parse_flow`+`validate_flow`、队列 `TaskQueue.from_dict`、
路线 `Route.from_dict`+`validate`），有错的那份会被跳过并说明原因，不影响其它份。

### 流程

| 文件 | 需要标注的模板 | 说明 |
|------|----------------|------|
| `周常活跃奖励.json` | 无（全部已有） | 装好就能跑：进周常页签 → 循环领到「领取」按钮消失 → 回主界面 |
| `危机讨伐（骨架）.json` | `crisis_hunt_entry`、`crisis_hunt_start`、`crisis_hunt_settle` | 入口 / 开始 / 结算三个点标好即可 |
| `剧情委托（骨架）.json` | `story_commission_entry`、`story_commission_auto`、`story_commission_settle` | 同上；结算等待给了 20 分钟 |

骨架流程的模板名**已经写死在 JSON 里**，标注时照抄名字就行（模板名来自 COCO 标注的
category，标完热更新即生效）。安装完成后状态栏会直接列出「还需要标注：…」——
这份清单由 `missing_templates()` 对比 `FeatureList` 得出，不需要你自己去数。

> 「周常活跃奖励」用的是 `active_weekly_check` / `active_weekly_claim_task` / `confirm_button` /
> `loading_check`，这四个仓库里本来就有（`每日收菜` 在用），所以它开箱可用。

### 队列

`一条龙.json`：家园 15 分钟 → 委托 30 分钟 → 收菜 10 分钟，失败即停。装好后在
「流程 / 队列」任务里把**队列文件**填 `一条龙` 就能跑（也可以用「一键日常」任务，
两者的差别是队列能按项设预算并混入流程/轮椅方案）。

### 路线

`主界面到背包.json`：等 `menu_backpack` → 点背包按钮（比例坐标 `[0.92, 0.88]`，按你的
分辨率校准）→ 等界面稳定。装好后在「导航路线」任务里填这个名字。它同时是一份**写法示例**：
航点可以是 `page` / `template` / `click` / `wait` 的任意组合。

## 8. 测试覆盖

| 测试 | 覆盖 |
|------|------|
| `tests/test_flow.py` | 解析/校验（含 500 步、4 层、1000 次、字段拼错、比例越界）、全部步骤类型、循环/条件、失败策略、预算（含长等待被截断、预算不记为步骤失败、断点停在最后成功步骤）、断点存取与续跑 |
| `tests/test_flow_recording.py` | down/up 配对、空档补 wait、跳过右键/滚轮、未抬起处理、400 步截断；**长按转 `hold`、短按保留 `duration`、原始像素坐标跳过并计数、超出 1 的小误差被夹取** |
| `tests/test_flow_recording_gui.py` | 界面录制接线：开始调框架 recorder、**任务运行中拒绝录制**、停止后事件转成流程并落盘、空录制不生成文件 |
| `tests/test_flow_templates.py` | 三份示例流程都能解析且零错误、周常流程不需要新模板、骨架流程各报 3 个待标模板、模板依赖收集（嵌套/去重）、`FeatureList` 取 `value`；队列/路线示例同样校验通过；三类安装计数、不覆盖、坏示例被跳过、未知类型报错 |
| `tests/test_task_queue.py` | 模型校验、文件存取、执行顺序、失败即停/继续、禁用项、未知项、预算标记；**总预算截断剩余项、传给执行器的预算取最小值、任务类预估超时跳过、流程类不因预估跳过、预估拿不到照常执行** |
| `tests/test_profile_store.py` | 快照、应用前备份、不删档外文件、名字校验、损坏元信息容错；**保存失败时旧档不变且无残留暂存目录、应用失败按备份回滚、档里的 `../..` 路径被拒绝** |

```bash
uv run python -m unittest discover -s tests
```
