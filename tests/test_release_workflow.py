import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")


def find_bash():
    if os.name == "nt" and GIT:
        # PATH may point to WSL's bash; use the Bash bundled with Git instead.
        for parent in Path(GIT).resolve().parents:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash")


BASH = find_bash()


@unittest.skipUnless(GIT and BASH, "Git and Bash are required for release workflow tests")
class TestReleaseWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Read the actual step without adding a YAML dependency to release tests.
        lines = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8").splitlines()
        step_start = lines.index("        id: result")
        script_start = lines.index("        run: |", step_start) + 1
        script_lines = []
        for line in lines[script_start:]:
            if line and not line.startswith("          "):
                break
            script_lines.append(line)
        cls.script = textwrap.dedent("\n".join(script_lines)) + "\n"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="release-workflow-test-")
        self.directory = Path(temporary.name).resolve()
        self.assertTrue(self.directory.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temporary.cleanup)
        self.origin = self.directory / "origin.git"
        self.source = self.directory / "source"
        self.invocation = 0
        self.env = os.environ.copy()
        git_config = self.directory / "empty.gitconfig"
        git_config.write_text("", encoding="utf-8")
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=git_config.as_posix(),
            GIT_ALLOW_PROTOCOL="file",
        )
        self.env.pop("BASH_ENV", None)
        self.git("init", "--bare", "--initial-branch=main", self.origin, cwd=self.directory)
        self.git("clone", self.origin, self.source, cwd=self.directory)
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release-test@example.invalid")
        self.advance_source("Initial source")
        self.git("tag", "v0.2.1")
        self.git("push", "origin", "v0.2.1")
        self.initial_sha = self.git("rev-parse", "HEAD")

    def git(self, *args, cwd=None):
        result = subprocess.run(
            [GIT, *map(str, args)],
            cwd=cwd or self.source,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=30,
        )
        return result.stdout.strip()

    def advance_source(self, message):
        (self.source / "source.txt").write_text(message + "\n", encoding="utf-8")
        self.git("add", "source.txt")
        self.git("commit", "-m", message)
        self.git("push", "origin", "main")

    def resolve(self, *, event="workflow_dispatch", ref="main", run_id="101", attempt="1", fail_output=False):
        self.invocation += 1
        checkout = self.directory / f"checkout-{self.invocation}"
        self.git("clone", self.origin, checkout)
        output = self.directory / f"output-{self.invocation}.txt"
        if fail_output:
            output = self.directory / "missing-output-directory" / output.name
        env = self.env.copy()
        env.update(
            GITHUB_EVENT_NAME=event,
            GITHUB_REF_NAME=ref,
            GITHUB_SHA=self.initial_sha,
            GITHUB_SERVER_URL="https://github.com",
            GITHUB_REPOSITORY="release-test/fixture",
            GITHUB_RUN_ID=run_id,
            GITHUB_RUN_ATTEMPT=attempt,
            GITHUB_OUTPUT=output.as_posix(),
            MAIN_BRANCH="main",
            BUMP_TYPE="patch",
        )
        result = subprocess.run(
            [BASH, "--noprofile", "--norc", "-s"],
            input=self.script,
            cwd=checkout,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if fail_output:
            self.assertNotEqual(result.returncode, 0)
            return None
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((checkout / "INJECTED").exists())
        return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())

    def test_tag_push_preserves_shell_metacharacters_without_executing_them(self):
        for tag in ("v0.3.0-$(touch${IFS}INJECTED)", "v0.3.0-`touch${IFS}INJECTED`", 'v0.3.0-"China"'):
            with self.subTest(tag=tag):
                self.git("check-ref-format", f"refs/tags/{tag}")
                result = self.resolve(event="push", ref=tag)
                self.assertEqual(result["tag"], tag)
                self.assertEqual(result["commit"], self.initial_sha)

    def test_rerun_keeps_original_release_after_new_source_and_another_release(self):
        first = self.resolve()
        self.assertEqual(first["tag"], "v0.2.2")
        self.assertEqual(first["commit"], self.initial_sha)
        self.advance_source("Source advanced after a failed build")
        other_run = self.resolve(run_id="102")
        self.assertEqual(other_run["tag"], "v0.2.3")
        self.assertEqual(other_run["commit"], self.git("rev-parse", "HEAD"))
        tags_before = self.git("show-ref", "--tags", cwd=self.origin)

        self.assertEqual(self.resolve(attempt="2"), first)
        self.assertEqual(self.git("show-ref", "--tags", cwd=self.origin), tags_before)

    def test_retry_recovers_tag_pushed_before_output_failure(self):
        self.resolve(fail_output=True)
        self.assertEqual(self.git("rev-parse", "refs/tags/v0.2.2^{commit}", cwd=self.origin), self.initial_sha)
        tags_before = self.git("show-ref", "--tags", cwd=self.origin)
        self.advance_source("Source advanced after preparation failed")

        recovered = self.resolve(attempt="2")
        self.assertEqual(recovered["tag"], "v0.2.2")
        self.assertEqual(recovered["commit"], self.initial_sha)
        self.assertEqual(self.git("show-ref", "--tags", cwd=self.origin), tags_before)


if __name__ == "__main__":
    unittest.main()
