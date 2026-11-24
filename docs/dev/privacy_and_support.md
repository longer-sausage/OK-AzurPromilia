# 隐私遮挡、坐标拾取与支持工具（批次 E-1）

## 1. UID 遮挡

`src/core/privacy.py` +「隐私遮挡」页。

- 最多 **8 块**，每块可以是**纯色**（`#RRGGBB`，默认黑）或**马赛克**（缩小再放大，真的糊掉）；
- 坐标一律存 **1920×1080 参考空间**，绘制时按当前画面等比换算 —— 换分辨率不用重调；
- 配置存 `configs/privacy.json`，默认一块「UID」盖住左下角（与原 `make_bottom_left_black` 同位置）；
- **作用于落盘截图**：`src/config.py` 的 `screenshot_processor` 现在走
  `make_privacy_processor()`，日志截图与诊断包里的图都被遮住；
  识别用的原始帧不受影响（否则会影响识别精度）。
- **失败帧也遮**：失败帧库（`failure_frames/`）会长期留在磁盘上，落盘前同样过一遍
  遮挡处理器；帧记录里带 `masked` 标记，复测页会标「已遮挡」——
  **挡住区域内的模板复测必然不命中**，这是有意的取舍（隐私优先）。
  遮罩出问题时会退回未遮挡并记一条警告：失败帧宁可有隐私风险，也不能因为遮罩丢失现场。

> 「只在游戏前台显示」这条是**屏幕显示**的要求，遮挡发生在落盘路径上，
> 因此无条件生效 —— 截图不会因为切窗口而漏遮。配置里的 `only_foreground`
> 留给覆盖层实现使用。

页面（`src/gui/privacy_tab.py`）：左边块列表、中间 16:9 预览（按比例画出遮挡框、
选中项高亮）、右边参数编辑（X/Y/宽/高/颜色/模式/标签）、底部增删改/保存/恢复默认。

## 2. 坐标拾取

`src/core/coord_picker.py`：

| 函数 | 作用 |
|------|------|
| `client_to_reference(x, y, w, h)` | 客户区像素 → 1920×1080 参考坐标 |
| `reference_to_client(rx, ry, w, h)` | 反向换算 |
| `format_snippet(rx, ry)` | 复制片段 `"x": 960, "y": 540` |
| `color_hex(frame, x, y)` | 该点颜色 `#RRGGBB` |
| `pick(frame, x, y)` | 一次拾取的完整结果（客户区 + 参考 + 颜色 + 片段） |

「隐私遮挡」页的「坐标拾取」采用**延时取点**：点按钮后 1 秒取当前鼠标位置与屏幕颜色，
换算成参考坐标填进当前块。

> 需求里写的是「全屏十字线」。做成覆盖层需要窗口句柄、多屏坐标换算与 DPI 处理，
> 而且只能在真机上验证；先把**换算逻辑和可靠的取点**做扎实（都有测试），
> 覆盖层留作后续增强，接口已经准备好了。

## 3. 语言包

`src/core/lang_pack.py` +「工具与诊断 → 语言包」。

```text
语言包EN/
├── i18n/en_US/LC_MESSAGES/ok.po
└── assets/lang/home.json
```

- 扫描程序目录下的 `语言包<代码>/` 文件夹；
- **只允许写 `i18n/` 与 `assets/lang/`**，出现其它路径直接拒绝（不写任何文件）；
- **只接受 `.po` / `.mo` / `.json`**（别的一律拒绝，语言包不该带可执行文件）；
- **拒绝符号链接 / 目录联接**：包里的链接能指向 `C:\Windows\…`，把系统文件复制进 `i18n/`
  再被别人打包带走；链接检查在扫描与写入两处都做；
- **体积上限**：单文件 8MB、总计 64MB（防压缩炸弹 / 防「一个 10GB 的 po」把解析卡死），
  超限直接拒绝并说明是哪个文件；
- 应用前把当前这两个目录备份到 `backups/langpack_<时间戳>/`；
- 「还原最近备份」会先给当前状态再做一份备份，所以来回切也不会丢东西。

## 4. 诊断包

`src/core/diagnostics_bundle.py` +「工具与诊断 → 诊断包」。

| 文件 | 内容 |
|------|------|
| `manifest.json` | 每个文件的大小 + SHA-256，以及**脱敏统计** |
| `system.json` | 平台、Python、版本、游戏 exe、支持分辨率、时间 |
| `configs/*.json` | 运行配置（不含 profiles 大快照）——**已脱敏** |
| `logs/ok-script*.log` | 日志尾部（默认 3 个文件、每个最多 2MB）——**已脱敏** |
| `templates.json` | 模板清单（coco categories） |
| `runs.json` | 运行报表统计（含每任务成功率/平均用时） |
| `ledger.json` | 记账汇总（只放合计与前 50 个物品，不放明细） |
| `failure_frames/…` | 索引 + 最新一帧（帧本身已过 UID 遮挡） |

隐私上的默认取舍：**截图默认不打包**（页面有「包含截图」开关），
记账只给合计、不给逐条明细；失败帧因为已经过了遮挡处理器，可以带。

### 打包前脱敏（重要）

诊断包是要发出去给别人看的（群、issue）。框架的 `Notification` 配置里有
**Discord Webhook、Telegram Bot Token、QQ Bot API Token**，而日志在 DEBUG 级别还会把
整份配置打出来（`Config:init self.config = {...}`）——原样打包等于把「往你的频道发消息」
的权限一起送出去。

`src/core/redaction.py` 在打包时抹掉这些值：

- 键名命中敏感词（`token`/`webhook`/`password`/`secret`/`chat id`/`nickname`/`账号`/`密码`…）
  → 值替换成 `***`，**键名保留**（收包的人仍能看出「配没配」）；
- 列表里的账号逐项替换、**长度保留**（「3 个账号」这个信息对排查有用）；
- 日志里 `'Discord Webhook': 'https://…'` 与 `webhook = https://…` 两种写法都抹；
- manifest 里记 `redacted: {count, keys, log_lines}`，收包的人知道哪些值不可信；
- 配置文件本身不是合法 JSON 时**原样复制**（坏文件本身也是线索）。

## 5. 崩溃诊断

`src/core/crash_guard.py`：

- `sys.excepthook`：主线程未捕获异常；
- `threading.excepthook`：后台线程异常（框架后台线程不少）；
- `faulthandler`：解释器级崩溃写到 `logs/crash_faulthandler.log`。

每次记录写 `crashes/crash_<时间戳>_<pid>.txt`（时间/线程/pid/Python 版本/cwd + 完整堆栈），
并自动在同目录打一份诊断包（`make_bundle=True`，里面带崩溃类型与消息）。

- **任何异常都不外抛**（记录失败就返回 `CrashRecord(error=...)`）；
- UI 线程的异常**只提示不退出**：`notify` 回调负责提示，是否退出仍由框架决定；
- `close()` 会卸掉钩子并关掉 faulthandler 句柄（测试与退出清理用）。

### 什么时候装上

`src/patches/crash_guard_patch.py`，在 `install_startup_patches()` **第一个**执行：

- 早挂早生效 —— 后面任何一个补丁初始化时炸了也会留下记录（否则「崩溃诊断」只是实现和测试，
  真崩了什么都不会写）；
- 安装失败只记日志，不影响启动；
- `OK_AP_DISABLE_CRASH_GUARD=1` 可以关掉（跑测试或排查钩子冲突时用）；
- `installed_guard()` 能拿到已装上的实例，调试用。

接线本身也有测试（`tests/test_crash_guard_patch.py`）：装上后真的会写
`crashes/crash_*.txt`、重复调用幂等、环境变量能关、安装失败不抛，以及
「崩溃诊断必须是 `install_startup_patches()` 里的第一个调用」。

## 6. 在线仓库（分享与一键安装）

`src/core/repo_index.py`。给一个返回 JSON 的 http(s) 索引地址（也可以是本地文件，
方便离线），就能浏览并安装别人分享的流程 / 队列 / 轮椅方案 / 模板包：

```json
{
  "name": "社区分享站",
  "entries": [
    {"kind": "flow", "name": "深渊速刷", "url": "https://…/abyss.json",
     "sha256": "…（可选，给了就校验）", "tags": ["刷图"]},
    {"kind": "template", "name": "2K 素材包", "url": "https://…/2k.zip"}
  ]
}
```

| kind | 装到 |
|------|------|
| `flow` | `configs/flows/<名字>.json` |
| `queue` | `configs/queues/<名字>.json` |
| `scheme` | `configs/schemes/<名字>.json` |
| `template` | 交给 `asset_pack.install_pack()`（逐文件 SHA-256 + 原子安装 + 回滚） |

规则：

- **同名不覆盖**（`allow_overwrite=False` 时直接拒绝），避免别人的流程盖掉你的；
- 给了 `sha256` 就校验，不匹配不落盘；
- 下载失败、内容不是合法 JSON、本地文件不存在，都返回 `InstallOutcome(ok=False)` 而不是抛异常。

页面在**「分享仓库」**：填地址 → 加载 → 列表（类型/名称/说明/标签，双击也能装）→ 安装选中，
可勾选「允许覆盖同名文件」；失败弹红框并把原因写回状态行。

## 7. 任务栏进度估算

`RunLog.progress_ratio(task, elapsed, cap=0.97, fallback_seconds=None)`：
按历史平均用时算比例，**封顶 0.97**（估到 100% 还在跑会误导人）。
没有历史数据时用 `fallback_seconds`，两者都没有返回 0。
真正结束由调用方自己置 1.0 或隐藏进度条。

## 8. 单实例（防多开）

由框架提供：`ok.util.process.check_mutex()`（`ok/__init__.py` 启动时调用，`config['check_mutex']` 可关）。

- 互斥量名 = 当前工作目录的 MD5，所以**同一个安装目录只能开一个**（不同目录各开一个）；
- 第二个实例会等 5 秒；期间前一个退出就正常接管；
- 5 秒后仍占着，就按 `%TEMP%/ok-script-<hash>.pid` 里记录的 PID 收尾；
- 收尾也失败则抛 `RuntimeError('Another application instance is still running and could not be stopped safely.')`
  拒绝启动，而不是让两个实例同时操作游戏。

本项目**没有**另写一套单实例逻辑（重复实现只会和框架打架），只在这里说明行为。

## 9. 测试覆盖

| 测试 | 覆盖 |
|------|------|
| `tests/test_privacy_and_diagnostics.py` | 颜色解析/格式化、块换算与夹取、8 块上限、纯色与马赛克绘制、配置读写与损坏容错、`screenshot_processor` 真的画上去；坐标换算与取色；语言包扫描/白名单拒绝/**非法后缀/单文件超限/总体积超限**/备份还原；诊断包内容与 manifest、截图开关、空目录也能打包；崩溃守卫安装幂等、堆栈落盘、诊断包、线程钩子、`close()` |
| `tests/test_config.py` | 新增 Tab（隐私遮挡）已注册且可导入 |
| `tests/test_failure_frame_store.py` | 落盘/索引/读取/修剪/清空/损坏容错/同名毫秒去重；**落盘前遮挡（不改原帧、其余区域不动、masked 标记过索引）、无处理器不变、处理器报错或返回空都退回未遮挡且不丢帧** |
| `tests/test_repo_index.py` | 索引解析/筛选、四类条目安装、同名不覆盖、覆盖开关、SHA-256 拦截、坏 JSON、模板包走 asset_pack；**恶意条目名（`../..`、`a/b`、空名）不能跳出配置目录且优雅失败**；进度比例（无历史/兜底/封顶/负值） |
| `tests/test_redaction.py` | 敏感键识别（token/webhook/password/chat id/账号/`-`/`_` 变体）、嵌套与列表脱敏（长度保留、不改原对象）、日志两种写法、无关行不动、**打包端到端：配置与日志里的 token 在 zip 里确实没了、manifest 记下脱敏统计、坏 JSON 原样复制** |

```bash
uv run python -m unittest discover -s tests
```
