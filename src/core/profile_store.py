"""配置档（profile）：把 ``configs/`` 下的一份快照存成命名档，随时切换。

为什么不是简单复制 ``configs/``：用户可能有几十个任务配置文件、队列和流程，
切档前必须**先备份当前状态**，否则一次误切就把正在用的配置覆盖掉了。

结构::

    configs/
    ├── profiles/
    │   ├── <档名>/
    │   │   ├── profile.json      # 元信息 + 文件清单（相对路径 + 大小）
    │   │   └── files/…           # 与 configs/ 下相对路径一致的快照
    │   └── .backup_<时间戳>/      # 应用配置档前的自动备份
    ├── DailyTask.json
    └── queues/…

约定：

- 快照范围 = ``configs/`` 下除 ``profiles/`` 以外的全部文件（递归）；
- 应用时只覆盖档里存在的文件，**不删除**档里没有的文件（避免误删用户数据），
  返回值里会如实报告``extra``（当前有、档里没有的文件数）；
- 所有写盘都是 ``.tmp`` + ``os.replace``；
- **应用失败会回滚**：先自动备份当前配置，中途出错就按备份恢复，不留半套配置；
- **保存不会毁掉已有档**：先写到 ``files.new``，全部成功后才替换旧快照；
- 档里的相对路径会做校验（拒绝绝对路径与 ``..``）——``profile.json`` 是磁盘上的文件，
  手改坏了也不能让它把文件写到 ``configs/`` 外面去。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

PROFILES_FOLDER = "profiles"
BACKUP_PREFIX = ".backup_"
META_NAME = "profile.json"
FILES_FOLDER = "files"
TEMP_FOLDER = "files.new"


class ProfileError(RuntimeError):
    """配置档操作失败。"""


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    saved_at: float
    files: int
    size: int

    @property
    def saved_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.saved_at))

    def label(self) -> str:
        return f"{self.name}  ({self.files} 个文件, {self.size // 1024} KB, {self.saved_text})"


@dataclass
class ApplyResult:
    profile: str
    restored: list[str] = field(default_factory=list)
    backup_dir: Path | None = None
    extra: int = 0

    def summary(self) -> str:
        text = f"已应用配置档 {self.profile}：恢复 {len(self.restored)} 个文件"
        if self.extra:
            text += f"，{self.extra} 个当前文件不在档里（未删除）"
        if self.backup_dir:
            text += f"，原配置备份在 {self.backup_dir.name}"
        return text


class ProfileStore:
    """配置档的保存 / 应用 / 删除。"""

    def __init__(self, config_folder: str | Path = "configs"):
        self.config_folder = Path(config_folder)
        self.profiles_root = self.config_folder / PROFILES_FOLDER

    # ── 路径 ─────────────────────────────────────────────
    def profile_dir(self, name: str) -> Path:
        safe = str(name or "").strip()
        if not safe or safe.startswith(".") or any(ch in safe for ch in '\\/:*?"<>|'):
            raise ProfileError(f"配置档名字不合法: {name!r}")
        return self.profiles_root / safe

    def _config_files(self) -> list[Path]:
        if not self.config_folder.is_dir():
            return []
        files = []
        for path in sorted(self.config_folder.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.config_folder)
            if relative.parts and relative.parts[0] == PROFILES_FOLDER:
                continue
            if relative.name.endswith(".tmp"):
                continue
            files.append(path)
        return files

    # ── 查询 ─────────────────────────────────────────────
    def list(self) -> list[ProfileInfo]:
        if not self.profiles_root.is_dir():
            return []
        infos = []
        for folder in sorted(self.profiles_root.iterdir()):
            if not folder.is_dir() or folder.name.startswith(BACKUP_PREFIX):
                continue
            meta = self._read_meta(folder)
            if meta is None:
                continue
            infos.append(
                ProfileInfo(
                    name=str(meta.get("name", folder.name)),
                    saved_at=float(meta.get("saved_at", 0.0) or 0.0),
                    files=len(meta.get("files", {}) or {}),
                    size=int(meta.get("size", 0) or 0),
                )
            )
        return infos

    def exists(self, name: str) -> bool:
        try:
            return (self.profile_dir(name) / META_NAME).is_file()
        except ProfileError:
            return False

    def _read_meta(self, folder: Path) -> dict | None:
        try:
            return json.loads((folder / META_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ── 保存 ─────────────────────────────────────────────
    def save(self, name: str, overwrite: bool = True) -> ProfileInfo:
        """把当前配置存成档。

        先写到 ``files.new``，全部成功后才替换旧快照 —— 否则中途失败（磁盘满、
        文件被占用）会把用户**已有的档**毁掉，而那是他最不该丢的东西。
        """
        target = self.profile_dir(name)
        if target.exists() and not overwrite:
            raise ProfileError(f"配置档已存在: {name}")
        target.mkdir(parents=True, exist_ok=True)
        staging = target / TEMP_FOLDER
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, dict] = {}
        total = 0
        try:
            for source in self._config_files():
                relative = source.relative_to(self.config_folder).as_posix()
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                size = destination.stat().st_size
                total += size
                manifest[relative] = {"size": size}
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ProfileError(f"保存配置档失败（原档未改动）: {exc}") from exc

        files_dir = target / FILES_FOLDER
        if files_dir.exists():
            shutil.rmtree(files_dir, ignore_errors=True)
        try:
            os.replace(staging, files_dir)
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ProfileError(f"保存配置档失败（原档未改动）: {exc}") from exc

        saved_at = time.time()
        meta = {
            "name": target.name,
            "saved_at": saved_at,
            "size": total,
            "files": manifest,
        }
        self._write_json(target / META_NAME, meta)
        return ProfileInfo(name=target.name, saved_at=saved_at, files=len(manifest), size=total)

    @staticmethod
    def _safe_relative(relative: str) -> PurePosixPath:
        """校验档里的相对路径：不许绝对路径、不许 ``..``。"""
        raw = str(relative or "").replace("\\", "/").strip()
        if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            raise ProfileError(f"配置档里有非法路径: {relative!r}")
        parts = PurePosixPath(raw).parts
        if any(part in ("..", "") for part in parts):
            raise ProfileError(f"配置档里有越界路径: {relative!r}")
        return PurePosixPath(*parts)

    # ── 应用 ─────────────────────────────────────────────
    def apply(self, name: str) -> ApplyResult:
        """把档里的配置覆盖回 ``configs/``；失败按备份回滚。"""
        target = self.profile_dir(name)
        meta = self._read_meta(target)
        if meta is None:
            raise ProfileError(f"配置档不存在或已损坏: {name}")
        files_dir = target / FILES_FOLDER

        planned = [
            (relative, self._safe_relative(relative)) for relative in (meta.get("files") or {})
        ]
        backup_dir = self._backup_current()
        restored: list[str] = []
        try:
            for relative, safe in planned:
                source = files_dir / Path(*safe.parts)
                if not source.is_file():
                    continue
                destination = self.config_folder / Path(*safe.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                tmp = destination.with_suffix(destination.suffix + ".tmp")
                shutil.copy2(source, tmp)
                os.replace(tmp, destination)
                restored.append(relative)
        except Exception as exc:
            rolled_back = self._rollback(backup_dir, restored)
            detail = "已按备份回滚" if rolled_back else "回滚也未完全成功，请检查 configs/ 与备份目录"
            raise ProfileError(f"应用配置档失败（{detail}）: {exc}") from exc

        current = {
            path.relative_to(self.config_folder).as_posix() for path in self._config_files()
        }
        extra = len(current - set(meta.get("files") or {}))
        return ApplyResult(profile=target.name, restored=restored, backup_dir=backup_dir, extra=extra)

    def _rollback(self, backup_dir: Path | None, restored: list[str]) -> bool:
        """把已经覆盖的文件按备份写回去；返回是否全部成功。"""
        if backup_dir is None:
            return False
        ok = True
        for relative in restored:
            source = backup_dir / relative
            destination = self.config_folder / relative
            try:
                if source.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                else:
                    destination.unlink(missing_ok=True)
            except OSError:
                ok = False
        return ok

    def _backup_current(self) -> Path | None:
        files = self._config_files()
        if not files:
            return None
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        backup_dir = self.profiles_root / f"{BACKUP_PREFIX}{stamp}"
        index = 0
        while backup_dir.exists():
            index += 1
            backup_dir = self.profiles_root / f"{BACKUP_PREFIX}{stamp}_{index}"
        for source in files:
            relative = source.relative_to(self.config_folder)
            destination = backup_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        return backup_dir

    # ── 删除 ─────────────────────────────────────────────
    def delete(self, name: str) -> bool:
        target = self.profile_dir(name)
        if not target.is_dir():
            return False
        shutil.rmtree(target, ignore_errors=True)
        return True

    # ── 内部 ─────────────────────────────────────────────
    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
