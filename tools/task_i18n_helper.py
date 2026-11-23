"""任务 i18n 辅助工具：扫描任务类里需要翻译的字符串、编译 .mo、检查重复 msgid。

本仓库的 i18n 有两条互不混用的链路（见 AGENTS.md）：
  - **UI 文案**：`self.tr("中文msgid")` → `i18n/<locale>/LC_MESSAGES/ok.po` → 编译为 `ok.mo`
  - **OCR 匹配文本**：`assets/lang/<模块>.json`（本工具不处理这一条）

三个子命令：

    # 1. 扫描一个任务文件，列出所有应进 ok.po 的字符串（msgid 候选）
    python tools/task_i18n_helper.py scan --task src/tasks/onetime/daily_task.py

    # 2. 把 i18n/ 下全部 ok.po 编译成 ok.mo
    python tools/task_i18n_helper.py compile --i18n i18n

    # 3. 检查各 ok.po 里有没有重复 msgid（有则退出码 1）
    python tools/task_i18n_helper.py check --i18n i18n

`scan` 会读取任务类的 `self.name` / `self.description` / `self.default_config` /
`self.config_description` / `self.config_type`，把其中的中文字符串抽出来 —— 这些就是该写进
`ok.po` 的 msgid（msgid 必须与代码里的字符串逐字一致）。

来源说明：本文件来自 **ok-script-app 模板**自带的 `.agents/skills/ok-script-i18n/scripts/task_i18n_helper.py`
（与本机 `test/ok-script-app/` 下的副本逐字节相同）。它随整个 `.agents/skills/` 目录在提交 `dbb0da6`
（"移除skill"）被删除，但 `AGENTS.md` 里对它的引用没有同步清理，导致文档指向一个不存在的文件。
现从 Git 历史 `b49de59` 原样恢复，仅新增本 docstring，放到与其它 i18n 工具
（`gen_lang_stubs.py` / `repair_po_locales.py`）同级的 `tools/` 下。

与模板副本的差异：**逻辑代码逐行一致**，只多了本 docstring 和 `--help` 文本。
"""
import argparse
import ast
import os
from collections import Counter

import polib


TASK_DICT_ATTRS = {"default_config", "config_description", "config_type"}
TASK_STRING_ATTRS = {"name", "description"}
CONFIG_TYPE_META = {
    "type",
    "options",
    "buttons",
    "drop_down",
    "multi_selection",
    "global",
    "text_edit",
    "button",
}


class TaskStringVisitor(ast.NodeVisitor):
    def __init__(self):
        self.strings = []

    def visit_Assign(self, node):
        for target in node.targets:
            self._collect_assignment(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._collect_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node):
        attr = node.func
        if isinstance(attr, ast.Attribute) and attr.attr == "update":
            if self._is_self_attr_in(attr.value, {"default_config", "config_description"}):
                for arg in node.args:
                    self._collect_dict_strings(arg)
            elif self._is_self_attr_in(attr.value, {"config_type"}):
                for arg in node.args:
                    self._collect_config_type_strings(arg)
        self.generic_visit(node)

    def _collect_assignment(self, target, value):
        if self._is_self_attr_in(target, TASK_STRING_ATTRS):
            self._add_string(value)
        elif self._is_self_attr_in(target, {"default_config", "config_description"}):
            self._collect_dict_strings(value)
        elif self._is_self_attr_in(target, {"config_type"}):
            self._collect_config_type_strings(value)

    def _is_self_attr_in(self, node, attrs):
        return (
            isinstance(node, ast.Attribute)
            and node.attr in attrs
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )

    def _add_string(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if value:
                self.strings.append(value)

    def _collect_dict_strings(self, node):
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values):
            self._add_string(key)
            self._collect_value_strings(value)

    def _collect_value_strings(self, node):
        self._add_string(node)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_value_strings(item)
        elif isinstance(node, ast.Dict):
            self._collect_dict_strings(node)

    def _collect_config_type_strings(self, node):
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values):
            self._add_string(key)
            self._collect_config_type_value(value)

    def _collect_config_type_value(self, node):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in {"options", "buttons"}:
                    self._collect_value_strings(value)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_config_type_value(item)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in CONFIG_TYPE_META:
            self._add_string(node)


def scan_task(path):
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    visitor = TaskStringVisitor()
    visitor.visit(tree)
    for value in dict.fromkeys(visitor.strings):
        print(value)


def iter_po_paths(i18n_dir):
    for root, _, files in os.walk(i18n_dir):
        if "ok.po" in files:
            yield os.path.join(root, "ok.po")


def compile_i18n(i18n_dir):
    for po_path in iter_po_paths(i18n_dir):
        mo_path = os.path.join(os.path.dirname(po_path), "ok.mo")
        po = polib.pofile(str(po_path))
        po.save_as_mofile(mo_path)
        print(f"compiled {po_path} -> {mo_path}")


def check_i18n(i18n_dir):
    failed = False
    for po_path in iter_po_paths(i18n_dir):
        po = polib.pofile(str(po_path))
        ids = [entry.msgid for entry in po if entry.msgid]
        duplicates = sorted(msgid for msgid, count in Counter(ids).items() if count > 1)
        if duplicates:
            failed = True
            print(f"duplicate msgid entries in {po_path}:")
            for msgid in duplicates:
                print(msgid)
        else:
            print(f"ok {po_path}")
    if failed:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(
        description="任务 i18n 辅助工具（扫描 / 编译 .mo / 检查重复 msgid）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="扫描任务文件里的待翻译字符串")
    scan.add_argument("--task", required=True, help="任务 .py 文件路径")

    compile_cmd = subparsers.add_parser("compile", help="把 ok.po 编译成 ok.mo")
    compile_cmd.add_argument("--i18n", default="i18n", help="i18n 目录（默认 i18n）")

    check_cmd = subparsers.add_parser("check", help="检查重复 msgid")
    check_cmd.add_argument("--i18n", default="i18n", help="i18n 目录（默认 i18n）")

    args = parser.parse_args()

    if args.command == "scan":
        scan_task(args.task)
    elif args.command == "compile":
        compile_i18n(args.i18n)
    elif args.command == "check":
        check_i18n(args.i18n)


if __name__ == "__main__":
    main()
