# ok-ap 项目文档

面向《蓝色星原：旅谣》（Azur Promilia）的游戏自动化项目，基于 [ok-script](https://ok-script.com/) 开发。

> 项目仍在开发中。**工程骨架已就位，游戏内容尚未实机开发。**

## 快速导航

- [从源码运行](dev/quickstart.md)
- [开发指南](dev/development.md)

## 项目结构

```
├── main.py / main_debug.py     入口（Release / Debug）
├── src/
│   ├── config.py               ok-script 应用配置与任务注册
│   ├── globals.py              全局单例
│   ├── icons.py                图标（默认复用 FluentIcon）
│   ├── core/
│   │   ├── base_game_task.py     任务基类
│   │   ├── base_mixin/         通用能力 mixin（能力库 / 框架方法覆写）
│   │   ├── config_migration.py 配置键迁移
│   │   ├── game_window.py      按 exe + 窗口类名找游戏窗口
│   │   ├── global_config_store.py 全局配置（本项目自建）
│   │   └── sequence_parser.py  逗号分隔配置串解析
│   ├── tasks/
│   │   ├── onetime/            一次性任务
│   │   ├── trigger/            触发式任务
│   │   ├── test/               测试任务
│   │   ├── daily/              多步骤任务编排与汇总
│   │   └── account/            账号作用域配置存储
│   ├── gui/                    自定义 Tab（全局配置 / 账号配置）
│   ├── interaction/            游戏交互（窗口 / 键鼠 / 屏幕位置）
│   ├── image/                  图像算法（旋转模板匹配 / 图像指纹 / HSV）
│   ├── yolo/                   YOLO 模型注册与 OpenVINO 推理
│   ├── patches/                启动补丁
│   └── data/
│       ├── feature_list.py      模板匹配特征枚举（由标注生成）
│       └── lang/               lang JSON 读取器
├── assets/coco_annotations.json 模板标注
├── assets/lang/                OCR 语言 JSON
├── i18n/*/LC_MESSAGES/ok.po    6 种语言 gettext 目录
├── tests/                      unittest 测试
├── scripts/ tools/             通用工具脚本
├── docs/                       本目录（面向人的文档）
└── .github/workflows/          CI（build / docs / release）
```

## 开发环境

依赖用 [uv](https://docs.astral.sh/uv/) 管理，需要 Python 3.12：

```bash
uv sync
uv run python main_debug.py
```

跑测试：

```bash
uv run python -m unittest discover -s tests
```
