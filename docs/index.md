# ok-ap 项目文档

面向《蓝色星原：旅谣》（Azur Promilia）的游戏自动化项目，基于 [ok-script](https://ok-script.com/) 开发。

## 功能概览

项目已接入实机自动化支持，涵盖日常流程、小游戏玩法接管与辅助功能：

- **一次性日常任务 (`src/tasks/onetime/`)**：
  - **一键日常 (`DailyTask`)**：多子任务编排与状态追踪，一键执行完整日常。
  - **家园日常 (`HomeDailyTask`)**：支持家园烹饪制作、牧场作物收获等家园流程。
  - **每日委托 (`CommissionDailyTask`)**：日常委托接取与提交。
  - **每日收菜 (`ClaimDailyTask`)**：自动领取每日活跃、周常活跃、战令与大月卡奖励。
- **手动启动的小游戏 (`src/tasks/onetime/`)**：
  - **自动祖赞卡之梯 (`AutoJengaTask`)**：根据八字摆动轨迹拟合与碰撞面积判定，实现全自动高分放置。
  - **自动花瓣音游 (`AutoRhythmTask`)**：高精度滚动速度拟合与输入时序预测，支持多轨、长按与双键连击。
  - **宝箱开锁辅助 (`TreasureUnlockTask`)**：高精度颜色带连通域检测与自动对齐开锁。
- **触发式辅助任务 (`src/tasks/trigger/`)**：
  - **自动冲刺 (`AutoSprintTask`)** 在游戏前台按住任一 Shift 半秒后开始冲刺，松开即停。可选 1–3 人，默认 3 人。单人连续冲刺，多人从 1 号位开始按队伍顺序轮换。
  - **自动跳过剧情 (`SkipDialogTask`)**：基于中央文本带特征的快速跳过与确认。
  - **星结契约辅助 (`StarLinkAssistTask`)**：屏幕中心准心光圈圆弧拟合与自动命中。

## 角色管理

在游戏中显示右侧的队伍头像，打开侧栏「角色管理」并点击「截图」。程序会先将游戏窗口置前，再采集三个槽位的头像并显示角色名和识别置信度。
点击某个槽位的「编辑」，在下拉框中选择角色并「保存」。可选角色均有专属角色类。未识别的头像显示为「默认」，编辑时选择「默认」可恢复基类绑定。
多个外观可以绑定同一角色，切换界面语言后绑定依然有效。
下方「全部模板」可独立滚动，按 UUID 文件名查看或删除单张头像。

备份角色模板时复制整个 `user_data/char/`，其中的 PNG 和 `bindings.json` 共同保存头像与角色的对应关系。
`./user_data/` 还保存本项目的账号覆盖和日常汇总。框架任务参数和界面设置在 `./configs/`，日志在 `./logs/`，诊断截图在 `./screenshots/`。

## 快速导航

- [从源码运行 / 快速上手](dev/quickstart.md)
- [开发指南](dev/development.md)
- [Action 生命周期参考](dev/action_lifecycle.md)
- [祖赞卡之梯算法与判定分析](dev/jenga_judgment.md)

## 项目结构

```
├── main.py / main_debug.py     入口（Release / Debug）
├── src/
│   ├── config.py               ok-script 应用配置与任务/Tab 注册
│   ├── globals.py              全局单例
│   ├── icons.py                图标（使用 ok-script 内置图标）
│   ├── core/
│   │   ├── base_game_task.py   任务通用基类（self.loop、暂停感知、配置迁移）
│   │   ├── base_mixin/         通用能力 mixin（RuntimeMixin、FrameworkOverrideMixin）
│   │   ├── detector/           Action 生命周期识别层（Hit / Detector 统一判据）
│   │   ├── config_migration.py 配置键迁移工具
│   │   ├── game_window.py      按 exe + 窗口类名定位游戏窗口
│   │   └── sequence_parser.py  逗号分隔配置串解析
│   ├── tasks/
│   │   ├── onetime/            一次性业务任务（Daily、HomeDaily、CommissionDaily、ClaimDaily、AutoJenga、AutoRhythm、TreasureUnlock）
│   │   ├── trigger/            触发式任务（AutoSprint、SkipDialog、StarLink）
│   │   ├── test/               调试与测试任务（Screenshot、Interaction、TreasureBand、UINavigate）
│   │   ├── daily/              多步骤任务编排与汇总（DailyFeature、步骤统计）
│   │   └── account/            账号作用域配置存储
│   ├── char/                   角色基类和各角色的专属实现
│   ├── combat/                 配队识别、角色实例化与战斗调度
│   ├── gui/                    账号配置与角色管理
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
├── user_data/                  项目持久化数据（角色模板、账号覆盖、日常汇总）
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
