"""仓库卫生的静态检查：测试不能往仓库目录里写东西。

真实事故：养成的 `import_dataset` 备份默认落到仓库里的 `assets/data/backups/`，
测试用临时 target 时备份还是写进仓库，几轮下来 8 个 `growth_*.json` 被 `git add -A`
一起提交了。这类问题不会让测试变红，只是悄悄弄脏仓库。

所以这里静态扫 `tests/` 里的「用默认路径构造写盘对象」的写法：
`FailureFrameStore()` / `Ledger()` / `RunLog()` / `CheckpointStore()`
这类不给路径就会写进仓库根目录。测试里应该显式传临时目录。

（另有一条 CI 门禁：跑完测试后 `git status --porcelain` 必须为空——那条能兜住所有
这里没想到的写法。）
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

#: 构造时会写盘的类：测试里必须显式给目录（位置参数或关键字参数都行）
WRITE_ON_CONSTRUCT = (
    "FailureFrameStore",
    "CheckpointStore",
    "Ledger",
    "RunLog",
)


def test_files() -> list[Path]:
    """除本文件之外的测试（本文件的 docstring 里就写着这些类名，会被自己扫到）。"""
    return [path for path in sorted(TESTS.glob("test_*.py")) if path.name != Path(__file__).name]


def _no_argument_calls(source: str, name: str) -> list[int]:
    """找 ``Name()`` 这种**一个参数都不给**的调用（不给路径就会写进仓库根）。"""
    hits = []
    pattern = re.compile(rf"\b{name}\(")
    for match in pattern.finditer(source):
        start = match.end()
        depth = 1
        index = start
        while index < len(source) and depth:
            char = source[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        call = source[start:index - 1].strip()
        if not call:
            hits.append(source[: match.start()].count("\n") + 1)
    return hits


class TestTestsDoNotWriteToRepo(unittest.TestCase):
    def test_write_on_construct_always_gets_a_path(self):
        problems = []
        for path in test_files():
            source = path.read_text(encoding="utf-8")
            for name in WRITE_ON_CONSTRUCT:
                for line in _no_argument_calls(source, name):
                    problems.append(f"{path.name}:{line} {name}() 没有给路径")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_production_defaults_stay_out_of_the_repo(self):
        """生产代码里那些「不给路径就写仓库根」的默认值，现在只剩两处且都合理。

        这条是**记录**而不是拦截：默认值本身没错（用户在仓库里运行就该写这里），
        错的是测试用了默认值。真正的拦截靠上面那条 + CI 的 git status 门禁。
        """
        defaults = {
            "src/core/failure_frame_store.py": 'DEFAULT_FOLDER = "failure_frames"',
            "src/core/flow/executor.py": 'Path("configs") / PROGRESS_FOLDER',
        }
        for relative, snippet in defaults.items():
            with self.subTest(file=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(snippet, source)


if __name__ == "__main__":
    unittest.main()
