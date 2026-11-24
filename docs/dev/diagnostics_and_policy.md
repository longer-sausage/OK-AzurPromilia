# 识别诊断与运行策略（批次 A）

这一层解决三件事：**任务失败后怎么定位问题**、**慢机器怎么放宽节奏**、
**游戏更新换素材怎么不换程序**。对应界面是「工具与诊断」页与「全局配置 → 自动化策略」。

## 1. 自动化策略（全局配置）

配置项定义在 `src/core/runtime_config.py`，注册进 `src/core/global_config_store.py` 的
`GLOBAL_CONFIG_OPTIONS`，界面分组在 `src/gui/global_config_tab.py` 的 `GLOBAL_CONFIG_GROUPS`。
存储位置：`configs/Automation Runtime Config.json`。

| 键 | 默认 | 作用 |
|------|------|------|
| `单步超时(秒)` | 10.0 | `self.loop(time_out=None)` 与 `wait_until(time_out=0)` 的默认超时 |
| `重试间隔(秒)` | 0.0 | 条件没满足时两次重试之间的间隔（与 `wait_until_check_delay` 取较大值） |
| `动作后等待(秒)` | 0.0 | 失败自恢复时每次按返回键后的等待 |
| `卡死检测阈值(秒)` | 30.0 | 画面长时间不变的告警阈值 |
| `低配模式` | False | 所有超时放宽 1.5 倍（含卡死阈值） |
| `失败后自动返回主菜单` | True | 失败后按返回键退回主菜单 |
| `最大返回次数` | 3 | 最多按几次返回键 |
| `失败帧保留数量` | 20 | 失败帧库保留帧数 |

约定：

- **默认值与改造前完全一致**（单步超时 10 秒、其余 0 秒），存量用户升级后时序不变；
- 取值做了类型兜底，用户手改 JSON 成字符串也不会让任务崩；
- `低配模式` **只放宽时间**，不改变任何判定阈值 —— 慢机器不该因为慢而换一套判据。

实现细节：

- `BaseGameTask.runtime` 是**每次读取**的属性（底层是内存里的 dict），改完配置下个任务即生效，不用重启；
- `self.loop(time_out=None)` 与 `wait_until(time_out=0)` 走全局策略；**显式传具体秒数的位置不受
  「单步超时」影响**，想精确控制就照旧传数字（但「低配模式」会放大所有超时，包括显式值）；
- `wait_until(time_out=0)` 的默认值优先用框架的 `wait_scene_timeout`，只有**用户改过**
  「单步超时」才用配置值——这样存量行为不变，改了配置又真的生效；
- 非 `BaseGameTask` 的对象（测试里的 MockTask）走 `_runtime_of()` 退回默认快照。

### 顺手修掉的两个死键

`src/config.py` 里的 `wait_until_before_delay` / `wait_until_check_delay` 在 ok-script 2.0.6
**没有任何代码引用**（死键）。`BaseGameTask.wait_until` 现在自己实现它们：
首次检测前等待、每轮检测之间等待，默认 0 与旧行为一致。

> ⚠️ 这一节曾经有个坑：`重试间隔(秒)` 与 `动作后等待(秒)` 在**改造当时其实没有消费者**
> —— 只有 dataclass 字段被读、没有任何调用点用它，等于用户改了没反应。现在：
> `重试间隔` 接进了 `wait_until` 的重试间隔（`RuntimeSettings.resolve_retry_gap`，
> 与死键 `wait_until_check_delay` 取较大值），`动作后等待` 接进了失败自恢复的按返回键间隔。
> 两个默认都是 0，所以存量行为不变。

> `wait_until_timeout` 仍未接进 `TaskExecutor`：`executor.wait_scene_timeout` 依旧是写死的 10 秒。
> 需要改单个等待的超时，请继续用 `self.loop(time_out=...)` / `wait_until(time_out=...)` 显式传值。

## 2. 卡死检测

`BaseGameTask` 把 `sleep_check_interval` 设为 `STUCK_CHECK_INTERVAL`（2 秒），
框架会在 `executor.sleep` 期间周期调用 `sleep_check()`，项目侧复用这个插槽做画面指纹对比：

```text
sleep_check → _check_stuck → perceptual_hash(self.frame)
  指纹变化 = 场景在动，重置基线
  指纹不变且持续 ≥ 卡死检测阈值 → 记一次警告（每个连续不变段只报一次）
```

- **只警告，不中止任务**：卡住的原因可能是加载、断线重连或挂机，
  该中止任务的是任务自己的超时，不是这个探针。
- 时间基准用 `self.active_time()`，**暂停期间不计入**「画面没变」的时长。
- `low_spec=True` 时阈值同样放大 1.5 倍，避免慢机器误报。
- 切换场景后想立刻重新基线：调 `reset_stuck_detection()`。

## 3. 失败自恢复

`handle_task_exception` 在记录日志后：

1. `capture_failure_frame()` —— 存下这一帧（见第 4 节）；
2. `recover_after_failure(reason)` —— 若 `失败后自动返回主菜单` 开启且异常不是
   `TaskDisabledException`，就 `recover_to_menu()`：

```text
最多 N 次：已在主菜单 → 返回 True
        否则 self.back() + 等待 once_sleep_time
```

- **只退菜单，不重跑任务**；是否重跑由用户或触发任务决定。
- 任何异常（`back()` 失败、识图失败）都在内部吞掉 —— 这是失败路径上的兜底，
  不能让它盖掉原始异常。
- 主界面判定用 `ui_page_appear(page_main)`，与导航体系同一套判据。

## 4. 失败帧库

`src/core/failure_frame_store.py`，目录 `failure_frames/`（根下，已 gitignore）。

**为什么不复用框架的 `screenshots/`：**

1. 框架落盘前会过 `screenshot_processor`（本项目把左下角 UID 涂黑）和 `blur_area`，
   落盘图与识别帧**不是同一张图**，拿去复测等于换了输入；
2. `screenshots/` 有体积/天数清理策略，失败现场会被顺手删掉；
3. 复测需要稳定索引（时间 / 任务 / 消息）。

存储格式：

```text
failure_frames/
├── index.json                       # {"version":1,"frames":[{id,file,created_at,task,message,width,height,channels}]}
├── 20261124_011530_412_DailyTask.png
└── ...
```

- 保留最近 N 帧（`失败帧保留数量`，默认 20），更旧的帧**连同文件一起删**；
- 索引用 `.tmp` + `os.replace` 原子写；索引损坏或旧格式（裸数组）都能容错读；
- 同一毫秒内多次失败会追加序号，`id` 与文件名都唯一；
- `save()` 在失败路径上被调用，**任何异常都不外抛**，只记日志。

写入入口：

- `BaseGameTask.capture_failure_frame(message, task_name=None)` —— 取 `self.frame`（识别原始帧）；
- `mark_task_failure()` / `handle_task_exception()` 已自动调用。

## 5. 全模板复测

`src/core/template_rescorer.py`：把**同一帧**喂给全部模板（当前 54 个），按得分降序返回。

三个必须遵守的细节（否则分数不可比）：

1. **显式传全屏 box**。`find_one_feature` 不传 box 时只在 coco 标注位置 ±variance（约 2px）
   的窗口里搜索，分数只反映「那个小窗口里的最佳位置」；
2. **threshold 压到 -1**。框架里 `threshold == 0` 会被替换成 `default_threshold`（0.8），
   低于阈值的模板直接不返回 —— 而 `TM_CCOEFF_NORMED` 的范围是 [-1, 1]，-1 才能拿到原始 max 值；
3. **按当前分辨率解析模板名**（`resolution_resolver(width)`，规则与
   `RuntimeMixin.get_feature_by_resolution` 一致，`tests/test_template_rescorer.py` 有等价性测试）。

**这套参数是真的能用的**，`tests/test_template_rescorer_e2e.py` 用真实
`FeatureSet` 验证过：把一个真实模板贴进 1920×1080 的暗背景，它拿到 **+1.000** 并排第一，
其余模板最高 0.58（判定「偏弱」）——分数确实有区分度，不是一排无用数字。

⏱️ **耗时**：1080p 全屏 × 54 个模板约 **5 秒**（每个约 100ms，`TM_CCOEFF_NORMED` 全图搜索）。
GUI 里这一步在后台线程跑、带进度，卡住几秒是正常的；嫌慢可以在「复测」时只选单个模板。

判定分档（离阈值还差 `gap = threshold - score`）：

| gap | 判定 | 含义 |
|------|------|------|
| ≤ 0 | 命中 | 模板仍然有效 |
| 0 ~ 0.08 | 差一点 | 通常是图标微调/抗锯齿/压缩差异，可以放宽阈值或重做模板 |
| 0.08 ~ 0.25 | 偏弱 | 模板大致对但环境变了（缩放、配色、滤镜） |
| > 0.25 | 不对 | 模板已经彻底失效（界面改版或换了素材） |

`TemplateRescorer.from_task(task)` 用在任务里（带分辨率映射）；
`from_feature_set(feature_set, width)` 用在 GUI（没有任务实例）。

## 6. 素材包热更新

`src/core/asset_pack.py`。zip 结构：

```text
asset_pack.json          # 清单
assets/images/1.png      # 素材（与仓库内路径一致）
ok_templates/...
```

清单：

```json
{
  "name": "ok-ap 素材包",
  "version": "2026.11.24",
  "created_at": 1764000000.0,
  "files": {"assets/images/1.png": {"sha256": "…", "size": 12345}}
}
```

`files` 也接受 `{"路径": "sha256"}` 简写。

安装流程（`install_pack`）：

```text
读清单 → 逐文件 SHA-256 校验（含路径合法性/前缀白名单/符号链接检查）
   ↓ 不通过 → 直接返回失败，磁盘一个字节都没动
解压到 app 根下的临时目录（同盘，保证后面的 os.replace 是原子的）
   ↓ 逐文件：旧文件先复制到 backups/asset_pack_<时间戳>/ → os.replace 就位
   ↓ 中途任何异常 → 按备份回滚 + 删除新增文件
写 backups/asset_pack_<时间戳>/install.json（清单 + 安装记录）
回调 on_installed → 刷新 FeatureSet 缓存 / OCR 纠错表
```

安全边界：

- 只允许写 `assets/` 与 `ok_templates/` 前缀 —— 素材包不该改代码；
- 拒绝绝对路径、`..`、盘符、zip 内符号链接（zip slip）。

打包侧工具：`build_manifest(root)` / `build_pack(zip, root, …)`（测试也用它造包）。

> ⚠️ **只能替换已有模板名**。`src/data/feature_list.py` 的 `FeatureList` 由
> `assets/coco_annotations.json` 生成，`get_feature_by_resolution` 会校验名字；
> 新增模板必须重新标注并重新生成枚举，改图不改名才走热更新这条路径。

### 刷新缓存为什么必须加锁

`FeatureSet.process_data()` 的实现是**先清空** `feature_dict` / `box_dict`，再从盘上重新读一遍合并。
所以它不是线程安全的，而每次识别都会走的 `ensure_feature()` 是加锁的。GUI 线程在任务运行时
直接调它，就会出现「字典已清空、还没填回来」的窗口，这期间的模板查找全部 miss，
任务会把「素材刚更新」当成「识别失败」。

`src/core/feature_reload.py` 因此把刷新包在 `feature_set.lock` 里（`reload_features()`），
并在**任务正在运行时明确告警**：

```
素材已更新，但当前有任务在运行：这一次识别可能因模板缓存重建而漏检，请留意日志
```

> 仍然做不到「绝对无感」：框架没有提供原子替换 `FeatureSet` 的接口。真要彻底解决，
> 得把素材安装推迟到任务间隙。这里选「马上生效 + 明确告警」——用户装完素材最自然的
> 期待就是立刻看到效果；刷新失败时页面会提示「重启程序后生效」，而不是假装成功。

## 7. 模板自检

`src/core/template_audit.py` +「工具与诊断 → 模板自检」。

需求里的原话是「先检查选区有没有纹理（纯色块会被拒绝）」——纯色块永远匹配不上
（`TM_CCOEFF_NORMED` 对常量图会给出无意义的高分或 NaN），做成模板只会让人误以为识别坏了。

三个便宜但有效的判据（默认值按 1080P 下 20~40px 小图标实测）：

| 指标 | 纯色块表现 | 阈值 |
|------|-----------|------|
| `stddev`（灰度标准差） | ≈ 0 | ≥ 6.0 |
| `unique_colors`（去重颜色数） | 1~2 | ≥ 8 |
| `edge_density`（Canny 边缘占比） | ≈ 0 | ≥ 0.01 |

- 三项全不达标 → `flat`（拒绝）；只有一项不达标 → `weak`（建议换区域）；全达标 → `ok`；
- 每个模板算 **SHA-256**，相同哈希归成一组「重复模板」——重复模板会让
  「全模板复测」的分数再也解释不清；
- `audit_directory()` 自动找 `coco_annotations.json`，逐条 bbox 从拼图源图裁切后分析；
- 只读，不改任何文件；当前仓库 54 个模板自检结果：**全部可用、无重复**。

## 8. 「工具与诊断」页

`src/gui/diagnostics_tab.py`，注册在 `src/config.py` 的 `custom_tabs`。懒加载
（首次显示才构建控件），复测在 `QThread` 里跑，不阻塞 UI。

- **失败帧复测**：下拉选帧 → 左侧预览 → 「复测全部模板」→ 右侧表格按得分排序
  （模板 / 得分 / 阈值 / 判定 / 位置，未命中按差距染成橙/红），底部一句话摘要；
  另有「刷新 / 打开目录 / 清空」。
- **模板自检**：「开始自检」→ 列出纹理最弱的 12 个模板与重复分组（见上一节）。
- **素材包安装**：填本地 zip 路径或 http(s) 直链 → 「安装」，结果写回状态行并弹通知；
  安装成功后自动刷新 FeatureSet 与 OCR 纠错表。
- **诊断包 / 语言包**：见 [隐私遮挡与支持工具](privacy_and_support.md) 第 4、5 节。

## 9. 安装自检

`src/core/health_check.py` +「工具与诊断 → 安装自检」+ 命令行 `tools/health_check.py`。

流程 / 队列 / 轮椅方案 / 路线 / 配置这些文件都是**用户手改的**，格式错了不会在保存时报错，
而是跑到一半才炸。自检把它们提前体检一遍：

| 检查 | 发现什么 |
|------|----------|
| 模板 | 纯色块、重复模板（复用模板自检） |
| 流程 | 结构错误、超 500 步 / 4 层 / 1000 次、字段拼错、比例越界 |
| 队列 | 解析失败、队列为空 |
| 轮椅方案 | 分段为空、循环非法、拼不成流程 |
| 导航路线 | 航点缺动作、页面名为空 |
| 配置 | `configs/*.json` 是否合法 JSON |
| i18n | 6 个语种目录是否齐全、是否有空 msgstr（装了 polib 才会查空串） |

```bash
uv run python tools/health_check.py           # 退出码 0 = 没问题
uv run python tools/health_check.py --strict  # 有 warning 也返回 1（CI 可用）
```

每一项都**不会抛异常**：坏文件变成一条结果，其它项继续查；单项检查自身出错也只会记成
「检查本身出错」，不影响其它项。

## 10. 测试覆盖

| 测试 | 覆盖 |
|------|------|
| `tests/test_runtime_config.py` | 默认值不变、类型兜底、低配缩放、ConfigOption 注册；重试间隔解析与真正被 `wait_until` 使用、`wait_until` 超时吃全局策略（默认用框架值 / 自定义覆盖 / 低配放大 / 显式值也放大） |
| `tests/test_stuck_and_recovery.py` | 卡死阈值/去重告警/低配放大、返回次数上限、异常不外抛、失败帧落盘 |
| `tests/test_failure_frame_store.py` | 落盘/索引/读取/修剪/清空/损坏容错/同名毫秒去重 |
| `tests/test_template_rescorer.py` | 排序、判定分档、全屏 box 与 -1 阈值契约、名字去重、缺模板容错、与 RuntimeMixin 等价 |
| `tests/test_template_rescorer_e2e.py` | **真模板端到端**：贴进画面的模板拿 +1.000 且排第一、没贴的得分明显更低、全黑画面不命中、summary 带计数与耗时 |
| `tests/test_template_audit.py` | 纯色块拒绝、有纹理通过、偏弱/尺寸过小/空区域、灰度图、SHA-256 稳定性与分数排序、COCO 批量自检（重复分组、越界 bbox、缺图报错） |
| `tests/test_asset_pack.py` | 清单读写、篡改检测、zip slip、前缀白名单、备份、dry-run、回滚 |
| `tests/test_feature_reload.py` | 刷新必须持有 `feature_set.lock`、返回 load_success、任务运行中告警、异常不外抛、无锁/无 feature_set 也不崩 |
| `tests/test_health_check.py` | 流程合法/比例越界/无法解析、空队列、方案拼装失败、路线缺动作、配置坏文件、汇总报告与退出码、单项检查不抛异常 |
| `tests/test_config.py` | 新增 Tab 可导入且已注册 |

全部离线可跑，不需要游戏：

```bash
uv run python -m unittest discover -s tests
```
