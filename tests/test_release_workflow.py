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
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def workflow_step_script(step_id):
    # Read the actual step without adding a YAML dependency to release tests.
    lines = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8").splitlines()
    step_start = lines.index(f"        id: {step_id}")
    script_start = lines.index("        run: |", step_start) + 1
    script_lines = []
    for line in lines[script_start:]:
        if line and not line.startswith("          "):
            break
        script_lines.append(line)
    return textwrap.dedent("\n".join(script_lines)) + "\n"


@unittest.skipUnless(GIT and BASH, "Git and Bash are required for release workflow tests")
class TestReleaseWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = workflow_step_script("result")

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


@unittest.skipUnless(os.name == "nt" and GIT and POWERSHELL, "Windows, Git and PowerShell are required")
class TestReleaseSyncWorkflow(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="release-sync-test-")
        self.directory = Path(temporary.name).resolve()
        self.assertTrue(self.directory.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temporary.cleanup)
        self.source = self.directory / "job" / "source"
        self.source.mkdir(parents=True)
        git_config = self.directory / "empty.gitconfig"
        git_config.write_text("", encoding="utf-8")
        github_env = self.directory / "github_env.txt"
        github_env.write_text("", encoding="utf-8")
        self.env = os.environ.copy()
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=git_config.as_posix(),
            GIT_ALLOW_PROTOCOL="file",
            GITHUB_WORKSPACE=str(self.source),
            GITHUB_ENV=str(github_env),
        )
        script = self.directory / "prepare_sync.ps1"
        script.write_text(workflow_step_script("case_sensitive_sync"), encoding="utf-8")
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=self.source,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Apply the emitted environment exactly as the runner does for subsequent steps.
        self.env.update(
            dict(line.split("=", 1) for line in github_env.read_text(encoding="utf-8-sig").splitlines() if line)
        )
        source_file = self.source / "src" / "interaction" / "key.py"
        source_file.parent.mkdir(parents=True)
        source_file.write_text("unchanged content\n", encoding="utf-8")
        self.assertFalse(source_file.with_name("Key.py").exists())

        self.seed = self.directory / "seed"
        self.git("init", "--initial-branch=main", self.seed)
        self.git("config", "user.name", "Release Sync Test", cwd=self.seed)
        self.git("config", "user.email", "release-sync-test@example.invalid", cwd=self.seed)
        old_file = self.seed / "src" / "interaction" / "Key.py"
        old_file.parent.mkdir(parents=True)
        old_file.write_text(source_file.read_text(encoding="utf-8"), encoding="utf-8")
        (self.seed / "retained.txt").write_text("outside sync list\n", encoding="utf-8")
        self.git("add", ".", cwd=self.seed)
        self.git("commit", "-m", "Legacy file casing", cwd=self.seed)

    def git(self, *args, cwd=None):
        result = subprocess.run(
            [GIT, *map(str, args)],
            cwd=cwd or self.directory,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=30,
        )
        return result.stdout.strip()

    def sync(self):
        origin = self.directory / "origin.git"
        target = self.source.parent / "target_update"
        self.git("clone", "--bare", self.seed, origin)
        self.git("clone", origin, target)
        # Some Windows runners persist this during clone, overriding the global setting.
        self.git("config", "--local", "core.ignorecase", "true", cwd=target)
        self.assertEqual(self.git("config", "--local", "--get", "core.ignorecase", cwd=target), "true")
        self.assertEqual(self.git("config", "--get", "core.ignorecase", cwd=target), "false")
        # partial-sync-repo replaces each whitelisted directory, then runs git add .
        self.assertTrue((target / "src").resolve().is_relative_to(self.directory))
        shutil.rmtree(target / "src")
        shutil.copytree(self.source / "src", target / "src")
        self.git("add", ".", cwd=target)
        self.assertEqual(self.git("ls-files", "src", cwd=target), "src/interaction/key.py")
        self.assertFalse((target / "src" / "interaction" / "Key.py").exists())
        self.assertEqual((target / "retained.txt").read_text(encoding="utf-8"), "outside sync list\n")
        changes = self.git("diff", "--cached", "--name-status", "-M", cwd=target)
        self.git("config", "user.name", "Release Sync Test", cwd=target)
        self.git("config", "user.email", "release-sync-test@example.invalid", cwd=target)
        self.git("commit", "-m", "Correct file casing", cwd=target)
        self.git("push", "origin", "main", cwd=target)
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD", "src", cwd=origin), "src/interaction/key.py")
        return changes

    def test_sync_records_case_only_rename_with_unchanged_content(self):
        self.assertEqual(self.sync(), "R100\tsrc/interaction/Key.py\tsrc/interaction/key.py")

    def test_sync_repairs_duplicate_case_entries_in_existing_repo(self):
        blob = self.git("rev-parse", "HEAD:src/interaction/Key.py", cwd=self.seed)
        self.git("update-index", "--add", "--cacheinfo", "100644", blob, "src/interaction/key.py", cwd=self.seed)
        self.git("commit", "-m", "Corrupted duplicate case entries", cwd=self.seed)
        self.assertEqual(self.sync(), "D\tsrc/interaction/Key.py")


if __name__ == "__main__":
    unittest.main()
