"""Stage removal of target paths whose exact spelling is absent from the sync source."""

import os
import subprocess
from pathlib import Path


def source_files(source: Path, items: list[str]) -> set[str]:
    files = set()
    for item in items:
        path = source / item
        if path.is_dir():
            for directory, dirs, names in os.walk(path):
                for name in names:
                    files.add((Path(directory) / name).relative_to(source).as_posix())
                for name in dirs:
                    link = Path(directory) / name
                    if link.is_symlink():
                        files.add(link.relative_to(source).as_posix())
        elif path.exists() or path.is_symlink():
            files.add(path.relative_to(source).as_posix())
    return files


def main():
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace:
        return
    source = Path(workspace).resolve()
    target = Path.cwd().resolve()
    if target.parent != source.parent or not target.name.startswith("target_"):
        return

    items = [
        line.strip().replace("\\", "/").rstrip("/")
        for line in (source / "deploy.txt").read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    actual = source_files(source, items)
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True
    ).stdout.decode("utf-8").split("\0")
    stale = [
        name for name in tracked
        if name and name not in actual
        and any(name == item or name.startswith(item + "/") for item in items)
    ]
    for start in range(0, len(stale), 100):
        subprocess.run(
            ["git", "rm", "--cached", "--ignore-unmatch", "--", *stale[start:start + 100]],
            check=True,
        )
    if stale:
        print(f"Removed {len(stale)} stale sync path(s) from the target Git index.")


if __name__ == "__main__":
    main()
