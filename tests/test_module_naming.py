from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")


class TestModuleNaming(unittest.TestCase):
    def test_all_src_python_files_are_lowercase_snake_case(self):
        src_dir = ROOT / "src"
        pattern = re.compile(r"^[a-z0-9_]+\.py$")
        invalid_files = []

        for py_file in src_dir.rglob("*.py"):
            if not pattern.match(py_file.name):
                invalid_files.append(str(py_file.relative_to(ROOT)))

        self.assertEqual(
            invalid_files,
            [],
            f"Found python files in src/ that do not conform to snake_case.py: {invalid_files}",
        )

    @unittest.skipUnless(GIT, "Git is required to check tracked file casing")
    def test_git_tracked_src_files_have_no_uppercase_names(self):
        result = subprocess.run(
            [GIT, "ls-files", "src"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        tracked_files = [line.strip() for line in result.stdout.splitlines() if line.strip().endswith(".py")]
        invalid_tracked = [f for f in tracked_files if any(c.isupper() for c in Path(f).name)]

        self.assertEqual(
            invalid_tracked,
            [],
            f"Git tracked files in src/ contain uppercase letters: {invalid_tracked}",
        )


if __name__ == "__main__":
    unittest.main()
