"""OCR 语言表（assets/lang/*.json）的完整性检查。

这类表和 `i18n/*.po` 是两回事：`.po` 管界面文案，这里管**游戏内 OCR 文本**。
少一个语种不会报错——`LangAccessor` 会回退到中文，于是日服/英语客户端上
「识别不到那个道具」但日志干净、测试全绿。

所以这里锁两件事：
1. 每个 key 都必须有 6 个语种且非空；
2. 允许的例外必须**显式登记**（`KNOWN_GAPS`）并写清原因——不能靠逃过测试来掩盖。
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANG_DIR = ROOT / "assets" / "lang"
LOCALES = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")

#: 已知缺语种的 key（(文件, key) -> 原因）。补上翻译后请一并删掉这里的条目。
KNOWN_GAPS = {
    ("home.json", "little_bobo"): "只确认了简中原文「小份波波饼」，其它语言的游戏内译名待确认",
}


def lang_files() -> list[Path]:
    return sorted(LANG_DIR.glob("*.json"))


def text_of(node: dict) -> str:
    for key in ("string", "pattern"):
        value = node.get(key)
        if value is not None:
            return str(value)
    terms = node.get("terms")
    if isinstance(terms, list):
        return " ".join(str(item) for item in terms)
    return ""


def missing_locales(path: Path) -> list[str]:
    """返回 ``key.locale`` 形式的缺口列表（``_`` 开头的注释键跳过）。"""
    problems = []
    data = json5_load(path)
    for key, node in data.items():
        if str(key).startswith("_"):
            continue  # 文件里可以写 _note 之类的说明
        if not isinstance(node, dict):
            problems.append(f"{key} (不是对象)")
            continue
        for locale in LOCALES:
            value = node.get(locale)
            if not isinstance(value, dict) or not text_of(value).strip():
                problems.append(f"{key}.{locale}")
    return problems


def json5_load(path: Path) -> dict:
    """用项目自己的 json5 读取器，保证和运行时看到的是同一份数据。"""
    try:
        import json5
    except ImportError:  # pragma: no cover - json5 是运行依赖
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open(encoding="utf-8") as handle:
        data = json5.load(handle)
    return data if isinstance(data, dict) else {}


class TestLangJsonCompleteness(unittest.TestCase):
    def test_every_key_has_all_locales(self):
        problems = []
        for path in lang_files():
            for gap in missing_locales(path):
                key = gap.split(".")[0]
                if (path.name, key) in KNOWN_GAPS:
                    continue
                problems.append(f"{path.name}: {gap}")
        self.assertEqual(
            problems,
            [],
            "OCR 语言表少了语种（会静默回退到中文，非中文客户端上识别不到）",
        )

    def test_known_gaps_still_exist(self):
        """例外登记必须指向真实存在的缺口：补上翻译后要把它删掉，否则登记会烂掉。"""
        stale = []
        for (name, key), reason in KNOWN_GAPS.items():
            path = LANG_DIR / name
            if not path.is_file():
                stale.append(f"{name} 不存在")
                continue
            gaps = missing_locales(path)
            if not any(gap.startswith(f"{key}.") for gap in gaps):
                stale.append(f"{name}:{key} 已经不缺语种了，请从 KNOWN_GAPS 删除")
            if not reason.strip():
                stale.append(f"{name}:{key} 没写原因")
        self.assertEqual(stale, [], "\n".join(stale))

    def test_locale_fallback_is_documented_behaviour(self):
        """确认运行时的回退链：缺语种会退到中文，这正是必须测完整性的原因。"""
        from src.data.lang import LangAccessor

        accessor = LangAccessor("ja_JP")
        module = accessor.home
        value = module.little_bobo
        self.assertIsNotNone(value)
        self.assertIn("波波饼", str(value))


if __name__ == "__main__":
    unittest.main()
