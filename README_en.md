<div align="center">

  <img src="icons/icon.png" alt="ok-ap logo" width="160" />

  <h1>ok-ap</h1>

  <p>
    An image-recognition automation tool for Azur Promilia, built with <a href="https://ok-script.com/">ok-script</a>.
    <br />
    面向《蓝色星原：旅谣》（Azur Promilia）的图像识别游戏自动化工具。
  </p>

  <p><i>Operates by simulating Windows user interface input: no memory reading, no file modification.</i></p>

  <p>
    <img src="https://img.shields.io/badge/platform-Windows-blue" alt="Platform" />
    <img src="https://img.shields.io/badge/python-3.12-skyblue" alt="Python version" />
    <img src="https://img.shields.io/badge/ok--script-2.0.6-orange" alt="ok-script version" />
    <img src="https://img.shields.io/badge/license-AGPL--3.0-green" alt="License" />
    <img src="https://img.shields.io/badge/status-WIP-orange" alt="Project status" />
  </p>

  <p>
    <b>English</b> | <a href="README.md">简体中文</a>
  </p>

  <p>
    <a href="#run-from-source">🚀 Run from Source</a> ·
    <a href="docs/dev/development.md">📖 Development Guide（中文）</a> ·
    <a href="docs/index.md">📚 Project Docs（中文）</a> ·
    <a href="https://github.com/longer-sausage/OK-AzurPromilia">⭐ Star this Project</a>
  </p>

</div>

---

> 📌 **Project status: the application skeleton is in place, but the game-specific content has not been developed against the real game yet.**
>
> What this repository currently offers is **a runnable ok-script application project plus a general-purpose capability layer**
> (configuration, task registration, keyboard and mouse interaction, image recognition, multi-account support,
> i18n, testing and release pipelines). This part of the code is real, runnable, and covered by tests.
>
> However, **almost nothing on the game side has been written yet**: the window parameters, UI templates,
> the key map, the “am I on the main screen?” check, and the daily / combat / navigation tasks are
> **all either pending in-game verification or not started**.
>
> **So at this stage it is not yet something you can use to automate the game.** Please approach it as a
> “run from source + contribute” project. See [docs/index.md](docs/index.md) for progress and known technical debt（中文）.

## ⚠️ Disclaimer

This software is an open-source, free external companion tool, intended **only for personal learning and
technical exchange**. It interacts with the game by simulating ordinary user-interface input, in order to
simplify repetitive operations. It does **not read game memory, does not modify game files, does not inject
into any process, and does not alter any game data**.

- **Purpose**: to reduce repetitive operations only. It is not intended to break game balance, nor to provide any unfair advantage.
- **At your own risk**: you should fully understand and voluntarily accept all consequences of using this tool, including but not limited to account warnings, removal of in-game rewards, or bans.
- **Liability**: any problem or consequence arising from the use of this software is unrelated to this project and its contributors.
- **Non-commercial**: do not use this software for boosting services, resale, or any other commercial or for-profit purpose.

<!-- TODO (before release): quote and link the official Azur Promilia user agreement / fair-play statement
     regarding third-party tools here, following the disclaimer sections of ok-ww and ok-nte. -->

## 🎮 What is this

`ok-ap` is an ok-script automation project for **Azur Promilia**. ok-script is a game automation framework
built around “screenshot → image recognition → simulated keyboard and mouse input”. On top of it, `ok-ap`
provides the application configuration, tasks, and game-specific implementation that this particular game requires.

## 📥 Download & Install

> **No stable release has been published yet.** The release pipeline (`release` + `build`)
> is already in place; the installers will be published here once the first release is out. Until then,
> please [run from source](#run-from-source).

## 🖥️ Requirements

- OS: Windows (x64).
- Privileges: start your terminal / PyCharm / VSCode **as Administrator** (required for the simulated keyboard and mouse input).
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

## 🔗 Projects built on ok-script

- Wuthering Waves [ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves)
- Neverness to Everness [BnanZ0/ok-nte](https://github.com/BnanZ0/ok-nte)
- Arknights: Endfield [AliceJump/ok-end-field](https://github.com/AliceJump/ok-end-field)
- Genshin Impact (unmaintained) [ok-oldking/ok-genshin-impact](https://github.com/ok-oldking/ok-genshin-impact)
- Girls' Frontline 2 [ok-oldking/ok-gf2](https://github.com/ok-oldking/ok-gf2)
- Honkai: Star Rail [Shasnow/ok-starrailassistant](https://github.com/Shasnow/ok-starrailassistant)
- Star Resonance [Sanheiii/ok-star-resonance](https://github.com/Sanheiii/ok-star-resonance)
- Duet Night Abyss [BnanZ0/ok-duet-night-abyss](https://github.com/BnanZ0/ok-duet-night-abyss)
- Bai Jing Hui Lang (unmaintained) [ok-oldking/ok-baijing](https://github.com/ok-oldking/ok-baijing)

## ❤️ Acknowledgements

### Contributors

<a href="https://github.com/longer-sausage/OK-AzurPromilia/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=longer-sausage/OK-AzurPromilia" alt="Contributors" />
</a>

### Special thanks

- [AliceJump](https://github.com/AliceJump) — the application skeleton and the general-purpose capability layer
  (application configuration, base class and mixins, interaction layer, image algorithms, multi-account support,
  orchestration, CI and release pipelines under `src/`) were synced from their ok-script project **ok-ap**
  (several modules are documented there as “ported from ok-gf2, adapted to ok-ap's structure and conventions”).
- [ok-oldking/ok-script](https://github.com/ok-oldking/ok-script) — the framework this project depends on.
- [ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves) and
  [BnanZ0/ok-nte](https://github.com/BnanZ0/ok-nte) — two mature 3D open-world implementations on the same
  framework. This project references their **design** only (**never their game-specific data**).

### Third-party

- [ok-oldking/OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)

## 📄 License

This project is licensed under [AGPL-3.0](LICENSE).
