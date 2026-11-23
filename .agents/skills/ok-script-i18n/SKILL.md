---
name: ok-script-i18n
description: Add, review, or repair OK-AzurPromilia task UI translations in gettext catalogs. Use when changing task names, descriptions, config labels/options, instructions, stable runtime messages, or compiling PO/MO files; keep OCR text in the project's separate lang JSON system.
---

# OK-AzurPromilia i18n

## 工作流程

1. 先读仓库根目录 `AGENTS.md` 和目标任务；任务实现配合 `$ok-script-tasks`。
2. 收集 UI 文案：任务名称、描述、配置键/选项、配置帮助、`instructions` 与需要翻译的稳定运行时消息。
3. 列出 `i18n/*/LC_MESSAGES/ok.po`，以实际目录确定 gettext 语言范围。
4. 保留现有翻译、注释、标记和条目顺序，补齐各语言缺失的条目；`msgid` 必须与源码逐字一致，包括空白、标点和占位符。
5. 检查重复条目、空翻译、占位符和各语言遗漏，再编译 `.mo`；复核最终 `.po` / `.mo` diff。

## 使用仓库现有工具

从仓库根目录运行，依赖遵循 `pyproject.toml` / `uv.lock`：

```powershell
uv run --locked python tools/task_i18n_helper.py scan --task src/tasks/onetime/daily_task.py
uv run --locked python tools/task_i18n_helper.py check --i18n i18n
uv run --locked python tools/task_i18n_helper.py compile --i18n i18n
```

- `scan` 只提供候选：它扫描部分字面量元数据，并会去掉首尾空白。必须回到源码核对原文，补查 `self.tr(...)`、常量、下标赋值和动态构造，不能把输出当作完整或精确的 msgid 集合。
- `check` 只检查重复 msgid；它不验证空翻译、占位符或语言间的键集合。逐项检查改动涉及的这些内容，不要将退出码 0 当作完整翻译验收。
- `compile` 只生成二进制目录，不保证翻译内容正确。不要在 skill 内维护同名工具副本。

## 文案与配置

- 使用稳定模板：`self.tr("完成 {count} 项").format(count=count)`。不要把 OCR 结果、账号名、用户输入或插值后的字符串传给 `tr()`，避免污染翻译收集池。
- 保留格式占位符及格式规格；检查翻译是否仍可用原参数格式化。不要为纯日志或装饰内容机械添加翻译。
- 将 emoji、树形符号和 HTML 装饰留在代码中拼接，只翻译有语义的文本。
- 保持已持久化的配置键稳定；确需改名时，按 `AGENTS.md` 同时添加配置迁移、同步 msgid 和文档，先验证旧值迁移再启动程序。
- 保存 `.po` 时保持 UTF-8 和 LF；只处理本次修改的目录文件，避免无关的全量换行或排序改写。

## OCR 与 gettext 的边界

- UI 说明、配置标签和帮助文本使用 gettext；OCR 匹配文本放在 `assets/lang/*.json`，由 `src/data/lang/` 的访问器通过 `self.lang.<模块>.<key>` 读取。
- OCR 新 key 使用语义化命名，并遵循 `AGENTS.md` 的六种语言节点和 `string` / `pattern` 结构；不要套用其他项目的语言限制。
- 修改 OCR 数据时检查实际 JSON 和访问器，运行相关测试：

  ```powershell
  uv run --locked python -m unittest tests.test_lang_accessor
  ```

## 目录冲突处理

遇到 Git 冲突时，对照共同祖先及双方 `.po` 内容逐条合并，保留双方有效翻译；不要凭导出文件的 mtime 或整侧优先策略覆盖另一侧修改。合并文本后重新检查并编译 `.mo`，不要直接选取冲突中的旧二进制文件。
