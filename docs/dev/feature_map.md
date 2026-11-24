# 功能对照表：需求清单逐条落地在哪

这份表是给「验收」用的：需求里每一条对应到**代码位置 / 测试 / 文档**，
以及**框架已经提供**或**受框架限制**的部分。找不到对应行的，就是还没做。

想自己试一遍的话看 **[验收手册](acceptance.md)**（每条功能：点哪里、该看到什么）。

状态说明：

- ✅ **已实现**：本项目代码实现，有离线测试；
- 🔧 **框架提供**：ok-script 自带能力，本项目只做配置或薄封装（不改框架）；
- ⚠️ **受限制**：框架层面做不到，见备注；
- 🚧 **需实机**：逻辑已就绪，但需要游戏内界面/截图才能落地具体模板或坐标。

---

## 一、模板与识别基础设施

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| 识别失败自动存帧（最近 N 张） | ✅ | `src/core/failure_frame_store.py` | `test_failure_frame_store.py` | [诊断](diagnostics_and_policy.md) §4 |
| 失败帧复测：整帧扫全部模板并报每个匹配分数（落盘前已过 UID 遮挡） | ✅ | `src/core/template_rescorer.py` + 「工具与诊断」页 | `test_template_rescorer.py` | 同上 §5 |
| 模板标注工具（框选/命名/增删/存标注） | 🔧 | 框架 `ui/qt/markup` | — | 同上 §5 |
| 标注区域纹理检查（纯色块拒绝） | ✅ | `src/core/template_audit.py` | `test_template_audit.py` | 同上 §7 |
| 模板 SHA-256（重复检测） | ✅ | 同上 | 同上 | 同上 §7 |
| 模板/素材热更新（逐文件校验 + 备份 + 回滚） | ✅ | `src/core/asset_pack.py` | `test_asset_pack.py` | 同上 §6 |
| 超时/重试/动作后等待/失败帧数量可调 | ✅ | `src/core/runtime_config.py` + 全局配置「自动化策略」 | `test_runtime_config.py` | 同上 §1 |
| 低配模式（超时 ×1.5，阈值不动） | ✅ | 同上 | 同上 | 同上 §1 |
| 卡死检测（画面长时间不变告警） | ✅ | `src/core/base_mixin/agent_mixin.py` `agent_check_stuck` | `test_stuck_and_recovery.py` | 同上 §2 |
| 失败自恢复（回主菜单，带次数上限） | ✅ | 同上 `AgentMixin.recover_after_failure` | 同上 | 同上 §3 |

## 二、日常自动化

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| 一条龙：顺序可配、可拖动排序 | ✅ | `src/tasks/daily/order.py` + 「一条龙顺序」页 | `test_daily_order.py` | [路线](routes_and_replay.md) §1 |
| 一条龙每项自己的参数可见 / 可打开配置 | ✅ | `src/core/task_config_summary.py` | `test_task_config_summary.py` | 同上 §1 |
| 一条龙跑完的执行汇总（日志 + txt 报告，多轮/多账号分组，旧报告按天数清理） | ✅ | `src/tasks/daily/daily_summary.py` | `test_daily_summary.py` | 同上 §1 |
| 周常活跃奖励 | ✅ | `assets/flow_templates/周常活跃奖励.json`（只用已有模板，开箱可跑） | `test_flow_templates.py` | [流程](flows_and_queues.md) §7 |
| 危机讨伐 / 剧情委托 | 🚧 | `assets/flow_templates/`（骨架流程，各差 3 个模板；模板名已写死，标好照抄即可） | 同上 | 同上 §7 |
| 实时辅助（按设置持续操作） | ✅ | `src/tasks/trigger/auto_combat_assist_task.py` | `test_auto_combat_assist_task.py` | 同上 §4 |
| 导航测试 / 路线管理（航点串、断点续跑） | ✅ | `src/core/route.py` + 「导航路线」任务 | `test_route.py` | 同上 §2 |
| 动作录制 → 流程（框架录制器 + 转换；长按转 hold、异常坐标跳过） | ✅ | `src/core/flow/recording.py` | `test_flow_recording.py` | [流程](flows_and_queues.md) §2 |
| 轮椅方案（多段录制拼成固定流程） | ✅ | `src/core/replay.py`（可循环、可导出成流程、可进队列） | `test_replay.py` | [路线](routes_and_replay.md) §3 |

## 三、流程编排

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| JSON 流程（500 步 / 1000 次 / 4 层嵌套） | ✅ | `src/core/flow/model.py`、`executor.py` | `test_flow.py`、`test_flow.py` | [流程](flows_and_queues.md) §1 |
| 录制一键转流程（最多 400 步） | ✅ | `src/core/flow/recording.py` | `test_flow_recording.py` | 同上 §3 |
| 断点续跑（跳过已完成顶层步骤；结构变了则忽略旧断点） | ✅ | `executor.CheckpointStore` | 同上 | 同上 §2 |
| 任务队列（含分钟预算、失败即停） | ✅ | `src/core/queue/` | `test_queue*.py` | 同上 §4 |
| 队列项支持流程 / 队列 / 任务 / 轮椅方案 | ✅ | `flow_task._queue_resolver` | `test_replay.py` | 同上 §4 |
| 定时触发（框架的计划任务） | 🔧 | `support_schedule_task = True` | — | 同上 §5 |
| 配置档（快照 / 应用前备份 / 增删；失败回滚） | ✅ | `src/core/profile_store.py` | `test_profile_store.py` | 同上 §6 |

## 四、养成计算

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| 角色/奇波/灵子/装备材料计算 | ✅ | `src/core/growth/` | `test_growth.py` | [离线工具](offline_tools.md) §1 |
| 数据来自数据文件（换版本只换数据） | ✅ | `assets/data/growth.json` | 同上 | 同上 §1 |
| 从 wiki 导出后导入（校验 + 备份 + 原子替换） | ✅ | `growth.import_dataset` + 「导入数据」 | 同上 | 同上 §1 |
| 导出 CSV / PNG | ✅ | `src/core/growth/export.py` | 同上 | 同上 §1 |

> ⚠️ 内置数据是**示例**（结构与真实一致、数值不完整），真实数值需要你导入。

## 五、抽卡分析

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| 概率模型（0.8% 基础、71 抽起加概率、90 硬保底） | ✅ | `src/core/gacha/model.py` | `test_gacha_model.py` | 同上 §2 |
| 预算内概率 / 期望抽数 / 最坏情况 | ✅ | 同上 | 同上 | 同上 §2 |

## 六、记账与报表

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| 结算界面 OCR 记账（多种写法） | ✅ | `src/core/ledger.py` + `settlement_task` | `test_ledger.py` | 同上 §3 |
| 运行报表（时长 / 成功率 / 按任务 / 失败原因；日常 + 流程/队列都记） | ✅ | `RunLog.by_task` / `failure_notes` / `report_lines`，写入点在 `DailyTaskRunner` 与 `FlowTask.run` | `test_ledger.py`、`test_flow_task_run_log.py` | 同上 §3 |

## 七、界面

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| 主题（浅色/深色/跟随系统） | 🔧 | 框架 `qfluentwidgets` 设置 | — | [界面行为](ui_behavior.md) §7 |
| 界面透明度 / 背景图 | ⚠️ | 框架 2.0.6 **无任何透明度 API**（`blurRadius` 是死配置） | — | 同上 §7 |
| 悬浮窗（图标态 / 面板态：任务列表、队列每项 ETA、颜色与不透明度≥10%、拖动与缩放） | ✅ | `src/core/float_window.py` + `src/gui/float_window.py` + 「悬浮窗」页 | `test_float_window.py` | [界面行为](ui_behavior.md) §5 |
| 托盘 / 系统通知 | 🔧 | 框架托盘与通知 | — | 同上 |
| 点 × 时询问（完全关闭 / 收托盘 / 记住） | ✅ | `src/patches/close_behavior_patch.py` | 同上 | 同上 §1 |
| 任务栏进度条 | ✅ | `src/core/taskbar.py`（ctypes 调 `ITaskbarList3`，可降级） | 同上 | 同上 §3 |
| 自定义全局快捷键 | ✅ | `src/core/hotkeys.py` + `ui_extras_patch` | 同上 | 同上 §4 |
| 提醒（04:00 重置 / 体力循环） | ✅ | `src/core/reminders.py` + 提醒线程 | 同上 | 同上 §2 |

## 八、扩展

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| UID 遮挡（≤8 块，纯色/马赛克） | ✅ | `src/core/privacy.py` + 「隐私遮挡」页 | `test_privacy_and_diagnostics.py` | [隐私](privacy_and_support.md) §1 |
| 1920×1080 参考坐标（分辨率无关） | ✅ | 同上 | 同上 | 同上 §1 |
| 仅前景时遮挡 | ✅ | 同上（`only_foreground`） | 同上 | 同上 §1 |
| 坐标拾取（客户区 ↔ 参考坐标）+ 取色 | ✅ | `src/core/coord_picker.py` | `test_privacy_and_diagnostics.py` | 同上 §2 |
| 全屏十字线拾取覆盖层 | ⚠️ | 已做「1 秒延时取点」替代（换算与取色完整）；全屏覆盖层涉及句柄/多屏/DPI，需真机验证 | — | 同上 §2 |

## 九、工具与诊断

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| 语言包（应用/还原，带备份，路径白名单 + 拒绝链接/超限/非法类型） | ✅ | `src/core/lang_pack.py` | `test_privacy_and_diagnostics.py` | 同上 §4 |
| 诊断包（配置/日志/模板/失败帧/系统信息，**打包前脱敏 token/webhook/账号**） | ✅ | `src/core/diagnostics_bundle.py` | `test_privacy_and_diagnostics.py` | 同上 §4 |
| 崩溃诊断（钩子 + 自动附诊断包，启动时安装） | ✅ | `src/core/crash_guard.py` + `src/patches/crash_guard_patch.py` | `test_privacy_and_diagnostics.py` | 同上 §5 |
| 在线仓库（索引 + 一键安装，同名不覆盖） | ✅ | `src/core/repo_index.py` + 「分享仓库」页 | `test_repo_index.py` | 同上 §6 |
| 单实例（防多开） | 🔧 | 框架 `ok.util.process.check_mutex`（cwd 命名互斥量，等待 5 秒后按记录的 PID 收尾，失败则拒绝启动） | — | 同上 §8 |

## 十、工程与质量

| 需求 | 状态 | 代码 | 测试 | 文档 |
|------|------|------|------|------|
| 安装自检（流程/队列/方案/路线/配置/i18n） | ✅ | `src/core/health_check.py` + `tools/health_check.py` | `test_health_check.py` | [诊断](diagnostics_and_policy.md) §9 |
| GUI 页离屏构建冒烟（9 个自定义页全部 build） | ✅ | `tests/test_gui_tabs.py` | 自身 | [开发指南](development.md) |
| 静态引用体检（动作白名单 / FeatureList / 配置项重名 / 图标 / i18n / 任务名） | ✅ | `tests/test_imports.py` | 自身 | [开发指南](development.md) |
| 补丁安装体检（16 个启动补丁装得上、幂等、不漏接） | ✅ | `tests/test_patch_install.py`（子进程隔离） | 自身 | 同上 |
| 文档守卫（链接 / 锚点 / 点名的测试文件 / 导航覆盖） | ✅ | `tests/test_docs_links.py` | 自身 | 同上 |
| 仓库卫生（测试不许往仓库里写文件；CI `git status` 门禁） | ✅ | `tests/test_repo_hygiene.py` + `pr-test.yml` | 自身 | 同上 |
| 离线回归 | ✅ | `tests/`（**1200+ 项**，不需要游戏） | 自身 | [开发指南](development.md) |
| CI 测试门禁 | ✅ | `.github/workflows/pr-test.yml` | — | [界面行为](ui_behavior.md) §6 |
| CI 只对改动文件跑 ruff | ✅ | 同上 | — | 同上 §6 |
| i18n 校验（6 语种 × 449 条、无空 msgstr；CI 也跑） | ✅ | `scripts/validate_all.py` + `tools/task_i18n_helper.py` + `tests/test_imports.py`（扫 `tr(` 与 `_tr(` 字面量、动态状态词） | 同上 | 同上 §6 |

---

## 十一、复查记录（验收后逐批回查，第 12–27 轮）

功能清单完成之后，按 A→E 逐批回查「**文档说做了、代码是否真的做了**」。
下表每一条都是**实际发现**，不是走过场；最后一列是验证它的测试。

| # | 发现 | 影响 | 修复 |
|---|------|------|------|
| 1 | `重试间隔(秒)` 没有任何消费点 | 改了不起作用 | `7f287d2` + `d74e12c`（`wait_until` 真的等待） |
| 2 | `wait_until` 绕开全局「单步超时」 | 超时策略管不到它 | `fab1866` |
| 3 | 队列 `total_budget_minutes` 从来不被读取 | 总预算形同虚设 | `13127d8` |
| 4 | 素材热更新后刷新模板缓存没加锁 | 任务运行中会漏检 | `20b95b8`（`feature_set.lock` + 运行中告警） |
| 5 | **失败帧存的是未遮挡原图** | UID 长期落盘、还会进诊断包 | `f1f7da0`（记录带 `masked` 标记） |
| 6 | **诊断包明文带 bot token / webhook** | 发群即泄漏发送权限 | `0756b5a`（`src/core/redaction.py` + manifest 记录脱敏统计） |
| 7 | 语言包可借符号链接读任意文件、无体积上限、任意类型 | 隐私泄漏 / 磁盘被塞满 | `9e5f665` |
| 8 | 流程预算只在步骤之间检查、超预算被当成步骤失败 | 长等待能撑爆预算；`continue_on_error` 下刷屏 | `8a4d6d4`（`FlowBudgetExceeded`） |
| 9 | **长按（蓄力）被转成「点击 + 等待」** | 录制回放语义完全不对 | `1ef0f2b`（新增 `click.hold`，走 `mouse_down/up`） |
| 10 | 录制器拿不到窗口尺寸时给原始像素坐标 | 流程校验报「比例越界」，用户看不懂 | `1ef0f2b`（跳过并计数 `bad_coordinates`） |
| 11 | 配置档保存先删旧快照、应用失败无回滚、档内可写 `../..` | 丢配置 / 写出 `configs/` 外 | `40a166d`（暂存目录 + 回滚 + 路径校验） |
| 12 | 概率表 `150%`、非单调、缺硬保底都照收 | 算出一堆「看起来很正常」的错结论 | `8ce2dc6` |
| 13 | 养成份数 0 静默当 1；负时长污染平均用时 | 结果不可信，且影响任务栏/HUD 预计 | `8ce2dc6` |
| 14 | **断点只记序号，不看内容** | 改流程/路线后续跑会跳过错误的步骤 | `ce667a4`（结构摘要 fingerprint，兼容旧断点） |
| 15 | 提醒/任务栏线程出错静默死掉 | 「提醒没反应」无法排查 | `f44354c`（单轮函数 + 限流日志） |
| 16 | 全模板复测的参数是否正确 | ——（**验证结论：正确**，贴入模板得 +1.000 排第一） | `e3807da`（真素材端到端测试） |
| 17 | **「另存为」手输文件名不补扩展名** | 导出成 `材料单`（无后缀），双击打不开 | `6551e12`（`ensure_suffix`，四处导出都补） |
| 18 | **测试往仓库里写文件**（养成导入的备份默认落仓库） | 8 个 `growth_*.json` 被 `git add -A` 提交 | `6551e12`（备份跟目标走）+ `c2c7c01`（静态检查 + CI `git status` 门禁） |
| 19 | **OCR 语言表只有简中**（`home.json:little_bobo`） | 日/英客户端回退到中文原文，识别不到且无日志 | `d7614e1`（`test_lang_json.py` + 显式 `KNOWN_GAPS` 登记） |
| 20 | `scripts/lang_fill_missing.py` 遇到说明键就崩 | 文档推荐的维护命令不可用 | `d7614e1`（跳过非字典节点 + 5 个用例） |
| 21 | `src/data/lang` 读取后不关文件句柄 | Windows 上语言表文件被占用 | `d7614e1`（`with` + `-W error::ResourceWarning` 验证） |
| 22 | 16 个启动补丁基本没有测试 | 补丁名改了/漏接 = 功能静默失效 | `afd2431`（子进程装两遍 + 防漏接 + 正控）；**结论：16 个全部正常且幂等** |
| 23 | **DPI 取整策略设得太晚**（框架在 `QApplication()` 之后设） | Qt 忽略 → 150% 缩放下文字偏糊 | `d0fc317`（入口最前面 `apply_high_dpi_policy()`，用 Qt getter 验证生效） |
| 24 | **一条龙汇总把失败任务拆成单字**（旧格式是字符串） | 报告里写「每, 日, 委, 托」 | `ce6b9d3` |
| 25 | **汇总的 19 条文案从未翻译**（用局部变量 `_tr`，静态检查扫不到） | 英文/日文用户看到整段中文 | `ce6b9d3`（补 19 条 × 6 语种 + 检查 `_tr(` 与动态状态词） |

**还验证过、确认无误的部分**（没有改动，只补了护栏）：路线校验（空航点/越界/未知界面/超时为 0）、
仓库条目名不能跳出目录、损坏 JSON 的配置一律回退默认值、`notification.emit` 的参数与框架一致、
热键/HUD/任务栏/提醒都有真实调用点、16 个启动补丁全部能装且幂等、
headless 启动整条链路（配置 → 补丁 → 设备 → 模板集 → 主窗口）无异常。

---

## 还差什么

| 缺口 | 原因 | 需要你做什么 |
|------|------|--------------|
| 危机讨伐 / 剧情委托 | 已给骨架流程，但入口/开始/结算三个按钮的**模板还没标** | 标这三个模板（名字照抄流程里的 `crisis_hunt_*` / `story_commission_*`），或给截图我来标 |
| 界面透明度 / 背景图 | ok-script 2.0.6 没有任何透明度 API | —（只能重写窗口合成，不建议） |
| 全屏十字线拾取 | 需要在真机上验证多屏/DPI/点击穿透 | 有需要时我再做，逻辑已就绪 |
| 养成真实数值 | 内置是示例数据 | 从 promilia.wiki 导出后用「导入数据」导入 |
