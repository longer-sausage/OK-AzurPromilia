# OK-AzurPromilia 项目文档

面向《蓝色星原：旅谣》（Azur Promilia）的游戏自动化项目，基于 [ok-script](https://ok-script.com/) 开发。

## 功能概览

项目已接入实机自动化支持，涵盖日常流程、小游戏玩法接管与辅助功能：

- **一次性日常任务 (`src/tasks/onetime/`)**：
  - **一键日常 (`DailyTask`)**：多子任务编排与状态追踪，一键执行完整日常。
  - **家园日常 (`HomeDailyTask`)**：支持家园烹饪制作、牧场作物收获等家园流程。
  - **每日委托 (`CommissionDailyTask`)**：日常委托接取与提交。
  - **每日收菜 (`ClaimDailyTask`)**：自动领取每日活跃、周常活跃、战令与大月卡奖励。
- **触发式玩法与小游戏 (`src/tasks/trigger/`)**：
  - **自动祖赞卡之梯 (`AutoJengaTask`)**：根据八字摆动轨迹拟合与碰撞面积判定，实现全自动高分放置。
  - **自动花瓣音游 (`AutoRhythmTask`)**：高精度滚动速度拟合与输入时序预测，支持多轨、长按与双键连击。
  - **自动跳过剧情 (`SkipDialogTask`)**：基于中央文本带特征的快速跳过与确认。
  - **星结契约辅助 (`StarLinkAssistTask`)**：屏幕中心准心光圈圆弧拟合与自动命中。
  - **宝箱开锁辅助 (`TreasureUnlockTask`)**：高精度颜色带连通域检测与自动对齐开锁。
- **识别诊断与运行策略（`src/core/` + 「工具与诊断」页）**：
  - **失败帧复测**：任务失败时自动保存识别原始帧，在「工具与诊断」里点一下就把这一帧喂给全部模板并按得分排序。
  - **素材包热更新**：装一个 zip 换掉识别素材，逐文件 SHA-256 校验、旧素材带时间戳备份，坏包不动任何文件。
  - **可调超时与低配模式**：「全局配置 → 自动化策略」里调单步超时/重试间隔/卡死阈值。
  - **模板自检 (src/core/template_audit.py)**：检查标注区域的纹理（纯色块直接拒绝）与 SHA-256 重复。
  - **卡死检测与失败自恢复**：画面长时间不变记录警告；任务失败后按返回键退回主菜单（只退菜单不重跑）。
- **流程编排与配置档（`src/core/flow` `src/core/queue` + 「流程与队列」页）**：
  - **JSON 流程**：移动/点击/按键/等待/等模板/循环/条件，支持嵌套（500 步 / 1000 次 / 4 层上限）。
  - **示例资源 (`assets/{flow,queue,route}_templates/`)**：「安装示例」一键装流程/队列/路线示例（不覆盖同名）；周常活跃奖励与「一条龙」队列开箱可用，危机讨伐 / 剧情委托是「标三个模板就能跑」的骨架，安装后直接列出还缺哪些模板。
  - **断点续跑**：按顶层步骤记录进度，失败后可继续。
  - **录制转流程**：把键鼠录制一键转成流程步骤，如实报告跳过的输入。
  - **任务队列**：把流程 / 一次性任务 / 触发任务串成队列，每项可设分钟预算，失败即停或继续。
  - **配置档**：把 `configs/` 打成命名快照，应用前自动备份。
- **离线工具（纯计算，不碰游戏）**：
  - **养成计算 (`src/core/growth/`)**：角色/奇波/灵子/装备的材料与体力折算、库存缺口、多目标合并、CSV/PNG 导出。
  - **抽卡分析 (`src/core/gacha/`)**：保底层 + 伯努利层模型，算期望抽数、预算内完成概率与最坏情况。
  - **记账与报表 (`src/core/ledger.py`)**：结算文字解析成 `ledger.jsonl`（「结算记账」任务可 OCR 画面），一条龙每项执行写入 `runs.jsonl`，都能导出 CSV。
  - 界面在「离线工具」页（`src/gui/offline_tools_tab.py`）。
- **复用与固化流程**：
  - **一条龙顺序 (`src/tasks/daily/order.py` + 「一条龙顺序」页)**：拖动调整「一键日常」各子任务的执行顺序与开关。
  - **每项自己的参数 (`src/core/task_config_summary.py`)**：选中一项就显示它与默认值不同的参数，可直接打开该任务的配置文件。
  - **导航路线 (`src/core/route.py` + 「导航路线」任务)**：把「怎么走到某界面」写成航点串，跑通即存、可断点续跑。
  - **轮椅方案 (`src/core/replay.py`)**：把多段录制拼成一套固定流程，可循环、可导出成流程文件。
  - **实时辅助 (`src/tasks/trigger/auto_combat_assist_task.py`)**：按配置的按键序列与冷却持续操作，可用前置模板限定生效场合。
- **隐私与支持工具**：
  - **UID 遮挡 (`src/core/privacy.py` + 「隐私遮挡」页)**：最多 8 块纯色/马赛克，1920×1080 参考坐标，作用于落盘截图与失败帧。
  - **坐标拾取 (`src/core/coord_picker.py`)**：客户区像素 ↔ 参考坐标，附带取色与复制片段。
  - **语言包 (`src/core/lang_pack.py`)**：扫描「语言包<代码>」目录，应用/还原带备份。
  - **诊断包 (`src/core/diagnostics_bundle.py`)**：配置+日志+模板清单+失败帧+系统信息打成 zip（默认不含截图）。
  - **崩溃诊断 (`src/core/crash_guard.py`)**：三处异常钩子写 `crashes/`，并自动附诊断包。
  - **在线仓库 (`src/core/repo_index.py` + 「分享仓库」页)**：填一个索引地址就能浏览并一键安装别人分享的流程/队列/轮椅方案/模板包（同名不覆盖、可选 SHA-256）。
- **界面行为（「界面与提醒」页）**：
  - **关闭询问**：点 × 时三选一（完全关闭 / 收到托盘 / 取消），可记住选择；退出流程中不弹窗。
  - **提醒**：每日 04:00 重置提醒 + 按分钟循环的体力提醒，只发通知、不打断任务。
  - **任务栏进度**：按历史平均用时估算、封顶 97%，拿不到窗口句柄自动降级。
  - **全局快捷键**：暂停/继续、停止当前任务、开关 UID 遮挡，可改键（F9–F12 归框架的启动/停止）。
  - **HUD 悬浮条 (`src/core/hud.py` + `src/gui/hud_window.py`)**：置顶小条显示状态/进度/预计剩余，带暂停与停止，不抢焦点。


## 快速导航

- [从源码运行 / 快速上手](dev/quickstart.md)
- [开发指南](dev/development.md)
- [安装自检：uv run python tools/health_check.py](dev/diagnostics_and_policy.md)
- [功能对照表（需求逐条 → 代码/测试/文档）](dev/feature_map.md)
- [验收手册（每条功能怎么点、该看到什么）](dev/acceptance.md)
- [识别诊断与运行策略（失败帧复测 / 素材包 / 卡死自恢复）](dev/diagnostics_and_policy.md)
- [流程、队列与配置档（JSON 编排 / 断点续跑 / 任务队列）](dev/flows_and_queues.md)
- [离线工具：养成计算 / 抽卡分析 / 记账报表](dev/offline_tools.md)
- [一条龙顺序、导航路线与轮椅方案](dev/routes_and_replay.md)
- [隐私遮挡、坐标拾取与支持工具（语言包 / 诊断包 / 崩溃诊断）](dev/privacy_and_support.md)
- [界面行为：关闭询问 / 提醒 / 任务栏进度 / 快捷键 / HUD](dev/ui_behavior.md)
- [Action 生命周期参考](dev/action_lifecycle.md)
- [祖赞卡之梯算法与判定分析](dev/jenga_judgment.md)

## 项目结构

```
├── main.py / main_debug.py     入口（Release / Debug）
├── src/
│   ├── config.py               ok-script 应用配置与任务/Tab 注册
│   ├── globals.py              全局单例
│   ├── icons.py                图标（复用 FluentIcon）
│   ├── core/
│   │   ├── base_game_task.py   任务通用基类（self.loop、暂停感知、配置迁移、配置分组）
│   │   │   └── base_mixin/agent_mixin.py  运行期策略、卡死探针、失败帧、失败自恢复、OCR 分行
│   │   ├── base_mixin/         通用能力 mixin（RuntimeMixin、FrameworkOverrideMixin）
│   │   ├── detector/           Action 生命周期识别层（Hit / Detector 统一判据）
│   │   ├── config_migration.py 配置键迁移工具
│   │   ├── game_window.py      按 exe + 窗口类名定位游戏窗口
│   │   ├── global_config_store.py 项目自建全局配置存储
│   │   ├── runtime_config.py   超时/重试/卡死/自恢复策略（全局配置项）
│   │   ├── failure_frame_store.py 失败帧库（独立目录 + 索引）
│   │   ├── template_rescorer.py 全模板打分（失败帧复测）
│   │   ├── template_audit.py   模板自检（纹理与 SHA-256）
│   │   ├── asset_pack.py       素材包热更新（SHA-256 + 原子安装 + 回滚）
│   │   ├── flow/               JSON 流程：模型/校验、执行器、断点、录制转流程、示例模板
│   │   ├── queue/              任务队列：模型、执行器
│   │   ├── growth/             养成计算：数据模型、计算、CSV/PNG 导出
│   │   ├── gacha/              抽卡概率模型（保底层 + 伯努利层）
│   │   ├── route.py            导航路线：航点模型、校验、执行（含断点）
│   │   ├── replay.py           轮椅方案：多段录制拼成流程
│   │   ├── ledger.py           结算记账（ledger.jsonl）与运行报表（runs.jsonl）
│   │   ├── privacy.py          UID 遮挡（参考坐标 + 纯色/马赛克）
│   │   ├── coord_picker.py     坐标换算与取色
│   │   ├── lang_pack.py        语言包扫描/应用/还原
│   │   ├── diagnostics_bundle.py 诊断包 zip
│   │   ├── crash_guard.py      崩溃诊断钩子
│   │   ├── repo_index.py      在线仓库（加载索引 + 一键安装）
│   │   ├── close_behavior.py   关闭询问（记住选择）
│   │   ├── reminders.py        每日重置 / 体力提醒调度
│   │   ├── hotkeys.py          快捷键解析/校验/注册计划
│   │   ├── taskbar.py          任务栏进度（ITaskbarList3，可降级）
│   │   ├── hud.py              HUD 状态文案与定位（纯逻辑）
│   │   ├── profile_store.py    配置档（configs/ 快照，应用前自动备份）
│   │   ├── task_config_summary.py 子任务参数摘要（「一条龙顺序」页用）
│   │   └── sequence_parser.py  逗号分隔配置串解析
│   ├── tasks/
│   │   ├── onetime/            一次性业务任务（Daily、HomeDaily、CommissionDaily、ClaimDaily、Flow、Route、Settlement）
│   │   ├── trigger/            触发式任务（AutoJenga、AutoRhythm、SkipDialog、StarLink、TreasureUnlock、AutoCombatAssist）
│   │   ├── test/               调试与测试任务（Screenshot、Interaction、TreasureBand、UINavigate）
│   │   ├── daily/              多步骤任务编排与汇总（DailyFeature、步骤统计、运行报表）
│   │   └── account/            账号作用域配置存储
│   ├── gui/                    自定义 Tab（全局配置 / 账号配置 / 一条龙顺序 / 流程与队列 / 离线工具 / 隐私遮挡 / 分享仓库 / 界面与提醒 / 工具与诊断）
│   ├── interaction/            游戏交互（GameInteraction 模拟键鼠、ScreenPosition）
│   ├── image/                  图像算法（旋转模板匹配、连通域、圆弧拟合、音游/叠叠乐检测等）
│   ├── yolo/                   YOLO 模型注册与 OpenVINO 推理
│   ├── patches/                框架启动猴子补丁
│   └── data/
│       ├── feature_list.py     模板匹配特征枚举（由标注自动生成）
│       └── lang/               lang JSON 读取器
├── assets/coco_annotations.json 模板标注（COCO 格式）
├── assets/lang/                OCR 语言匹配 JSON（6 种语言）
├── i18n/*/LC_MESSAGES/ok.po    UI 文案 gettext 国际化目录
├── tests/                      unittest 单元测试
├── scripts/ tools/             编译、检查、多语言维护等工具脚本
├── docs/                       项目与开发文档
└── .github/workflows/          CI/CD 自动化流水线（build / release / pr-test / docs）
```

## 开发与测试

本项目依赖通过 [uv](https://docs.astral.sh/uv/) 管理，支持 **Python 3.12**：

```bash
# 安装/同步依赖
uv sync

# 运行 Debug 模式
uv run python main_debug.py

# 执行全量单元测试
uv run python -m unittest discover -s tests
# 或使用 PowerShell 逐文件运行测试
./run_tests.ps1
```
