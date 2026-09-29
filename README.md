<div align="center">

  <img src="icons/icon.png" alt="ok-ap logo" width="160" />

  <h1>OK-AzurPromilia</h1>

  <p>QQ群 1121173471</p>

  <p>
    面向《蓝色星原：旅谣》（Azur Promilia）的图像识别游戏自动化工具，基于 <a href="https://ok-script.com/">ok-script</a> 开发。
    <br />
    Game automation tool for Azur Promilia, built with <a href="https://ok-script.com/">ok-script</a>.
  </p>

  <p>
    <img src="https://img.shields.io/badge/platform-Windows-blue" alt="平台" />
    <img src="https://img.shields.io/badge/python-3.12-skyblue" alt="Python 版本" />
    <img src="https://img.shields.io/badge/ok--script-2.0.6-orange" alt="ok-script 版本" />
    <img src="https://img.shields.io/badge/license-AGPL--3.0-green" alt="许可证" />
  </p>

  <p>
    <a href="README_en.md">English</a> | <b>简体中文</b>
  </p>

</div>

## ⚠️ 免责声明

本软件为开源、免费的外部辅助工具，仅供**个人学习与技术交流**使用，通过模拟常规用户界面与游戏交互，
旨在简化重复性操作。程序**不读取游戏内存、不修改游戏文件、不注入进程、不修改任何游戏数据**。

- **使用目的**：仅为减少重复操作，无意破坏游戏平衡，也不提供任何不公平优势。
- **风险自负**：您应充分了解并自愿承担使用本工具可能带来的所有后果，包括但不限于账号被警告、扣除收益或封禁。
- **责任范围**：因使用本软件产生的一切问题及后果，均与本项目及贡献者无关。
- **禁止商用**：请勿将本软件用于代练、售卖等任何商业或营利性目的。

## 🎮 这是什么

`ok-ap` 是面向《蓝色星原：旅谣》（Azur Promilia）的 ok-script 自动化项目。ok-script 是一套
「截图 → 图像识别 → 模拟键鼠」的游戏自动化框架，`ok-ap` 在此之上提供旅瑶这一款游戏所需要的
应用配置、任务与游戏特化实现。

## 📥 下载与安装

前往 [GitHub Releases](https://github.com/longer-sausage/OK-AzurPromilia/releases) 下载最新版本安装包：

- **中国大陆**：推荐下载 `ok-ap-win32-China-setup-*.exe`（安装包自带运行环境，更新源为国内节点，国内下载与更新更稳定）。
- **海外地区**：推荐下载 `ok-ap-win32-Global-setup-*.exe`（安装包自带运行环境，更新源为 GitHub）。

> 💡 **提示**：安装包首次运行若被 Windows Defender 拦截，点击「更多信息」并选择「仍要运行」即可。

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

## ❤️ 致谢

### 贡献者

<a href="https://github.com/longer-sausage/OK-AzurPromilia/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=longer-sausage/OK-AzurPromilia" alt="Contributors" />
</a>

### 特别感谢

- [AliceJump](https://github.com/AliceJump) —— 本仓库的工程骨架与通用能力层。
- [ok-oldking/ok-script](https://github.com/ok-oldking/ok-script) —— 所依赖的框架。

### 第三方

- [ok-oldking/OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)

## 📄 许可证

本项目采用 [AGPL-3.0](LICENSE) 许可证。
