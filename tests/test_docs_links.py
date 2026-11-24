"""文档一致性测试：链接、锚点、导航覆盖、以及文档里点名的测试文件是否真的存在。

文档腐化很难靠人眼发现：改了文件名、删了测试、搬了章节，链接照样显示成可点的蓝色文字，
点下去才发现 404。这几条检查让 CI 直接红。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "docs"

LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
TEST_PATH = re.compile(r"`(tests/[0-9A-Za-z_/]+\.py)`")


def all_docs(root: Path = ROOT) -> list[Path]:
    return sorted([*root.glob("*.md"), *(root / "docs").rglob("*.md")])


def broken_links(root: Path = ROOT) -> list[str]:
    missing = []
    for path in all_docs(root):
        for _text, target in LINK.findall(path.read_text(encoding="utf-8")):
            target = target.strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            if not (path.parent / path_part).resolve().exists():
                missing.append(f"{path.relative_to(root).as_posix()} -> {target}")
    return missing


def broken_anchors(root: Path = ROOT) -> list[str]:
    bad = []
    for path in all_docs(root):
        for _text, target in LINK.findall(path.read_text(encoding="utf-8")):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:")) or "#" not in target:
                continue
            path_part, anchor = target.split("#", 1)
            if not anchor:
                continue
            target_file = (path.parent / path_part).resolve() if path_part else path
            if not target_file.exists():
                continue  # 由 broken_links 负责报
            if anchor not in anchors_of(target_file):
                bad.append(f"{path.relative_to(root).as_posix()} -> {target}")
    return bad


def slugify(text: str) -> str:
    """按 GitHub 的规则把标题转成锚点（保留中文，去标点，空格转连字符）。"""
    text = text.strip().lower()
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", text)
    return text.replace(" ", "-")


def anchors_of(path: Path) -> set[str]:
    result = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(line)
        if match:
            result.add(slugify(match.group(2)))
    return result


class TestDocLinks(unittest.TestCase):
    def test_relative_links_resolve(self):
        self.assertEqual(broken_links(), [], "文档里有指向不存在文件的链接")

    def test_anchors_exist(self):
        self.assertEqual(broken_anchors(), [], "文档里有指向不存在标题的锚点链接")

    def test_checker_detects_problems(self):
        """正控：故意造一份坏文档，确认检查器真的能抓到（不然测试永远绿）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "good.md").write_text("# 标题\n\n## 小节\n", encoding="utf-8")
            (root / "README.md").write_text(
                "[好](docs/good.md)\n[坏](docs/missing.md)\n"
                "[锚点好](docs/good.md#小节)\n[锚点坏](docs/good.md#不存在)\n",
                encoding="utf-8",
            )
            self.assertEqual(broken_links(root), ["README.md -> docs/missing.md"])
            self.assertEqual(broken_anchors(root), ["README.md -> docs/good.md#不存在"])

    def test_referenced_test_files_exist(self):
        """文档/README 里用反引号点名的 tests/*.py 必须真的存在（改名后别忘了改文档）。"""
        missing = []
        for path in all_docs():
            for relative in TEST_PATH.findall(path.read_text(encoding="utf-8")):
                if not (ROOT / relative).is_file():
                    missing.append(f"{path.relative_to(ROOT).as_posix()} -> {relative}")
        self.assertEqual(missing, [], "文档引用了不存在的测试文件")

    def test_index_lists_every_dev_doc(self):
        index = (DOCS_ROOT / "index.md").read_text(encoding="utf-8")
        linked = {
            (DOCS_ROOT / target.split("#", 1)[0]).resolve()
            for _text, target in LINK.findall(index)
            if not target.startswith(("http://", "https://", "mailto:"))
        }
        unlisted = [
            path.relative_to(ROOT).as_posix()
            for path in sorted((DOCS_ROOT / "dev").glob("*.md"))
            if path.resolve() not in linked
        ]
        self.assertEqual(unlisted, [], "docs/index.md 的快速导航漏了这些文档")


if __name__ == "__main__":
    unittest.main()
