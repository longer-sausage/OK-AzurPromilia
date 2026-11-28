<div align="center">

  <img src="icons/icon.png" alt="ok-ap logo" width="160" />

  <h1>ok-ap</h1>

  <p>QQ Group 1121173471</p>

  <p>
    An image-recognition automation tool for Azur Promilia, built with <a href="https://ok-script.com/">ok-script</a>.
    <br />
    面向《蓝色星原：旅谣》（Azur Promilia）的图像识别游戏自动化工具。
  </p>

  <p>
    <img src="https://img.shields.io/badge/platform-Windows-blue" alt="Platform" />
    <img src="https://img.shields.io/badge/python-3.12-skyblue" alt="Python version" />
    <img src="https://img.shields.io/badge/ok--script-3.0.14-orange" alt="ok-script version" />
    <img src="https://img.shields.io/badge/license-AGPL--3.0-green" alt="License" />
  </p>

  <p>
    <b>English</b> | <a href="README.md">简体中文</a>
  </p>

</div>

## ⚠️ Disclaimer

This software is an open-source, free external companion tool, intended **only for personal learning and
technical exchange**. It interacts with the game by simulating ordinary user-interface input, in order to
simplify repetitive operations. It does **not read game memory, does not modify game files, does not inject
into any process, and does not alter any game data**.

- **Purpose**: to reduce repetitive operations only. It is not intended to break game balance, nor to provide any unfair advantage.
- **At your own risk**: you should fully understand and voluntarily accept all consequences of using this tool, including but not limited to account warnings, removal of in-game rewards, or bans.
- **Liability**: any problem or consequence arising from the use of this software is unrelated to this project and its contributors.
- **Non-commercial**: do not use this software for boosting services, resale, or any other commercial or for-profit purpose.

## 🎮 What is this

`ok-ap` is an ok-script automation project for **Azur Promilia**. ok-script is a game automation framework
built around “screenshot → image recognition → simulated keyboard and mouse input”. On top of it, `ok-ap`
provides the application configuration, tasks, and game-specific implementation that this particular game requires.

### Character management and user data

With the party portraits visible in the game, open **Character Management** and select **Capture**.
Choose **Edit** on a slot, select a character with a dedicated class, and save. Unrecognized portraits use the
base class (`BaseChar`). Select **Default** to restore that binding. Multiple portraits can share one character
across UI languages and party positions.
To back up character templates, keep the PNG files and `bindings.json` together in `./user_data/char/`.

`./user_data/` holds character templates, per-account overrides, and daily reports created by this project.
ok-script stores task and UI settings in `configs/`, logs in `logs/`, and diagnostic screenshots in `screenshots/` at the installation root.

## 📥 Download & Install

Installers and portable archives are available on [GitHub Releases](https://github.com/longer-sausage/ok-ap/releases).

- **Mainland China**: Recommended to download `ok-ap-win32-China-setup-*.exe` (bundled runtime environment, uses domestic mirror for faster and more stable updates).
- **Global**: Recommended to download `ok-ap-win32-Global-setup-*.exe` (bundled runtime environment, uses GitHub update source).
- **Portable**: Choose `ok-ap-win32-China-portable-*.7z` in mainland China or `ok-ap-win32-Global-portable-*.7z` elsewhere. Both include Python and dependencies. Extract the entire archive and double-click `ok-ap\ok-ap.cmd`.

> 💡 **Note**: Windows Defender may block the installer on first run — click "More info" and select "Run anyway".

## 🖥️ Requirements

- OS: Windows (x64).
- Privileges: start your terminal / PyCharm / VSCode **as Administrator** (required for simulated keyboard and mouse input).
- Running from source: **Python 3.12** (this version only). Dependencies are managed with [uv](https://docs.astral.sh/uv/).
- Game resolution, in-game filters and game language — **[unverified]**, not yet confirmed on a real client; this section will be filled in once confirmed.

<a id="run-from-source"></a>

## 🚀 Run from Source

Dependencies are managed with [uv](https://docs.astral.sh/uv/) (install uv first), and **only Python 3.12 is supported**.

```bash
# Create the virtual environment and install / update dependencies
uv sync

# Run the Release build
uv run python main.py

# Run the Debug build (more recognition output — use this while developing recognition logic)
uv run python main_debug.py
```

## ⌨️ Command-line Arguments

The arguments are defined in the framework at `ok/util/process.py:571`:

```pwsh
# Start, automatically run task #1, and exit when it finishes
ok-ap.exe -t 1 -e
```

| Argument | Description |
| --- | --- |
| `-t <index>` | Automatically run the Nth task on startup. The index refers to the position in the `onetime_tasks` list in `src/config.py` (**starting from 1**). **Only accepts an index, not a task name.** |
| `-e` | Exit automatically after the task finishes. |
| `-h` | Headless mode, no UI is started. **Note: this is not `--help`.** |
| `--help` | Show help. |

## 🧪 Development & Tests

```bash
# Run every test under tests/ (PowerShell, invokes uv run per file)
./run_tests.ps1

# Or run the whole test directory directly
uv run python -m unittest discover -s tests
```

## ❤️ Acknowledgements

### Contributors

<a href="https://github.com/longer-sausage/ok-ap/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=longer-sausage/ok-ap" alt="Contributors" />
</a>

### Special thanks

- [AliceJump](https://github.com/AliceJump) — the application skeleton and the general-purpose capability layer.
- [ok-oldking/ok-script](https://github.com/ok-oldking/ok-script) — the framework this project depends on.

### Third-party

- [ok-oldking/OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)

## 📄 License

This project is licensed under [AGPL-3.0](LICENSE).
