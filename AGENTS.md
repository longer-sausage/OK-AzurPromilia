# AGENTS.md

项目级强制规则。每次会话开始必须读取并遵守。

## 运行环境

- Python：依赖通过 [uv](https://docs.astral.sh/uv/) 管理，`uv sync` 创建仓库本地 `.venv`，
  用 `uv run python ...` 执行。`pyproject.toml` + `uv.lock` 为唯一来源，
  `requirements.txt` 为发布流水线的派生产物（`uv export --no-hashes --no-dev --output-file requirements.txt`），**勿手改**。
- **锁定的框架版本是 `ok-script==2.0.6`**（`pyproject.toml` / `uv.lock` / `requirements.txt` / `.venv` 四处一致）。
  需要离线查阅框架源码时，可解压 wheel 到本地（`python -m pip download ok-script==2.0.6 --no-deps`，
  wheel 是 `py3-none-any`，解压即全部源码）。
- 测试：`run_tests.ps1`（经 `uv run`）或 `uv run python -m unittest discover -s tests`。
  ⚠️ **不要用 `python -m ok.test.RunTests`**：它在跑完测试后会在 `ok.quit()` 处崩掉，退出码非 0。
- 日志：`logs/ok-script.log`（配置历史、任务执行、OCR 均可在此排查）；历史日志位于同目录的
  `ok-script.YYYY-MM-DD.log` 文件中。

## 代码风格

- Python 模块文件用 `snake_case.py`，`tests/` 下用 `test_*.py`；类名仍用 `PascalCase`。
- 任务类基于 ok-script：`BaseTask` / `TriggerTask`，
  **本项目通用基类为 `src/core/base_game_task.py`**（不要直接继承 `BaseTask`）。
- 任务字符串国际化：UI 文案走 `self.tr()` + `i18n/*/LC_MESSAGES/ok.po`；
  OCR 匹配文本走 `assets/lang/*.json` + `self.lang.<模块>.<key>`。两者**不要混用**。
- 新增任务后必须在 `src/config.py` 的 `onetime_tasks` / `trigger_tasks` 中注册。
- **改动本仓库代码前**：先看上面 `src/` 的目录职责表，确认这段逻辑该放哪一层哪个目录；
  不要顺手重构、不要为凑数量拆文件。若本机有 `.claude/skills/azurpromilia-project-architecture`，
  它以更细的粒度记录了目录职责、分层判断与已有能力清单（记得同步更新，见 `AGENTS.md` 顶部说明）。

## 配置键名修改（重要）

`configs/` 目录下的 JSON 是用户运行数据，修改 `default_config` 中的配置键名时必须遵守以下顺序，否则会丢失用户配置：

1. **先加迁移表，再改键名**：在同一个任务类中先添加 `config_key_migrations = {旧键: 新键}`，再修改 `default_config` / 键名常量 / 键生成函数。二者必须在同一提交中完成，禁止分步部署。
2. **迁移表生效前禁止运行程序**：改完键名后不要直接启动应用验证；先用 `migrate_config_file_keys(<任务名>, migrations)`（见 `src/core/config_migration.py`）跑迁移测试，确认旧值已复制到新键。
3. **同步 i18n**：键名变化后必须同步全部 `i18n/*/LC_MESSAGES/ok.po` 的 msgid（msgid 必须与代码键名一致），
   再编译 `.mo` 并查重：

   ```bash
   python tools/task_i18n_helper.py compile --i18n i18n   # 编译全部 .mo
   python tools/task_i18n_helper.py check --i18n i18n     # 查重复 msgid（有则退出码 1）
   python scripts/validate_all.py                         # 更严格：编译 + 查空 msgstr
   ```

4. **同步文档**：搜索 `docs/` 中出现的旧键名并更新。
5. **配置丢失可恢复**：`logs/ok-script.log` 中每行 `Config:init self.config = {...}` 保存了完整历史配置（DEBUG 级别），可从最后一次出现旧键名的记录恢复用户值。

## lang JSON key 命名约定（重要）

`assets/lang/` 下的语言 JSON 中：

- **新增 key 用语义化命名**，不要沿用旧的 `k_<md5前8位>` hash 风格。
- 每个 key 下为 6 种语言节点（`zh_CN`/`zh_TW`/`en_US`/`ja_JP`/`ko_KR`/`es_ES`），格式 `{"string": "..."}` 或 `{"pattern": "..."}`。
- 代码通过 `self.lang.<模块名>.<语义化key>` 读取，自动按当前 UI 语言选择（见 `src/data/lang/`）。
- **lang JSON 只放 OCR 匹配文本**。UI 说明（如 `instructions` 富文本）**不用 lang JSON**，改用 `self.tr("中文msgid")` 走 ok 的 gettext i18n：msgid 写入 `i18n/*/LC_MESSAGES/ok.po`（msgid 必须与代码字符串逐字一致，含全角标点/`{占位符}`），再用 `python tools/task_i18n_helper.py compile --i18n i18n` 编译 `ok.mo` 生效。
  加新文案时先用 `python tools/task_i18n_helper.py scan --task <任务文件>` 把该进 `.po` 的字符串列出来，避免漏翻。
- **最小原则**：emoji、`└─`/`├─`、HTML 标签/颜色等无需翻译的内容一律留在代码里拼，只把需翻译的纯文本放进 i18n 数据。

## 循环与重试规范（`self.loop`）

在任务或 Mixin 中进行**带超时的轮询、重试或多帧检测**时，**严禁裸写** `while self.active_time() - start < time_out:`、`while time.monotonic() < deadline:` 并手动 `self.next_frame()`，**必须统一使用 `self.loop`**（定义于 `BaseGameTask`）：

- **特性保障**：
  1. **暂停感知（Pause-aware）**：基于 `self.active_time()` 计算活跃耗时，任务被用户暂停期间计时自动冻结，避免无谓超时。
  2. **自动取帧驱动**：`yield_frame=True`（默认）时每次迭代自动调用并产出下一帧（`self.next_frame()`），循环体内无需手动取帧。若不需要帧（或动作内部自行取帧），传 `yield_frame=False`（产出 `None`）。
  3. **超时行为可控**：
     - `raise_if_time_out=True`（默认）：循环超时未提前 `break`/`return` 时抛出 `TimeoutError('Loop time out.')`。
     - `raise_if_time_out=False`：超时后正常退出循环，可在循环后执行兜底逻辑或抛出业务异常（如 `WaitFailedException`）。
     - `raise_if_time_out=ExceptionInstance` 或 `ExceptionClass`：超时直接抛出指定的自定义异常。

- **标准用法模式**：
  ```python
  # 模式 A（最常用）：默认取帧，找到目标即 break，超时自动抛 TimeoutError
  for frame in self.loop(time_out=10):
      if target := self.find_one(FeatureList.some_btn, frame=frame):
          self.click(target)
          break

  # 模式 B：超时后执行特定兜底或抛业务异常（WaitFailedException）
  for frame in self.loop(time_out=10, raise_if_time_out=False):
      if self._check_success(frame):
          return
  raise WaitFailedException("操作超时")

  # 模式 C：不需要自动取帧的时间控制循环
  for _ in self.loop(time_out=10, yield_frame=False, raise_if_time_out=False):
      if self.try_step():
          return
  ```

