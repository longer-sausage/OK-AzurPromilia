# 界面行为：左侧导航、关闭询问、提醒、任务栏进度、快捷键（批次 E-2）

「界面与提醒」页集中管理这四件事，设置写到 `configs/` 下的 JSON，改完立即生效
（提示线程每轮都会重新读配置；快捷键例外，见下）。

## 0. 左侧导航布局

侧栏本来是三个区（qfluentwidgets）：`topLayout`（本项目 9 个自定义页，**不滚动**）、
`scrollArea`（框架的 Capture / Triggers / Tasks / Templates / Schedule，**带滚动条**）、
`bottomLayout`（Debug / Run Code / Notification / Settings / About，固定）。

于是看着像「上面 9 项不动 + 下面另起一段带滚动条」，滚动条出现在列表中间。

`src/patches/nav_layout_patch.py` 的做法是**只改注册位置、不动控件父窗口**：

1. 包一层 `FluentWindow.addSubInterface`：`src.gui.*` 的页面一律以
   `NavigationItemPosition.SCROLL` 注册 → 和框架的项进同一个滚动区；
2. 窗口建好后在**同一个布局内**把本项目的项排到前面（`takeAt` + `insertWidget`），
   视觉顺序保持「自定义页 → Capture / Triggers / Tasks…」；
3. 底部 5 项本来就是 `BOTTOM`，一行代码都不碰。

结果：**整列一个滚动条**；底部 5 项（开发工具 / 运行代码 / 通知 / 设置 / 关于）固定不滚。

> ⚠️ 第一版补丁是「把 `topLayout` 里的控件搬到 `scrollLayout`」，结果滚动区被撑到标题栏
> 下面、和窗口标题文字重叠（用户截图确认）。控件跨布局搬移后父窗口/几何没有重算，
> 所以现在改成上面这种「只改注册位置 + 同布局内排序」的做法。
> `tests/test_nav_layout_patch.py` 锁住三条：本项目页面被改成 SCROLL、框架/底部页面保持原位置、
> 同布局排序后顺序为「本项目 → 框架项」。
>
> 如果鼠标悬停时的选中指示条位置看起来偏了，告诉我——指示器位置是框架算的，
> 我在这台机器上没有字体、看不到真实窗口，只能靠你确认。

## 1. 关闭询问

`src/core/close_behavior.py` + `src/patches/close_behavior_patch.py`。

框架只有一个「关闭时最小化到托盘」开关，没有「问一下」的余地。补丁把
`MainWindow.closeEvent` 换成三选一：

| 选择 | 行为 |
|------|------|
| 完全关闭 | 调用**原来的** `closeEvent`（看门狗、清理、托盘图标回收都不变） |
| 收到托盘 | `event.ignore()` + `hide()` |
| 取消 | `event.ignore()` |

- 可勾选「记住我的选择」，只记 `close` / `tray`（取消不该被记住）；
- 退出流程中（`exit_event` 已置位）**直接走框架逻辑**，不会弹窗卡住退出；
- 任何异常都退回框架逻辑 —— 补丁坏掉最多是「没弹窗」，不会关不掉。

配置：`configs/close_behavior.json`（`{"ask": true, "remember": ""}`）。
页面上三种模式：每次询问 / 直接关闭 / 最小化到托盘。

## 2. 提醒

`src/core/reminders.py` + `src/patches/ui_extras_patch.py` 里的提醒线程（30 秒轮询）。

- **每日重置**：过了 `daily_reset_hour`（默认 4 点）当天的第一次轮询提醒一次，
  同一天不重复；跨天自动重置；
- **体力提醒**：每 `stamina_interval_minutes` 分钟提醒一次（0 = 关闭）；
- 只发**右下角通知**，不打断任务；调度器是纯函数 + 注入时钟，测试不用真等 04:00。

配置：`configs/reminders.json`。

## 3. 任务栏进度

`src/core/taskbar.py` + 进度线程（0.5 秒轮询 `og.executor.current_task`）。

- 比例 = 已用时间 / 该任务的**历史平均用时**（`RunLog.estimate_seconds`），
  **封顶 0.97** —— 估到 100% 还在跑会误导人，结束时直接撤掉进度条；
- 没有历史数据就不显示（返回 `None`），不编一个假进度；
- 变化小于 1% 不推，避免每秒刷爆系统调用；
- 后端用 ctypes 直接调 `ITaskbarList3`（不依赖 pywin32）；**拿不到窗口句柄或 COM
  失败就安静降级成空实现**，绝不影响任务执行。

⚠️ **COM 必须按线程来**：`ITaskbarList3` 是单线程套间（STA）对象，在 A 线程
`CoCreateInstance` 出来、跑到 B 线程调用会被拒（或被 Qt 的套间搅成未定义行为）。
所以后端是在**进度线程内部**创建的（`CoInitialize` 也在那里），线程退出时再
`CoUninitialize`。测试里有一条专门盯这个：断言后端创建的线程 id 不是主线程。

配置：`configs/ui_extras.json` 的 `taskbar_progress`。

### 后台线程不会因为一次出错就永久失效

两个线程（提醒 / 任务栏）每轮都跑 `reminder_tick` / `taskbar_tick`，这两个函数**自己吞异常**：

- 一轮出错（配置读不了、COM 调不通、日志文件被占用）只影响这一轮，下一轮照常；
- 第一次出错与之后每 60 次会**写一条日志**（`后台线程出错（第 N 次）：…`），
  重复的同类错误不刷屏 —— 否则用户报「提醒没反应」时日志里什么都没有，没法排查；
- 线程启动阶段（创建后端 / 首次读配置）也包了 try：失败写日志后线程退出，不会静默死掉。

## 4. 全局快捷键

`src/core/hotkeys.py` + 快捷键线程（`RegisterHotKey` + `PeekMessageW` 消息循环）。

| 动作 | 默认键 | 说明 |
|------|--------|------|
| `pause` | `ctrl+alt+p` | 暂停 / 继续（`executor.pause()` / `executor.start()`） |
| `stop` | `ctrl+alt+s` | 停止当前任务（`executor.stop_current_task()`） |
| `toggle_uid_mask` | `ctrl+alt+u` | 开关 UID 遮挡（改 `configs/privacy.json` 的 `enabled`） |

- 为什么不是 F9/F10：**F9–F12 已经被框架的启动/停止占用**，再绑会冲突；
  校验函数会直接报「被框架的启动/停止占用」；
- 纯字母键会被拒绝（会和游戏输入打架），必须带修饰键；
- 校验能提前发现：写法错误、重复绑定、占用框架键；
- 改完**需要重启**才重新注册（消息循环里注册一次）。

配置：`configs/hotkeys.json`。

## 5. 悬浮窗（取代旧的 HUD 悬浮条）

`src/core/float_window.py`（设置与文案，纯逻辑）+ `src/gui/float_window.py`（窗口）+
`src/gui/float_window_tab.py`（「悬浮窗」设置页）+ `ui_extras_patch` 里挂载。

两种形态，「缩放」即在两者间切换：

| 形态 | 外观 | 交互 |
|------|------|------|
| **图标态**（默认） | 圆角化的软件图标（`icons/icon.png`），大小 32~160 px 可调 | 拖到屏幕任意位置；**点一下**展开成面板 |
| **面板态** | 圆角矩形，显示当前任务、预计剩余、队列每项「约 N 分钟」 | 拖动移动；拖**右下角**改大小；双击或点「收起」回图标态 |

- 颜色 `#rrggbb`、不透明度 **10%~100%**（`MIN_OPACITY = 0.10`，低于 10% 等于看不见）、
  圆角 0~30 px 都在「悬浮窗」页设置，保存后**立刻**作用到已打开的窗口；
- **默认位置在屏幕右下角**（`default_position`），并且离底边额外留 **72px**（`NOTIFICATION_CLEARANCE`）：
  Windows 的通知弹窗是从任务栏上方升起的，正好盖住右下角那块，留出这截高度就不会被压住
  （悬浮窗本身是置顶窗口，通常也在通知之上，但两者同时出现时仍然清爽）。
  位置按**可用区域**（`availableGeometry`）算，任务栏在左边/上边时原点不是 0,0 也能正确贴角；
- 位置、大小、形态、图标大小全部写回 `configs/float_window.json`，重启后原地复原；
  位置有夹取（`clamp_position`），换分辨率也不会跑到屏幕外；「恢复默认位置与大小」会把
  坐标清空，下次布局重新回到右下角；
- 队列 ETA 来自 `src/core/run_state.py`：`QueueRunner` 开始/每项切换/结束时更新，
  悬浮窗每 `poll_ms`（默认 500ms）读一次快照；单项用时优先取该任务的历史均值
  （`RunLog.estimate_seconds`），跑完一项后用实测值替换，越跑越准；
- 窗口标志 `FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowDoesNotAcceptFocus` +
  `WA_ShowWithoutActivating` —— **不抢游戏焦点、不打断输入**；
- 退出时通过 `app.aboutToQuit` 一起关掉；开关在「悬浮窗」页（`configs/float_window.json` 的 `enabled`）。

旧的 HUD 悬浮条（`src/gui/hud_window.py`）已删除，「界面与提醒」页上的
「显示 HUD 悬浮条」勾选框一并移除——两个悬浮 UI 会互相打架。`src/core/hud.py` 里的
`format_remaining` / `build_hud_text` 仍在（纯文本逻辑，被测试复用）。

剩余时间文案：`58 秒` / `12 分 30 秒` / `1 小时 1 分`，数据缺失显示「未知」；
悬浮窗里的 ETA 用 `约 45 秒` / `约 3 分钟` / `约 1 小时 5 分`（`format_eta`）。

## 6. 工程质量

CI（`.github/workflows/pr-test.yml`）在 PR 上跑五道门禁：

| 门禁 | 跑什么 | 挡住什么 |
|------|--------|----------|
| 依赖可复现 | `gen_release_requirements.py --check` | `requirements.txt` 与 `uv.lock` 不同步导致打包失败 |
| **只对改动文件跑 ruff** | `git diff base...HEAD -- "*.py"` | 基线里有历史告警，全量检查会挡住所有 PR；新代码必须干净，确需保留用 `# noqa: 规则名` |
| 全量离线测试 | `unittest discover -s tests` | 逻辑回归（1200+ 项，不需要游戏） |
| **i18n 校验 + 安装自检** | `scripts/validate_all.py`、`tools/health_check.py --strict` | 加了 UI 文案没翻译（6 语种有空 msgstr）、示例流程/队列装上打不开 |
| **测试后工作区必须干净** | `git status --porcelain` 非空即失败 | 测试往仓库里写文件（真实事故：养成导入的备份默认落仓库，8 个 `growth_*.json` 被 `git add -A` 提交） |

`pyproject.toml` 里把 `RUF003`（注释里的 `×`、`—` 等中文标点）加入 ignore：
本项目注释大量引用游戏内文案，这类字符是故意的。顺手把碰过的文件里的历史告警清零
（`base_game_task.py` / `daily_task_runner.py` / `daily_task.py` / 若干测试）。

**文档也有测试**（`tests/test_docs_links.py`）：相对链接必须指向存在的文件、`#锚点` 必须
能对上标题（按 GitHub 规则生成 slug，保留中文）、文档里用反引号点名的 `tests/*.py` 必须真的
存在、`docs/index.md` 不能漏列文档。改名/搬章节忘了同步，CI 会直接红，不用等人点到 404。

## 7. 明确没做的

- **界面透明度 / 背景图**：ok-script 2.0.6 **没有任何透明度 API**
  （`cfg.blurRadius` 是死配置），要做只能重写窗口合成，风险高、收益低，暂不做。

## 8. 测试覆盖

| 测试 | 覆盖 |
|------|------|
| `tests/test_ui_behaviors.py` | 关闭行为决策与记忆、配置读写容错；提醒设置校验、调度（每日一次/跨天/体力间隔/重置/剩余时间）；快捷键解析（修饰键、F 键、别名、纯字母拒绝、重复/占用检测、注册计划、持久化）；任务栏进度（节流、封顶、清除、无历史不显示、无句柄降级；后端在进度线程内创建、线程幂等、清理不抛）；快捷键动作（暂停切换、停止当前任务、开关遮挡、未知动作、无执行器）；HUD 文案与位置（剩余时间格式化、进度封顶、隐藏剩余、底部居中、多屏偏移、超大窗口夹取、设置读写保留其它键）；**后台线程一轮出错不影响下一轮、同类错误只记一条、关掉任务栏后进度条被撤掉** |

```bash
uv run python -m unittest discover -s tests
```
