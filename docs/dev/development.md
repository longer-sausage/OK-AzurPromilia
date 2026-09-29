# 开发指南（DEVELOPMENT）

## 架构

本项目基于 [ok-script](https://github.com/ok-oldking/ok-script)。应用配置集中在 `src/config.py`，任务与 Tab 均在此注册。

Python 模块文件统一用 `snake_case.py`，`tests/` 中的测试文件用 `test_*.py`；类名保持 `PascalCase`。

**框架版本锁定 `ok-script==2.0.6`**（`pyproject.toml` + `uv.lock` 为唯一来源）。

### 目录职责

| 目录 | 职责 |
|------|------|
| `main.py` / `main_debug.py` | 入口（Release / Debug）。启动前先装 `src/patches/` |
| `src/config.py` | ok-script 应用配置：窗口、OCR、模板匹配、任务与 Tab 注册 |
| `src/core/base_game_task.py` | **任务基类**：暂停感知计时、配置迁移、异常处理、配置分组 |
| `src/core/base_mixin/runtime_mixin.py` | 通用能力库：分辨率映射、点击验证、YOLO 检测、画面稳定判定、键鼠 |
| `src/core/base_mixin/framework_override_mixin.py` | 用"同名覆写 + `super()`"扩展框架方法（不复制框架代码） |
| `src/core/detector/` | **识别层**：把四类识别源（模板 / YOLO / OCR / 按钮）归一成统一判据 `Hit` / `Detector`，供 Action 生命周期编排使用 |
| `src/core/config_migration.py` | 配置键迁移工具（改键名时使用，防丢用户配置） |
| `src/core/global_config_store.py` | 本项目自建的全局配置（**不走框架的 `config['global_configs']`**） |
| `src/interaction/` | 窗口与键鼠：`GameInteraction`（自定义后台输入）、`Mouse`、`ScreenPosition`、`KeyConfig` |
| `src/image/` | 图像算法：`rotated_template`（旋转模板匹配）、`stability`（图像指纹）、`frame_processes`、`hsv_config`、`button_detector`（固定 Box 按钮检测，无 OCR）、`glow_target_detector`（可射击光圈，HSV+圆弧拟合）、`treasure_band_detector`（开锁颜色带，HSV+连通域）、`rhythm_detector`（音游音符及长条头尾）、`jenga_detector`（祖赞卡之梯的悬挂图腾与塔顶） |
| `src/yolo/` | YOLO 模型注册（`models.py`）与 OpenVINO 推理 |
| `src/tasks/onetime/` | 一次性任务 |
| `src/tasks/trigger/` | 触发式任务 |
| `src/tasks/test/` | 调试/测试任务 |
| `src/tasks/daily/` | 多步骤任务的编排（四态统计、失败标记、分账号轮次、汇总报告） |
| `src/tasks/account/` | 账号作用域配置存储 |
| `src/gui/` | 自定义 Tab（全局配置页、账号配置页） |
| `src/patches/` | 启动补丁。**是对框架内部实现的猴子补丁，升级 ok-script 前必须逐个复核** |
| `src/data/feature_list.py` | 模板名枚举（**由标注自动生成**，代码里不要写裸字符串模板名） |
| `src/data/lang/` | `assets/lang/*.json` 的读取器 |
| `assets/coco_annotations.json` | 模板标注（COCO 格式） |
| `assets/lang/` | **OCR 匹配文本**（不是 UI 文案） |
| `i18n/` | gettext 目录（**UI 文案**） |
| `scripts/` `tools/` | 语言同步、PO 修复、lang 类型桩生成等工具 |
| `tests/` | unittest 测试 |

## 花瓣音游：自动演奏

在触发任务中启用「自动音游」，按 F 进入花瓣音游后自动接管，结束后自动释放按键。
蓝色用 Q、红色用 E、紫色同时按 Q/E；长条持续按住至尾部经过判定点。
游戏需要保持前台，程序和游戏的权限等级应一致。

- `src/image/rhythm_detector.py`：三枚说明图标作为界面闸门。游戏 Canvas 保持等比缩放，
  轨道居中、说明图标靠右，不能按截图的宽高分别拉伸。`RhythmLayout` 将实际 UI 的
  缩放和偏移映射到 1920×1080 的逻辑坐标；窗口边框造成偏移时用任意两枚图标校准，
  再校验全部三枚。窗口尺寸变化时重新定位。
  HSV + 连通域识别头尾，用高亮圆形与本轮学习的高通纹理找回与场景粘连的头部，
  再检查白色按键图案与前景饱和度，排除背景光斑。长条结合横向颜色支持与淡灰尾符纹理定位，
  渐隐区域的颜色边界只作为长度下界；完整轨道用于测尾部，清晰区用于建轨迹。
  头部被命中特效覆盖时从右侧身体继续测尾部，圆角拟合拆开粘连的短音符。
  只归一化小 ROI，WGC 的 BGRA 截图也无需先复制整张画面。
- `src/tasks/trigger/auto_rhythm_task.py`：在清晰区域建立轨迹，多帧直线拟合滚动速度，预测被
  命中特效遮挡的音符。速度拟合至少需要三个观测、80 ms 的时间跨度；未完成拟合时，
  关联范围随帧间隔扩大，适应低帧率。使用 `perf_counter` 和画面生成时间补偿截图、
  识别延迟；按下和长条松开使用 UI 配置「输入延迟 (ms)」，默认提前 50 ms，范围 0..150 ms。
  所有音符共享本帧位移与滚动速度，遮挡轨迹也跟随共同位移。定时回调没有新观测时保持速度，
  清理旧轨迹不能改变已经安排的截止时刻。下一次截图可能跨过按键时刻时，
  按实际截图加识别耗时先服务定时输入。
  轨迹至少有三个观测才参与按键/长按冲突调度，避免单帧身体碎片提前截断长按。
  每次只消耗实际选中的音符，同键待松开时优先服务松键，过期音符不阻塞后续截止时刻。
  截图短暂变慢时，已确认音符保留至预测判定时刻后 100 ms；按滚动速度换算位置容差，
  避免固定像素截止线随速度改变有效时间窗。
  游戏在长条松开后才推进当前音符，因此长按结束前不输入下一枚音符，无论它是什么颜色；
  根据已拟合的下一枚音符预留 45 ms 松键间隙。连续按下至少间隔 60 ms，
  避免相邻 Q/E 单键被游戏合成为紫色双键；重触发时保留至少 15 ms 抬起态。
  长度和短/长分类通过多帧确认，单帧背景粘连或身体缺失不能改写已确认的长按。
  演奏期间通过 `self.loop` 独占任务循环，定时等待可由停止事件打断。
- `src/patches/capture_timestamp_patch.py`：为 WGC 保留 `SystemRelativeTime`，100 ns 单位转为
  与 `perf_counter` 一致的 QPC 秒数。时间戳在取帧锁内与返回画面配对，按调用线程保存，
  避免截图预览覆盖任务正在使用的观测时间；取帧失败时清除旧时间戳。
  补丁针对锁定的 ok-script 2.0.6，升级时复核截图生命周期；
  其它截图方式使用截图耗时的中点估计，无法保证相同的时序精度。
- 停用、暂停、失焦、退出、截图中断和异常均清理持键；清理直接调用交互层，避免框架
  `check_enabled()` 在停用后阻止 key-up。看门线程只负责终止输入，不截图也不按新键。
- `tests/test_auto_rhythm_task.py` 覆盖真实截图、640×360 到 4K、超宽屏/竖屏/窗口比例、
  BGRA、三色短音符、连续同色、遮挡、粘连长条、10..144 FPS、不同速度/处理延迟、
  定时输入与停止清理；`test_capture_timestamp_patch.py` 验证 WGC 时间戳保留。
  `tests/fixtures/rhythm/other_pc_*.png` 来自其他电脑的参考录像，详见同目录 README。
  截图只保留识别区域，其余画面置黑；测试不连接游戏，不发送真实按键。

2026-09-29 授权本机实机验证（1920×1080、ForegroundBitBlt）结果为 259 次 Perfect，
Good / Normal / Miss / Wrong 全部为 0，总完成度 99.86%；另用 WGC 实机验证，
同样 259 次 Perfect、其余判定为 0，总完成度 100.00%。参考录像的 2107 帧音游画面
另作离线复核。447 项完整测试通过，其中包含分辨率矩阵和时序、停止清理等回归；
未修改 `src/config.py` 的分辨率设置。

## 祖赞卡之梯：自动放置图腾

当前验收目标为每轮两分钟内完成、零 Miss，允许 Good / Perfect。2026-09-26 的本机机器码核对见
[实际判定与运动公式](jenga_judgment.md)：接触面多边形面积决定 Perfect，
悬挂轨迹为八字，释放继承横向速度，下落使用带限速的加速度。
2026-09-28 的较早候选已完成 88.3 秒、20/20、零 Miss 的实机投放；随后仍发现
其他轮次的 Miss 和超时，不能将这一轮作为当前实现稳定通过的证明。
当前候选及不同分辨率、截图方式的整轮验收记录见上述文档。

在触发任务中启用「自动祖赞卡之梯」，手动进入玩法并保持游戏前台。任务根据画面
自动对齐、放置，界面消失、暂停或失焦时等待恢复；手动停用或退出程序时结束任务，
停用及异常均释放按键。
不负责从大世界寻找玩法入口、选择模式或重开结算画面。

- `src/image/jenga_detector.py`：匹配顶部操作图标，并检查右下角动画星星的颜色占比，确认玩法界面。
  用鸟与图腾的空间关系确认悬挂状态，用图腾细节模板寻找最高承接面，坐标归一至
  1920×1080。灰度高通过滤模糊背景，并比较同帧前景的纹理密度，防止模糊栏杆进入
  模板缓存。模板覆盖尺度与小角度晃动；未知悬挂造型尝试
  从鸟下方清晰轮廓定位，在释放后保存有限数量的会话模板供后续塔顶识别。
  对高通响应限幅，减少闪光特效的影响；重新定位使用较低分辨率，连续跟踪使用
  较高分辨率。塔顶搜索覆盖横向 100..1820，避免高层摆动与累计偏移越出旧搜索区。
  鸟颈部在顶部提示条后分成两段时保留下缘锚点；未知裁图会定期核对强参考匹配，
  已确认的悬挂造型保持至本次释放。逆光时低对比轮廓补全与头部位置一致的暗腿，
  避免缓存只含头和躯干的局部裁图。
  黄色翅膀与鸟颈尺寸相近时，用下方完整图腾消除歧义。方向光下的参考造型若未达到
  单帧强匹配门槛，需三个较强的不同画面，或五个较弱画面跨过至少 50px 横向弧段，
  支持同一造型且明显高于其他造型才确认身份；
  固定画面不增加票数，身份确认不修改已有定位边界。小于 160px 的缓存另核对完整
  轮廓，位置一致且延长至少 12px 时恢复脚部，覆盖旧 25px 延长门槛遗漏的裁短。
  完整高度经不同位置的多帧确认后单独保存，缓存改为跟踪上部纹理；
  同一静止画面不建立完整高度。已确认人形或猫头鹰且上端紧邻鸟颈时，
  可使用本轮轨道测得的画面比例和原生网格高度；未知造型不强行补齐。
  缓存虽相关度很高但上端偏离鸟颈时，先回退到完整参考，再尝试轮廓；
  不让局部躯干持续成为新的完整上端。兔子的耳部另保留原有完整轮廓恢复路径。
  塔顶缓存带小角度转动变体；缓存失配时优先重查该造型，通过纹理、位置及置信度
  检查才接受，否则回退到全部造型，不把弱匹配当作有效跟踪。
- `src/tasks/trigger/auto_jenga_task.py`：`JengaPlayer` 从鸟的横向轨迹恢复八字相位和
  像素/世界单位比例，完整图腾底边独立测量高度；顶部提示条及光照造成的鸟颈
  下缘变化不会进入相位或横向速度。按原生 1/30 秒固定步先更新重力速度，
  用重力 49.05、终端速度 10 及最后一步的实际接触位置求解下落时间，保留
  释放瞬间的横向速度。塔顶使用 4 秒周期拟合，并与独立的几何时钟预测交叉核对。
  覆盖至少 2 秒时可拟合图腾相对鸟的周期横移，只有误差更小且振幅合理才使用。
  首块至少收集 2 秒、12 个观测。后续可保留已测横向中心和半径，重新拟合高度与
  塔顶；短观测需至少 0.6 秒、6 个样本，并增加未来位置误差及外推放大检查。
  未满 1.2 秒的短弧另留 22px；在 1.2..2 秒之间连续淡出，并始终计入实际残差和
  外推放大，避免某一帧跨过时间阈值就突然失去保护。投放时刻比较整个时间误差范围内的落点，
  在相同接触余量下允许选择更稳妥的非零偏移 Good。
  塔顶均值偏离已测轨道中心时，仅在完整预算合格的候选中优先向内修正，
  小于 0.025 世界单位的差异不修正，超出部分乘以 0.65，单次修正不超过
  0.055 世界单位；修正只改变完整接触预算内的候选排序。
  交接方向来自成功放置后新鸟早期的半程，避免依赖固定屏幕方向。
  原生 `BeginMovement` 每次重置相位。塔身明显摆动且观测覆盖两秒时，
  在当前安全候选之外预估下一块的接触机会：未知接触面、三个高度假设、
  原生换块时序、短弧保护及 27 个时序组合参与比较。
  下一块换相位的时刻另覆盖当前释放、下落与换块固定步的不确定性；
  未知未来造型的保守预算不合格时，仍可优先选择明显改善下一块接触的交接。
  若一圈内另一个当前安全交接预计能明显缩短两块总等待，最多等待一次；
  到达计划时刻仍须用新观测重新通过全部当前接触检查，计划过期便丢弃。
  等待期间也持续复核未来接触预算；新观测使计划失效时，立即恢复当前安全候选，
  避免放过合格的半程后才发现远期计划无法执行。
  预判只选择时机，不能扩大 Good 范围或绕过识别、残差、时效与按键截止检查。
  未可靠观测早期方向时保持原有选择。
  避免允许的小偏移连续向同一侧累积，令高层错过许多摆动周期。
  不足一个原生鸟轨道周期（2π / 1.6 秒）的遮挡保留记录，并继续通过当前帧、
  拟合残差与双时钟检查；垂直镜头跳变重测高度。更长的识别间断清除历史时钟，保留已测
  横向中心与半径并用新短弧重新验证；画面冻结或场景退出清除全部运动先验。
  高度拟合要求至少 60% / 6 点落在 25px 内的同一簇，且当前帧属于该簇。
  在未来最多 250 ms 内求解相交时刻；没有中心交点时也检查最近落点。
  Good 接触范围内才可释放，按投放/接触固定步时间误差与较小接触面的半面积
  检查 Good 余量；未知造型使用保守尺寸。预测或实际输入迟到时跳过按键。
  下落时距的不确定性同时改变悬挂物的横向漂移和承接面的预测时刻；
  飞行区间从首次几何接触覆盖到当前 1/30 秒物理步结束，使用半步中心及两端，
  另按冲击速度加入可见网格/碰撞面边缘余量。
  承接面另有独立的接触帧误差。释放、飞行和承接时刻的 27 个组合均参与预算。
  每次释放后从两幅不同画面确认 Perfect / Good / Miss。Miss 或判定缺失时继续运行，
  无单轮超时或失败锁定，任务持续至手动停用。
  新一块需有消失重现、足够横移或双帧成功判定的证据，且释放后至少等待 0.8 秒，
  避免重复按键。判定轮询已消耗消失画面时，不再要求新块远离旧释放坐标。
- 实际截图的操作提示为 **F / 鼠标左键**，任务使用全局 `Interact Key` 对 F 的映射。
  不照搬资料包 demo 的空格键、十层目标或 88 px/m；截图包含 `0/20`、`2/20`、
  `19/20`，任务也不把这些分数当作固定层数或结束条件。
- `_input_lead_ms=45` 保留原有图像运动时序补偿的语义，包含输入与刚体渲染插值的影响；
  在中心值周围至少保留半个固定步，较长的额外补偿和实际相位抖动扩大范围。
  WGC 画面生成时间另外补偿截图、识别耗时。
  陈旧或重复的 WGC 时间戳不能作为新观测；无时间戳时使用取帧起止的中点估计。
  `_fall_time_ms`、`_drift_time_ms` 为兼容用户历史数据保留，物理预测已不读取这两个
  固定时距。实际像素比例来自本轮拟合的轨迹横向振幅 / 原生半径 1.25。
- `assets/jenga/` 为参考截图裁出的图形模板；`tests/fixtures/jenga/` 为遮去身份区域
  的真实截图。测试覆盖多分辨率、背景干扰、新造型回退、双向塔身相位、按键清理、
  丢帧和释放去重。

验证方式：使用 Python 调用真实 `AutoJengaTask.run()`、框架前台 BitBlt 截图与
`GameInteraction` 按键，并记录逐帧时间戳、释放记录和录像。本次验证使用 Python。
静态回归包含昼夜光照、动画星星和摇摆塔顶；时序回归包含低帧率、短暂遮挡、
双向运动与释放后的横移。界面消失只表示暂停投放，不单独作为挑战成功证据。

2026-09-26 实机结果：放置达到 **20/20**，结算显示 **CHALLENGE COMPLETE、两星**；
任务在操作 HUD 消失后停止输入，补录结算确认通过。结算截图保存在
`tests/fixtures/jenga/complete.png`（身份区域已遮去）。此次验证覆盖昼夜变化、人形、
猫头鹰、兔子，以及高层快速摇摆；两星结果不代表全部放置均为完美判定。

## 宝箱开锁：颜色带检测与开锁任务

开锁小游戏右侧轨道里的颜色带是**低饱和、高亮度、竖向细长**的矩形块，同一区域还有
两类干扰：贴 ROI 边缘的轨道边框、矮胖的横向钥匙。前者靠「不贴边」排除，后者靠长宽比
排除，都不是颜色问题，所以统一走**连通域 + 形状判据**。

### 检测器 `src/image/treasure_band_detector.py`

流程：`Box → ROI → HSV inRange(S≤80, V≥190) → 3x3 开运算 → 连通域 → 形状过滤 → 按 Y 排序`

默认判据（1920×1080 实测）：`area≥300`、`w≤30`、`h≥30`、`h/w≥2.0`、不贴 ROI 左右边缘。

```python
from src.image.treasure_band_detector import TreasureBandDetector

detector = TreasureBandDetector()                 # 复用实例，不要逐帧新建
roi = self.box_of_screen(0.6797, 0.2324, 0.7021, 0.7778)

bands = detector.find(frame, roi)                 # 命中，按 Y 升序
results = detector.analyze(frame, roi)            # 全部连通域，含被过滤项与原因
detector.y_ranges(bands)                          # [(286, 391), (552, 605), (672, 725)]
TreasureBandDetector.hit_by_center_y(bands, 580)  # 钥匙 center_y 落在哪条带
```

| 成员 | 说明 |
|------|------|
| `find(frame, box)` | 命中的帧坐标 `Box` 列表（可直接 `click()` / `draw_boxes()`） |
| `analyze(frame, box)` | 全部连通域的 `BandDetection`，`failed` 给出过滤原因，用于校准阈值 |
| `presence(frame, box)` | Box 内目标颜色像素占比 0~1，**存在性判定专用** |
| `y_ranges(boxes)` / `hit_by_center_y(boxes, y)` | Y 区间 / 钥匙命中 |

阈值集中在 `BandThresholds`，用 `with_(...)` 生成副本。尺寸类阈值**不随分辨率自动缩放**，
调用方按 `resolution_scale()` 自行换算（开锁任务里已做）。

### 存在性判定为什么不用 `find()`

钥匙是横向结构，压在条带上会把它切成上下两段，连通域各自变小后被形状判据拒绝，
结果是「**明明还在却被判消失**」。所以判断条带在不在只统计 bbox 内目标颜色像素的
占比（`presence`），不看形状。真实截图实测：条带在 0.99，被抹掉后 0.00，
阈值 0.15 有充足余量。

### 开锁任务 `src/tasks/trigger/treasure_unlock_task.py`

触发式任务，状态机跨 `run()` 调用保持：

```text
WAIT_TREASURE ──treasure_icon──> CALIBRATING_BANDS ──连续多帧稳定──> UNLOCKING
                                                                        │
                              钥匙 Y 命中缓存 bbox → 点击 → 稳定消失 → 移出 active
                                                                        ↓
   FINISHED <── 持续无条带 ── COMPLETION_CHECK <──── active 空 ─────────┘
```

要点：

- **校准结果是后续唯一的定位基准**。开锁阶段不再重建坐标，只用缓存 bbox 做
  「钥匙 Y 匹配」和「存在 / 消失判定」。
- 稳定判定一律用连续状态而非单帧：校准用「连续 N 帧布局一致」，消失与完成确认用
  `wait_until(..., settle_time=...)`（复用框架，就是「条件持续成立一段时间」）。
- 点击失败到上限后把该条带移到队尾，避免死磕一条；`active` 清空后还要做
  **完成确认**（`完成确认时长`），期间条带重现就回 `UNLOCKING`。
- 宝箱 UI 只作为「已退出界面」的**辅助**信号，不能反过来阻塞结束，否则图标常驻时
  会永远卡在完成确认里（有 2 倍时长上限兜底）。
- `treasure_key_icon` **必须显式传搜索 box**：`find_feature` 不传 box 时只搜 coco
  标注位置 ±variance（约 4px），而钥匙会沿轨道上下滑动，默认框根本搜不到。

调试任务 `src/tasks/test/test_treasure_band_task.py` 会在覆盖层实时画框
（红=命中 / 绿=ROI / 蓝=被过滤），用来肉眼校准阈值。

## 固定 Box 按钮检测（中央文本带，无 OCR）

部分按钮（如「跳过剧情 / 确认」）底色为深灰 `RGB(50,50,53)`，与游戏背景几乎一致，
模板匹配和「整块颜色判定」都不稳定。这类按钮的可靠特征是**按钮中央的文字**。

`src/image/button_detector.py` 检测「Box 中央是否存在一条颜色落在指定 HSV 区间内的文本带」，
**只有两个颜色参数**：

- `text_hsv` —— 文字（前景）颜色区间，**主特征**；
- `backdrop_hsv` —— 底色（背景）颜色区间，**可选辅助特征**。

入口挂在 `RuntimeMixin` 上：

```python
box = self.box_of_screen(0.575, 0.61, 0.64, 0.64)
if result := self.find_button(box):      # 默认 = 深灰底 + 亮色文字
    self.click(result)                   # 结果可直接点击

# 直接传颜色
self.find_button(box, text_hsv=((0, 0, 170), (180, 100, 255)))
self.find_button(box, text_hsv=((0, 0, 0), (180, 255, 90)),
                      backdrop_hsv=((0, 0, 170), (180, 80, 255)))

# 或封装成语义常量复用
from src.image.button_detector import ButtonThresholds
SKIP_BUTTON = ButtonThresholds.for_button(
    ((0, 0, 170), (180, 100, 255)),   # 文字：亮色
    ((0, 0, 30), (180, 80, 110)),     # 底色：深灰
    name="skip_button",
)
self.find_button(box, thresholds=SKIP_BUTTON)
```

| 方法 | 返回 | 说明 |
|------|------|------|
| `find_button(box, frame=None, thresholds=None, text_hsv=None, backdrop_hsv=None)` | `Box \| None` | 命中返回可直接 `click()` 的 Box |
| `find_buttons(boxes, ...)` | `Box \| None` | 多个候选 Box，返回首个命中 |
| `wait_button(box, time_out=5, ...)` | `Box \| None` | 以视觉状态等待，不用固定延时 |
| `analyze_button(box, ...)` | `ButtonDetection` | 含中间指标与未命中原因，用于校准阈值 |
| `button_detector(thresholds=None)` | `ButtonDetector` | 缓存的检测器实例 |

预设（`ButtonThresholds` 的类方法 / 常量）：

| 预设 | 文字 HSV | 底色 HSV | 适用 |
|------|----------|----------|------|
| `DARK_BUTTON_THRESHOLDS` / `DEFAULT_BUTTON_THRESHOLDS`（默认） | `(0,0,170)~(180,100,255)` | `(0,0,30)~(180,80,110)` | 深灰 / 黑底 + 亮字 |
| `LIGHT_BUTTON_THRESHOLDS` | `(0,0,0)~(180,255,90)` | `(0,0,170)~(180,80,255)` | 亮 / 白底 + 深字（默认校验底色） |
| `for_button(text_hsv, backdrop_hsv, name=...)` | 任意 | 可选 | 封装任意语义按钮 |

要点：

- 底色区间**只在 Box 不够贴合按钮时才明显生效**：真实 1080P 截图滑窗 884 个位置实测，
  只用文字特征误报 17，加上底色区间后 0，命中率不变，单次 +0.07 ms。按需开启。
- 所有阈值集中在 `ButtonThresholds`，用 `with_(...)` 生成改过的副本，不修改默认值。
- 传入的 Box 要**贴合按钮**；Box 远大于按钮时文本带相对过薄，会被形状判定拒绝。

**以视觉状态等待**用 `wait_button(box, time_out=5, ...)`，不要用固定延时。若还需要「等到 → 点击 → 验证结果」的完整流程，见下一节的 `wait_action_result`。

## Action 生命周期：等条件 → 动作 → 验证

游戏里大量操作是同一个形状：**看到某个东西 → 动它 → 确认预期结果出现了**。
这类「识别 + 动作 + 再识别」的流程收敛成两个 API：

- **识别层** `src/core/detector/` —— 把模板 / YOLO / OCR / 按钮四类识别源归一成统一判据
  `Detector.detect(frame) -> Hit | None`；
- **编排层** `RuntimeMixin.wait_action_result` / `wait_expectation` —— 负责「何时执行动作、何时判定成功」。

两层正交：识别层只管「这一帧有没有」，编排层只管「什么时候做、做几次」。

```python
from src.core.detector import TemplateDetector
from src.data.feature_list import FeatureList

# A≠C：看到宝箱图标 → 点击 → 等解锁界面出现
self.wait_action_result(
    condition=TemplateDetector(FeatureList.treasure_icon),   # A 前置条件
    action=lambda hit: self.click(hit.box),                  # 主 Action
    expect=TemplateDetector(FeatureList.unlock_ui),          # C 预期结果
    time_out=5, expect_time_out=1.5, max_attempts=2,
)
```

| 阶段 | 触发条件 | 行为 |
|------|------|------|
| ① 等条件 | `condition` 不为 `None` 时 | `condition` 在 `time_out` 内命中才继续；为 `None` 时跳过等待直接进入阶段 ②；未命中 → 返回 `False`（`raise_if_not_found=True` 则抛 `WaitFailedException`） |
| ② 动作 + 验证 | 条件命中或 `condition` 为 `None` | `action(hit)` → `expect` 验证；未通过则重试，最多 `max_attempts` 次 |
| ③ 持续阶段 | 仅当给了 `while_condition` **且** `repeat_action` | `while_condition` 持续命中就重复 `repeat_action`；**未命中立即停止**；每轮后继续检查 `expect`，命中即返回 `True` |

只等一个结果（只用到 C）则走 `wait_expectation`：

```python
hit = self.wait_expectation(TemplateDetector(FeatureList.main_ui), time_out=2.0)
```

三条最容易踩的契约：

- **`expect=None` 表示「动作成功即返回」** —— 调用方声明该动作没有可验证的结果。
- **`settle_time` 默认 0（命中一次即可）**；非 0 的语义是「**每次判定**都要求连续成立够时长」，
  而不是「总共等这么久」—— 会显著增加耗时，只在「等 UI 稳定下来再确认」时开启。
- **条件命中与动作之间会重取一帧**，防止会动的目标在 `settle_time` 期间位移。

> 📖 **完整参考见 [`action_lifecycle.md`](action_lifecycle.md)** —— 含全部识别器参数（`pick` 策略、
> `mask_function`、`use_find_one` 等）、组合器语义、典型用法配方与真实落地样例。

## 循环与重试规范（`self.loop`）

在任务或 Mixin 中进行**带超时的轮询、重试或多帧检测**时，**严禁裸写** `while self.active_time() - start < time_out:`、`while time.monotonic() < deadline:` 并手动 `self.next_frame()`，**必须统一使用 `self.loop`**（定义于 `BaseGameTask`）：

### 为什么使用 `self.loop`

1. **暂停感知（Pause-aware）**：内部基于 `self.active_time()` 计算活跃耗时，任务被用户暂停期间计时自动冻结，避免无谓超时。
2. **自动取帧驱动**：`yield_frame=True`（默认）时每次迭代自动调用并产出下一帧（`self.next_frame()`），循环体内无需手动写 `self.next_frame()`。若不需要帧（或动作内部自行取帧），传 `yield_frame=False`（产出 `None`）。
3. **超时行为可控**：
   - `raise_if_time_out=True`（默认）：循环超时未提前 `break`/`return` 时抛出 `TimeoutError('Loop time out.')`。
   - `raise_if_time_out=False`：超时后正常退出循环，可在循环后执行兜底逻辑或抛出业务异常（如 `WaitFailedException`）。
   - `raise_if_time_out=ExceptionInstance` 或 `ExceptionClass`：超时直接抛出指定的自定义异常。

### 标准用法模式

- **模式 A（最常用）：默认取帧，找到目标即 break，超时自动抛 TimeoutError**
  ```python
  for frame in self.loop(time_out=10):
      if target := self.find_one(FeatureList.some_btn, frame=frame):
          self.click(target)
          break
  ```

- **模式 B：超时后执行特定兜底或抛业务异常（WaitFailedException）**
  ```python
  for frame in self.loop(time_out=10, raise_if_time_out=False):
      if self._check_success(frame):
          return
  raise WaitFailedException("操作超时")
  ```

- **模式 C：不需要自动取帧的时间控制循环**
  ```python
  for _ in self.loop(time_out=10, yield_frame=False, raise_if_time_out=False):
      if self.try_step():
          return
  ```

- **模式 D：直接指定超时抛出的异常类型或实例**
  ```python
  for frame in self.loop(time_out=5, raise_if_time_out=WaitFailedException("未找到确认按钮")):
      if confirm := self.find_confirm(frame=frame):
          self.click(confirm)
          break
  ```

## 辅助星结：可射击光圈检测与辅助任务

星结时（智慧种族与奇波通过星结卡缔结契约），光标指向目标会在屏幕中心出现一道
彩色光圈作为可射击标识：绿（100%）、金黄（48.3%）、橙红（1.4%）对应不同命中概率。

### 检测器 `src/image/glow_target_detector.py`

关键是**这道光圈是同一圆上的一段弧，不是闭合圆环**，所以不能靠「有没有内孔」判断。
1920×1080 三张样本实测：圆心 ≈ 屏幕中心 `(963, 541)`、半径 ≈ 52px、圆拟合残差 < 1.5px、
弧宽 3~6px —— 三张是同一个圆。

流程：`Box → ROI → HSV inRange(S≥110, V≥190) → 3x3 闭运算 → 连通域 → 最小二乘圆拟合 → 判据过滤`

判据（默认，`GlowThresholds`）：

| 判据 | 默认值 | 作用 |
|------|--------|------|
| 残差 ≤ `max_residual` 且 ≤ `max_residual_ratio × 半径` | 6px / 0.12 | 「是不是圆」的核心，噪声与色块拟合不出低残差小圆 |
| 内切厚度 ≤ `max_thickness_ratio × 半径` | 0.35 | 排除实心圆盘（见下） |
| 半径 ∈ [`min_radius`, `max_radius`] | 30 ~ 90 | 直线会拟合出半径上千的圆，由上限挡掉 |
| 跨度（外接框对角线）≥ `min_span` | 40 | 排除小碎点 |
| 圆心落在 ROI 内 | 开 | 避免误检画面别处的大圆弧 |

```python
from src.image.glow_target_detector import GlowTargetDetector

detector = GlowTargetDetector()                    # 复用实例，不要逐帧新建
roi = self.box_of_screen(0.4724, 0.4426, 0.5365, 0.5611)

result = detector.analyze(frame, roi)              # 含残差 / 半径 / 厚度 / 未命中原因
box = detector.find(frame, roi)                    # 命中的帧坐标 Box，可直接 click()
```

两个容易踩的点：

- **厚度判据不能省。** 实心圆盘的**外轮廓本身就是一个完美圆**，只比残差会把圆盘判成环；
  加上「内切厚度 ≤ 0.35×半径」才能区分（真实弧带 3px / 半径 52px ≈ 0.06，圆盘 ≈ 1.0）。
- **厚度必须在连通域像素上算，不能用轮廓填充。** 闭合光环一填充就变成实心盘，
  厚度立刻从「半环宽」跳到「半径」，把真目标误杀。

**点击位置取拟合出的圆心**，不是外接框中心：弧只是圆的一段（往往只有右侧 90°~120°），
框中心会偏向弧那一侧，而圆心就是屏幕准心。`GlowDetection.center` 给出这个坐标。

`require_arc=False` 可退回「最大彩色团块」模式，用于提示本身是实心色块的画面。

### 任务 `src/tasks/trigger/star_link_assist_task.py`

触发式任务，`run()` 里三道闸门依次短路：

```text
star_link_icon 存在? ──否──> 返回（不在星结界面，不干预）
        │ 是
        ↓
  圆弧拟合命中? ──否──> 清空连续命中计数，返回
        │ 是
        ↓
  连续命中帧数 / 点击冷却 ──> 单击左键（圆心）
```

- **前置闸门** `find_feature(feature_name=FeatureList.star_link_icon, frame=frame)`：
  屏幕中心那片区域彩色元素不少（技能特效、场景光斑都可能拟合成圆弧），用「场合」
  把检测限定在星结界面内。这里显式传 `frame` 复用同一帧，不用 `find_one()`（会再取一帧）。
- **连续命中帧数**过滤光圈淡入淡出的过渡帧；**点击冷却**防止一次提示被点成连击。
- 尺寸类阈值（面积按平方、半径与跨度按线性）随 `resolution_scale()` 换算。

## 任务配置项的显示与隐藏

任务卡片上该出现哪些配置项，由两条 ok-script 机制决定。本项目按下面的分工使用：

| 配置项性质 | 处理方式 | 例子 |
|------|------|------|
| 用户该调的开关 | 普通键名，正常显示 | `检测绿色`、`记录点击日志` |
| 数值调优项 | **键名前加 `_`**，留在 `default_config` 但不渲染 | `_连续命中帧数`、`_条带存在阈值` |
| 调试任务自己的阈值 | 正常显示（调阈值就是它的用途） | `V 下限`、`最大宽度` |

### 用 `_` 前缀隐藏调优项

框架的渲染逻辑是 `if not key.startswith('_')`（`ok/ui/qt/tasks/ConfigCard.py`），
所以**键名以 `_` 开头就不会出现在 UI 上**，但：

- 键仍在 `default_config` 里 → 默认值保留，`configs/*.json` 里仍会写入，仍可从日志恢复
- 代码里照常读：`self.config.get("_连续命中帧数", 1)`
- `ok/core/config_schema.py` 也会跳过它们，不会漏进自动生成的 schema

改键名时记得**同步 `config_description` 的键**，否则说明会挂在一个不存在的键上。

> 调优项属于「不该让用户操心」的参数，隐藏它们**不写迁移表** —— 旧键会作为孤儿留在
> 用户的 JSON 里（无害），值回落到新默认值。只有当某个参数需要保住用户已调过的值时，
> 才按下一节加迁移表。

### 调试任务只在 debug 模式露面

框架的三个任务列表（`OneTimeTaskTab` / `TriggerTaskTab` / `ScheduleTaskTab`）都会用
`getattr(task, 'visible', True)` 过滤，所以纯调试任务在 `__init__` 里写：

```python
self.visible = self.debug      # self.debug -> executor.debug，main_debug.py 下为 True
```

正式业务任务**不要**设 `visible`，任何模式下都应可见。当前按此约定归类的调试任务是
`TestScreenshotTask`、`TestInteractionTask`、`TestTreasureBandTask`、`TestUINavigateTask`。

两条契约都由 `tests/test_task_config_visibility.py` 固化，改配置项时会被它挡住。

## 配置键迁移

修改 `default_config` 键名时必须先添加迁移表（同一提交完成）：

```python
class MyTask(BaseGameTask):
    config_key_migrations = {"旧键": "新键"}
```

`BaseGameTask.load_config` 会沿 MRO 收集所有迁移表并执行，详见 `src/core/config_migration.py`。
完整的改键名流程（含 i18n 与文档同步）见 `AGENTS.md`。

## i18n

本项目有**两条互不混用**的翻译链路：

- **UI 文案**：代码用 `self.tr("中文")`，msgid 写入 `i18n/*/LC_MESSAGES/ok.po`，
  再编译 `.mo`：

  ```bash
  python tools/task_i18n_helper.py compile --i18n i18n   # 编译全部 .mo
  python tools/task_i18n_helper.py check --i18n i18n     # 查重复 msgid（有则退出码 1）
  python scripts/validate_all.py                         # 编译 + 查空 msgstr
  ```

  新增文案时，先用 `scan` 把该进 `.po` 的字符串列出来，避免漏翻：

  ```bash
  python tools/task_i18n_helper.py scan --task src/tasks/onetime/daily_task.py
  ```

- **OCR 匹配文本**：放进 `assets/lang/<模块>.json`，每 key 下 6 种语言节点，
  代码用 `self.lang.<模块>.<key>` 读取。

其它相关工具：

```bash
python tools/gen_lang_stubs.py          # 重新生成 src/data/lang/_lang_typed.py 类型桩
python scripts/lang_fill_missing.py     # 补全 assets/lang/*.json 缺失语言（--dry-run 可预览）
python tools/repair_po_locales.py --apply   # 修复 .po 中语言写错的条目
```

## 测试

- 测试位于 `tests/`，使用 `unittest`。
- 全部跑：`uv run python -m unittest discover -s tests`
  或逐文件跑：`./run_tests.ps1`（CI 在打 tag 时也跑它）。

> ⚠️ 不要用 `python -m ok.test.RunTests` —— 它在跑完测试后会在 `ok.quit()` 处抛
> `AttributeError`，退出码非 0。

写视觉/任务逻辑的测试时用框架的 `TaskTestCase` + `set_image()`（静态图驱动），
它可以断言"识别结果"与"决策方向"，但不能断言真实输入效果。

## 运行

```bash
uv sync
uv run python main.py          # Release
uv run python main_debug.py    # Debug（更多日志、overlay、热重载）
```

命令行参数只有三个：`-t <1 起的序号>`、`-e`（跑完退出）、`-h`（headless）。
注意 `-t` **只接受序号**，不接受任务名。

## 发布

- 发版流程：`.github/workflows/release.yml`，支持手动触发 `workflow_dispatch`（提供主版本/次版本/补丁版本递增选项，自动给主分支 HEAD 打 tag），也支持直接推送 tag（`v*`）。
  单个工作流全自动完成版本计算、打 tag、测试、同步更新仓库、用 pyappify 打包并发布 Release。
- 发版串行执行，使用 `queue: max` 保留最多 100 个等待任务，后来的 tag 不会替换已有的排队任务。
- 手动发版会在 tag 注释中记录 `Release-Run`（当前工作流 run 的 URL）。完整重跑时复用该 tag 和原提交，不再次递增版本或切换到最新主分支；重建时覆盖同名构建产物。
- **`requirements.txt` 是 `pyproject.toml` + `uv.lock` 的派生产物，不要手改**。
  依赖变更后重新生成：

  ```bash
  uv export --no-hashes --no-dev --output-file requirements.txt
  ```

  （CI 用 `pip install -r requirements.txt` 装依赖，不一致会导致打包失败。）

## 目录约定

- 新增任务类必须注册进 `src/config.py`，否则 UI 中不可见。
- 任务/配置相关文档与代码同步更新。

