"""导入体检：把 ``src/`` 下每个模块都 import 一遍。

回归测试大多只碰到自己关心的模块，GUI 页和补丁模块经常没人碰——
一个拼错的名字可以一直躺到用户点开那一页才炸。这个测试把整棵树都导一遍，
不需要游戏、不需要窗口（Qt 控件 import 不需要 QApplication）。
"""

import importlib
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

SKIP_PARTS = {"__pycache__"}


def iter_modules() -> list[str]:
    names = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        relative = path.relative_to(SRC_ROOT.parent).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return names


class TestImports(unittest.TestCase):
    def test_every_module_imports(self):
        modules = iter_modules()
        self.assertGreater(len(modules), 50, "src 下模块数量异常")
        failures = []
        for name in modules:
            try:
                importlib.import_module(name)
            except Exception as exc:  # 收集全部失败再一起报，避免只看到第一个
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_config_tabs_and_tasks_resolve(self):
        """config.py 里声明的每个 Tab / 任务都能真的导到类。"""
        from src.config import config

        failures = []
        for group in ("custom_tabs", "onetime_tasks", "trigger_tasks"):
            for module_name, class_name in config.get(group, []):
                try:
                    module = importlib.import_module(module_name)
                    getattr(module, class_name)
                except Exception as exc:
                    failures.append(f"{group}: {module_name}.{class_name}: {exc}")
        self.assertEqual(failures, [], "\n".join(failures))


class TestStaticReferences(unittest.TestCase):
    """字符串/属性引用对不对——这类错误只有跑到那一步才会炸。"""

    def test_action_allowlist_exists_on_base_task(self):
        """流程里能调的 action，必须在 BaseGameTask 上真的有这个方法。

        ``dodge`` 曾经在白名单里但方法不存在：用户写到这一步才会 AttributeError。
        """
        from src.core.base_game_task import BaseGameTask
        from src.core.flow.executor import ACTION_ALLOWLIST

        missing = sorted(name for name in ACTION_ALLOWLIST if not hasattr(BaseGameTask, name))
        self.assertEqual(missing, [], f"白名单里有但 BaseGameTask 没有：{missing}")

    def test_feature_list_references_exist(self):
        """``FeatureList.xxx`` 的每一处**真实代码引用**都必须存在（docstring 不算）。"""
        import ast

        from src.data.feature_list import FeatureList

        absent: list[str] = []
        for path in SRC_ROOT.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):  # 语法错误由导入体检负责抓
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "FeatureList"
                    and not hasattr(FeatureList, node.attr)
                ):
                    absent.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno} FeatureList.{node.attr}")
        self.assertEqual(absent, [], "\n".join(absent))

    def test_config_option_names_unique(self):
        """全局配置项的 key 不能重名（重名会互相覆盖，用户改了没生效）。"""
        import importlib

        from src.config import config

        keys: dict[str, str] = {}
        duplicates: list[str] = []
        modules = set()
        for group in ("configs", "global_configs"):
            for item in config.get(group, []) or []:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    modules.add((item[0], item[1]))
        for module_name, attr in modules:
            try:
                module = importlib.import_module(module_name)
                option = getattr(module, attr)
            except Exception:
                continue
            name = getattr(option, "name", "") or attr
            if name in keys:
                duplicates.append(f"{name}（{keys[name]} 与 {attr}）")
            keys[name] = attr
        self.assertEqual(duplicates, [], "\n".join(duplicates))


    def test_icons_referenced_in_code_exist(self):
        """``FluentIcon.xxx`` / ``Icons.xxx`` 必须存在（改版后图标名会被删）。

        ``FluentIcon.CALCULATOR`` 与 ``FluentIcon.HEALTH`` 都在开发过程中踩过：
        代码写得下、跑起来打开那一页才 AttributeError。
        """
        import ast

        from qfluentwidgets import FluentIcon

        from src.icons import Icons

        known = {"FluentIcon": FluentIcon, "Icons": Icons}
        absent: list[str] = []
        for path in SRC_ROOT.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in known
                    and not hasattr(known[node.value.id], node.attr)
                ):
                    absent.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno} {node.value.id}.{node.attr}")
        self.assertEqual(absent, [], "\n".join(absent))

    def test_tr_literals_are_translated(self):
        """代码里每个字面量 ``tr("...")`` 都要在 6 个语种的 .po 里。

        漏翻不会崩（tr 会退回中文），但英文/日文用户会突然看到一段中文，
        而且没人会主动去发现——所以让测试来发现。
        """
        import ast
        import re

        try:
            import polib
        except ImportError:  # pragma: no cover - 开发环境才有 polib
            self.skipTest("没有 polib")

        repo_root = SRC_ROOT.parent
        langs = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")
        catalogs = {}
        for lang in langs:
            po_path = repo_root / "i18n" / lang / "LC_MESSAGES" / "ok.po"
            catalogs[lang] = {entry.msgid for entry in polib.pofile(str(po_path))}

        cjk = re.compile(r"[\u4e00-\u9fff]")
        missing: list[str] = []
        for path in SRC_ROOT.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # obj.tr("…") / obj._tr("…") 与 直接 _tr("…") 都要查：
                # daily_summary.py 里 _tr 是局部变量，只认属性调用会漏掉整个模块。
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    name = node.func.id
                else:
                    continue
                if name not in ("tr", "_tr"):
                    continue
                for arg in node.args:
                    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                        continue
                    if not cjk.search(arg.value):
                        continue
                    for lang, msgids in catalogs.items():
                        if arg.value not in msgids:
                            missing.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno} [{lang}] {arg.value[:40]}")
        self.assertEqual(missing, [], "\n".join(missing[:20]))

    def test_daily_runner_status_words_are_translated(self):
        """``final_summary["status"]`` 的值是「先写中文、再 _tr 动态翻」的，扫不到字面量调用。

        这些词会出现在「执行情况汇总」里（日志 + txt 报告），漏翻同样是英文用户看到中文。
        """
        import ast

        try:
            import polib
        except ImportError:  # pragma: no cover
            self.skipTest("没有 polib")

        repo_root = SRC_ROOT.parent
        source = (SRC_ROOT / "tasks" / "daily" / "daily_task_runner.py").read_text(encoding="utf-8")
        statuses = set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t for t in node.targets if isinstance(t, ast.Subscript)]
            if not targets:
                continue
            key = targets[0].slice
            if not (isinstance(key, ast.Constant) and key.value == "status"):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                statuses.add(node.value.value)
        self.assertTrue(statuses, "没找到任何 status 赋值（脚本结构变了？）")

        missing = []
        for lang in ("zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"):
            msgids = {
                entry.msgid
                for entry in polib.pofile(str(repo_root / "i18n" / lang / "LC_MESSAGES" / "ok.po"))
            }
            for status in sorted(statuses):
                if status not in msgids:
                    missing.append(f"[{lang}] {status}")
        self.assertEqual(missing, [], "\n".join(missing))

    def test_translated_placeholders_match(self):
        """msgstr 里的 ``{占位符}`` 必须和 msgid 一致，否则 ``.format()`` 会 KeyError。"""
        import re

        try:
            import polib
        except ImportError:  # pragma: no cover
            self.skipTest("没有 polib")

        repo_root = SRC_ROOT.parent
        pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
        mismatches: list[str] = []
        for lang in ("zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"):
            po_path = repo_root / "i18n" / lang / "LC_MESSAGES" / "ok.po"
            for entry in polib.pofile(str(po_path)):
                if not entry.msgstr or entry.obsolete:
                    continue
                expected = set(pattern.findall(entry.msgid))
                actual = set(pattern.findall(entry.msgstr))
                if expected != actual:
                    mismatches.append(f"[{lang}] {entry.msgid[:40]}: {sorted(expected)} → {sorted(actual)}")
        self.assertEqual(mismatches, [], "\n".join(mismatches[:20]))


class TestTaskDeclarations(unittest.TestCase):
    """注册的每个任务的声明是否自洽。"""

    def _registered(self):
        from src.config import config

        pairs = []
        for group in ("onetime_tasks", "trigger_tasks"):
            for module_name, class_name in config.get(group, []) or []:
                pairs.append((group, module_name, class_name))
        return pairs

    def _module_tree(self, module_name):
        import ast

        repo_root = SRC_ROOT.parent
        path = repo_root / (module_name.replace(".", "/") + ".py")
        return ast.parse(path.read_text(encoding="utf-8"))

    def test_task_display_names_unique(self):
        """任务显示名不能重复：队列按「类名或显示名」找任务，重名会找错。"""
        import ast

        names: dict[str, list[str]] = {}
        for _group, module_name, class_name in self._registered():
            for node in ast.walk(self._module_tree(module_name)):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "name"
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        names.setdefault(node.value.value, []).append(class_name)
        duplicates = {name: classes for name, classes in names.items() if len(set(classes)) > 1}
        self.assertEqual(duplicates, {}, f"任务显示名重复：{duplicates}")

    def test_config_metadata_keys_match_defaults(self):
        """``config_description`` / ``config_type`` 的键必须都在 ``default_config`` 里。

        写错键名不会报错，只是**说明/下拉框静默失效**——用户看不到提示。
        """
        import ast

        problems: list[str] = []
        for _group, module_name, class_name in self._registered():
            tree = self._module_tree(module_name)
            defaults: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr == "default_config"
                            and isinstance(node.value, ast.Dict)
                        ):
                            defaults |= {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
            if not defaults:
                continue
            for attr in ("config_description", "config_type"):
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Assign):
                        continue
                    for target in node.targets:
                        if not (isinstance(target, ast.Attribute) and target.attr == attr):
                            continue
                        if not isinstance(node.value, ast.Dict):
                            continue
                        for key in node.value.keys:
                            if isinstance(key, ast.Constant) and key.value not in defaults:
                                problems.append(f"{class_name}.{attr} 的 {key.value!r} 不在 default_config")
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
