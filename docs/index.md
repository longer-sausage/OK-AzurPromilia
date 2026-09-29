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
│   ├── icons.py                图标（复用 FluentIcon）
│   ├── core/
│   │   ├── base_game_task.py   任务通用基类（self.loop、暂停感知、配置迁移）
│   │   ├── base_mixin/         通用能力 mixin（RuntimeMixin、FrameworkOverrideMixin）
│   │   ├── detector/           Action 生命周期识别层（Hit / Detector 统一判据）
│   │   ├── config_migration.py 配置键迁移工具
│   │   ├── game_window.py      按 exe + 窗口类名定位游戏窗口
│   │   ├── global_config_store.py 项目自建全局配置存储
│   │   └── sequence_parser.py  逗号分隔配置串解析
│   ├── tasks/
│   │   ├── onetime/            一次性业务任务（Daily、HomeDaily、CommissionDaily、ClaimDaily）
│   │   ├── trigger/            触发式任务（AutoJenga、AutoRhythm、SkipDialog、StarLink、TreasureUnlock）
│   │   ├── test/               调试与测试任务（Screenshot、Interaction、TreasureBand、UINavigate）
│   │   ├── daily/              多步骤任务编排与汇总（DailyFeature、步骤统计）
│   │   └── account/            账号作用域配置存储
│   ├── gui/                    自定义 Tab（全局配置 / 账号配置）
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
