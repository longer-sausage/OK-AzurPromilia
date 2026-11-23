---
name: ok-script-tasks
description: Create, modify, and review OK-AzurPromilia automation tasks, including BaseGameTask and TriggerTask behavior, task registration, configuration migration, UI navigation, polling, and localized task text. Use for task work in this repository.
---

# OK-AzurPromilia Tasks

## 开始前

1. 读取根目录 `AGENTS.md`，以 `pyproject.toml` 和 `uv.lock` 的锁定版本为准。
2. 阅读目标任务及相邻实现；一次性任务参考 `src/tasks/onetime/home_daily_task.py`，触发任务参考 `src/tasks/trigger/skip_dialog_task.py`。
3. 先查 `src/core/base_game_task.py`、`src/core/base_mixin/`、`src/core/detector/` 和 `src/data/page.py` 的已有能力，再决定是否新增辅助方法。

## 实现流程

1. 使用 `snake_case.py` 模块名和 `PascalCase` 类名。一次性任务继承 `BaseGameTask`；触发任务按本项目顺序继承 `BaseGameTask, TriggerTask`。
2. 在 `__init__` 中先调用 `super().__init__(*args, **kwargs)`，再设置名称、说明、图标和必要配置。优先用 `.update(...)` 扩展配置字典，保留基类默认项。
3. 将可编辑设置声明在 `default_config`，在 `config_description` 中写用途；仅在需要特定控件时添加 `config_type`。读取框架加载后的 `self.config`，不要直接改运行中的配置 JSON。
4. 为触发任务明确 `_enabled` 默认值和 `trigger_interval`。没有处理目标时及时返回；只有实际处理了事件、需要重新扫描高优先级任务时才返回真值。
5. 优先复用 `ui_ensure`、`wait_action_result` 和现有 detector；沿用目标任务的页面、特征和输入封装，避免重复实现导航或识别逻辑。
6. 在 `src/config.py` 的 `onetime_tasks` 或 `trigger_tasks` 注册真实模块路径及类名。接入一键日常时，同时查看 `src/tasks/daily/daily_feature.py` 的子任务配置与账号覆盖约定。
7. 按下述规则补齐配置迁移、翻译及相关验证，再检查最终 diff。

## 轮询与重试

- 带超时的轮询、多帧检测和重试统一用 `self.loop(...)`，保留暂停期间冻结计时的行为。
- 默认每轮产出新帧，将该帧传给支持 `frame` 的识别方法；不要在循环体无条件重复 `next_frame()`。
- 动作内部自行取帧或无需帧时，使用 `yield_frame=False`。
- 找到目标后用 `break` 或 `return` 结束；默认超时抛出 `TimeoutError`。需要兜底时使用 `raise_if_time_out=False`，并在循环后明确处理失败。
- 不另写基于墙钟时间的超时循环；具体参数和例子以 `AGENTS.md`、`BaseGameTask.loop` 为准。

## 配置与语言

- 保持已有配置键稳定，不为统一语言而重命名。确需改名时，同一提交内添加 `config_key_migrations` 并修改默认值及引用。
- 启动应用前，按 `src/core/config_migration.py` 验证旧配置值迁移；同时更新相关文档和 gettext msgid。
- 涉及多账号覆盖时，另查 `src/tasks/account/account_scope_store.py`：普通任务的迁移只处理该任务 JSON，不会自动迁移 `account_scoped_overrides.json`；须同时处理受影响的账号覆盖键并验证旧值保留。
- UI 名称、说明、配置标签和富文本说明使用 gettext；按 `$ok-script-i18n` 同步 `i18n/*/LC_MESSAGES/ok.po` 并编译 `.mo`。
- OCR 文本放入 `assets/lang/*.json`，通过 `self.lang.<模块>.<语义化key>` 读取，不放进 gettext 或任务配置充当翻译表。
- 新 OCR key 按 `AGENTS.md` 补齐 `zh_CN`、`zh_TW`、`en_US`、`ja_JP`、`ko_KR`、`es_ES`；读取接口见 `src/data/lang/`。
- 新控件元数据以锁定的框架实现和本项目补丁为依据，不照搬其他项目或不同版本的 Qt/headless 行为。

## 验证

- 从 `tests/test_*.py` 选择覆盖本次行为的测试。配置迁移和轮询改动可分别运行下列现有模块：

  ```bash
  uv run --locked python -m unittest tests.test_config_migration
  uv run --locked python -m unittest tests.test_loop
  ```

- 语言访问器或任务配置可见性改动，分别查看 `tests/test_lang_accessor.py`、`tests/test_task_config_visibility.py`。
- 新增行为缺少覆盖时补必要的回归测试；按改动风险运行完整测试：`uv run --locked python -m unittest discover -s tests -p "test_*.py"`。
- 核对注册、配置键、翻译和最终 diff；需要真实游戏窗口的行为，说明已验证范围和仍待实机验证部分。
