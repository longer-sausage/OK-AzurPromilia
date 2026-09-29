<div align="center">

  <img src="icons/icon.png" alt="ok-ap logo" width="160" />

  <h1>ok-ap</h1>

  <p>QQ群 1121173471</p>

  <p>
    面向《蓝色星原：旅谣》（Azur Promilia）的图像识别游戏自动化工具，基于 <a href="https://ok-script.com/">ok-script</a> 开发。
    <br />
    Game automation tool for Azur Promilia, built with <a href="https://ok-script.com/">ok-script</a>.
  </p>

  <p><i>通过模拟 Windows 用户界面进行操作：不读取游戏内存，不修改游戏文件。</i></p>

  <p>
    <img src="https://img.shields.io/badge/platform-Windows-blue" alt="平台" />
    <img src="https://img.shields.io/badge/python-3.12-skyblue" alt="Python 版本" />
    <img src="https://img.shields.io/badge/ok--script-2.0.6-orange" alt="ok-script 版本" />
    <img src="https://img.shields.io/badge/license-AGPL--3.0-green" alt="许可证" />
    <img src="https://img.shields.io/badge/status-开发中-orange" alt="项目状态" />
  </p>

  <p>
    <a href="README_en.md">English</a> | <b>简体中文</b>
  </p>

  <p>
    <a href="#run-from-source">🚀 从源码运行</a> ·
    <a href="docs/dev/development.md">📖 开发指南</a> ·
    <a href="docs/index.md">📚 项目文档</a> ·
    <a href="https://github.com/longer-sausage/OK-AzurPromilia">⭐ 点亮小星星</a>
  </p>

</div>

---

> 📌 **项目状态：工程骨架已就位，游戏内容尚未实机开发。**
>
> 本仓库当前提供的是**一套可以跑起来的 ok-script 应用工程 + 通用能力底座**
> （配置、任务注册、键鼠交互、图像识别、多账户、i18n、测试与发布流水线），
> 这部分代码是真实、可运行、有测试覆盖的。
>
> 但**旅瑶的游戏侧内容基本还没写**：窗口参数、UI 模板、按键表、“我在主界面吗”的判定、
> 日常 / 战斗 / 跑图等任务，**全部处于待实机确认或未开始的状态**。
>
> **所以现阶段它还不是一个能拿来挂机的成品**，请以「从源码运行 + 参与开发」的视角使用。
> 详细进度与技术债见 [docs/index.md](docs/index.md)。

## ⚠️ 免责声明

本软件为开源、免费的外部辅助工具，仅供**个人学习与技术交流**使用，通过模拟常规用户界面与游戏交互，
旨在简化重复性操作。程序**不读取游戏内存、不修改游戏文件、不注入进程、不修改任何游戏数据**。

- **使用目的**：仅为减少重复操作，无意破坏游戏平衡，也不提供任何不公平优势。
- **风险自负**：您应充分了解并自愿承担使用本工具可能带来的所有后果，包括但不限于账号被警告、扣除收益或封禁。
- **责任范围**：因使用本软件产生的一切问题及后果，均与本项目及贡献者无关。
- **禁止商用**：请勿将本软件用于代练、售卖等任何商业或营利性目的。

<!-- TODO（发布前补全）：在此处补上《蓝色星原：旅谣》官方用户协议 / 公平运营声明中
     关于第三方工具的原文引用与链接，格式可参照 ok-ww、ok-nte 的免责声明章节。 -->

## 🎮 这是什么

`ok-ap` 是面向《蓝色星原：旅谣》（Azur Promilia）的 ok-script 自动化项目。ok-script 是一套
「截图 → 图像识别 → 模拟键鼠」的游戏自动化框架，`ok-ap` 在此之上提供旅瑶这一款游戏所需要的
应用配置、任务与游戏特化实现。

## 📥 下载与安装

> **尚未发布正式版本。** 发版流水线（`release` + `build`）已经就位，
> 正式发版后会在此提供安装包；在那之前请[从源码运行](#run-from-source)。

## 🖥️ 运行环境

- 操作系统：Windows（x64）。
- 权限：**必须以管理员权限**启动终端 / PyCharm / VSCode（模拟键鼠输入需要）。
- 源码运行：**Python 3.12**（仅此版本），依赖用 [uv](https://docs.astral.sh/uv/) 管理。
- 游戏分辨率、画面滤镜、游戏语言等要求 —— **[待验证]**，尚未实机确认，确认后会补到这里。

<a id="run-from-source"></a>

## 🚀 从源码运行

依赖管理使用 [uv](https://docs.astral.sh/uv/)（需先安装 uv），且**仅支持 Python 3.12**。

```bash
# 创建虚拟环境并安装/更新依赖
uv sync

# 运行 Release 版本
uv run python main.py

# 运行 Debug 版本（输出更多识别信息，开发识别逻辑时用这个）
uv run python main_debug.py
```

## ⌨️ 命令行参数

参数定义在框架的 `ok/util/process.py:571`：

```pwsh
# 启动后自动执行第 1 个任务，并在任务完成后退出程序
ok-ap.exe -t 1 -e
```

| 参数 | 说明 |
| --- | --- |
| `-t <序号>` | 启动后自动执行第 N 个任务，序号对应 `src/config.py` 中 `onetime_tasks` 列表的顺序（**从 1 开始**）。**只接受序号，不接受任务名。** |
| `-e` | 任务执行完毕后自动退出程序。 |
| `-h` | 无头（headless）模式，不启动界面。**注意：这不是 `--help`。** |
| `--help` | 查看帮助。 |

## 🧪 开发与测试

```bash
# 执行 tests/ 下全部测试（PowerShell，逐文件调用 uv run）
./run_tests.ps1

# 或直接跑整个测试目录
uv run python -m unittest discover -s tests
```

## 🔗 基于 ok-script 开发的项目

- 鸣潮 [ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves)
- 异环 [BnanZ0/ok-nte](https://github.com/BnanZ0/ok-nte)
- 明日方舟:终末地 [AliceJump/ok-end-field](https://github.com/AliceJump/ok-end-field)
- 原神（停止维护） [ok-oldking/ok-genshin-impact](https://github.com/ok-oldking/ok-genshin-impact)
- 少前2 [ok-oldking/ok-gf2](https://github.com/ok-oldking/ok-gf2)
- 星铁 [Shasnow/ok-starrailassistant](https://github.com/Shasnow/ok-starrailassistant)
- 星痕共鸣 [Sanheiii/ok-star-resonance](https://github.com/Sanheiii/ok-star-resonance)
- 二重螺旋 [BnanZ0/ok-duet-night-abyss](https://github.com/BnanZ0/ok-duet-night-abyss)
- 白荆回廊（停止更新） [ok-oldking/ok-baijing](https://github.com/ok-oldking/ok-baijing)

## ❤️ 致谢

### 贡献者

<a href="https://github.com/longer-sausage/OK-AzurPromilia/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=longer-sausage/OK-AzurPromilia" alt="Contributors" />
</a>

### 特别感谢

- [AliceJump](https://github.com/AliceJump) —— 本仓库的工程骨架与通用能力层（`src/` 下的应用配置、
  基类与 mixin、交互层、图像算法、多账户、编排、CI 与发布流水线）同步自其 ok-script 项目 **ok-ap**
  （其中多个模块的文档标注为「移植自 ok-gf2，适配 ok-ap 的结构与约定」）。
- [ok-oldking/ok-script](https://github.com/ok-oldking/ok-script) —— 所依赖的框架。
- [ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves) 与
  [BnanZ0/ok-nte](https://github.com/BnanZ0/ok-nte) —— 同框架的两个成熟 3D 大世界实现，
  本项目在设计层面参考了它们的思路（**只学设计，不复制其游戏业务数据**）。

### 第三方

- [ok-oldking/OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)

## 📄 许可证

本项目采用 [AGPL-3.0](LICENSE) 许可证。
