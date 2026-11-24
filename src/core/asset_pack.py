"""素材包（模板热更新）：装一个 zip 就换掉识别素材，不用换程序。

游戏更新导致图标变化时，把新素材打成 zip 装进去即可。为了「坏包一个文件都不动」，
安装严格分四步：

1. **读清单**：``asset_pack.json`` 里记录每个文件的 SHA-256；
2. **全量校验**：逐文件算 sha256 与清单比对，任何一个缺失/不匹配/越界路径都直接中止，
   此时磁盘上的素材**没有被碰过**；
3. **解压到临时目录**：先落到 ``.asset_pack_tmp``，再逐个 ``os.replace`` 到目标位置；
4. **备份 + 回滚**：动手前把将被覆盖的文件复制到
   ``backups/asset_pack_<时间戳>/``，中途任何异常都按备份回滚。

安全边界：

- 只允许写 ``assets/`` 与 ``ok_templates/`` 两个前缀（素材包不该改代码）；
- 拒绝绝对路径、``..``、Windows 盘符与 zip 里的符号链接（zip slip）。

清单格式（zip 根部的 ``asset_pack.json``）::

    {
      "name": "ok-ap 素材包",
      "version": "2026.11.24",
      "created_at": 1764000000.0,
      "files": {"assets/images/1.png": {"sha256": "…", "size": 12345}}
    }

``files`` 也接受 ``{"路径": "sha256"}`` 的简写形式。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "asset_pack.json"
BACKUP_DIR_NAME = "backups"
ALLOWED_PREFIXES = ("assets/", "ok_templates/")
CHUNK_SIZE = 1 << 20


class AssetPackError(Exception):
    """素材包不合法（不是 zip / 没有清单 / 路径越界）。"""


@dataclass(frozen=True)
class AssetEntry:
    path: str
    sha256: str
    size: int = 0

    def to_dict(self) -> dict:
        return {"sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class AssetPackManifest:
    name: str = ""
    version: str = ""
    files: tuple[AssetEntry, ...] = ()
    created_at: float = 0.0

    def paths(self) -> list[str]:
        return [entry.path for entry in self.files]

    def by_path(self) -> dict[str, AssetEntry]:
        return {entry.path: entry for entry in self.files}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "created_at": self.created_at,
            "files": {entry.path: entry.to_dict() for entry in self.files},
        }


@dataclass
class VerifyReport:
    """校验结果。``ok`` 为真才允许安装。"""

    ok: bool = True
    checked: int = 0
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return f"校验通过：{self.checked} 个文件"
        parts = []
        if self.missing:
            parts.append(f"缺少 {len(self.missing)} 个")
        if self.mismatched:
            parts.append(f"SHA-256 不符 {len(self.mismatched)} 个")
        if self.unsupported:
            parts.append(f"路径不合法 {len(self.unsupported)} 个")
        return "校验失败：" + "，".join(parts) if parts else "校验失败"


@dataclass
class InstallReport:
    ok: bool = False
    message: str = ""
    installed: list[str] = field(default_factory=list)
    backed_up: list[str] = field(default_factory=list)
    backup_dir: Path | None = None
    verify: VerifyReport | None = None

    def summary(self) -> str:
        if self.ok:
            backup = f"，旧素材备份在 {self.backup_dir}" if self.backup_dir else ""
            return f"安装完成：{len(self.installed)} 个文件{backup}"
        return f"安装失败：{self.message}"


# ── 基础工具 ──────────────────────────────────────────────
def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(stream) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def normalize_member(name: str) -> str:
    """把 zip 成员名规范成 posix 相对路径，越界则抛 ``AssetPackError``。"""
    raw = str(name or "").replace("\\", "/").strip()
    if not raw:
        raise AssetPackError("空的文件名")
    if raw.startswith("/") or raw.startswith("//"):
        raise AssetPackError(f"绝对路径不允许: {raw}")
    if len(raw) > 1 and raw[1] == ":":
        raise AssetPackError(f"盘符路径不允许: {raw}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise AssetPackError(f"路径越界: {raw}")
    return "/".join(parts)


def is_allowed(member: str) -> bool:
    return any(member.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xF000
    return mode == 0xA000


# ── 清单 ─────────────────────────────────────────────────
def parse_manifest(data: Mapping) -> AssetPackManifest:
    if not isinstance(data, Mapping):
        raise AssetPackError("清单必须是 JSON 对象")
    raw_files = data.get("files")
    if not isinstance(raw_files, Mapping) or not raw_files:
        raise AssetPackError("清单缺少 files")
    entries: list[AssetEntry] = []
    for path, info in raw_files.items():
        member = normalize_member(path)
        if isinstance(info, Mapping):
            digest = str(info.get("sha256", "")).strip().lower()
            size = int(info.get("size", 0) or 0)
        else:
            digest = str(info or "").strip().lower()
            size = 0
        if len(digest) != 64:
            raise AssetPackError(f"清单里的 SHA-256 不合法: {member}")
        entries.append(AssetEntry(path=member, sha256=digest, size=size))
    entries.sort(key=lambda entry: entry.path)
    return AssetPackManifest(
        name=str(data.get("name", "") or ""),
        version=str(data.get("version", "") or ""),
        files=tuple(entries),
        created_at=float(data.get("created_at", 0.0) or 0.0),
    )


def read_manifest(zip_path: str | Path) -> AssetPackManifest:
    path = Path(zip_path)
    if not path.is_file():
        raise AssetPackError(f"素材包不存在: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                raw = archive.read(MANIFEST_NAME)
            except KeyError as exc:
                raise AssetPackError(f"素材包里没有 {MANIFEST_NAME}") from exc
    except zipfile.BadZipFile as exc:
        raise AssetPackError(f"不是合法的 zip: {path}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AssetPackError(f"{MANIFEST_NAME} 不是合法 JSON") from exc
    return parse_manifest(data)


def build_manifest(
    root: str | Path,
    relative_paths: Iterable[str] | None = None,
    name: str = "",
    version: str = "",
) -> AssetPackManifest:
    """按目录里的实际文件生成清单（用于打包）。"""
    base = Path(root)
    if relative_paths is None:
        relative_paths = [
            path.relative_to(base).as_posix()
            for prefix in ALLOWED_PREFIXES
            for path in sorted((base / prefix).rglob("*"))
            if path.is_file()
        ]
    entries = []
    for rel in relative_paths:
        member = normalize_member(rel)
        target = base / member
        if not target.is_file():
            raise AssetPackError(f"缺少文件: {member}")
        entries.append(AssetEntry(path=member, sha256=sha256_file(target), size=target.stat().st_size))
    entries.sort(key=lambda entry: entry.path)
    return AssetPackManifest(name=name, version=version, files=tuple(entries), created_at=time.time())


def build_pack(
    zip_path: str | Path,
    root: str | Path,
    relative_paths: Iterable[str] | None = None,
    name: str = "",
    version: str = "",
) -> AssetPackManifest:
    """把目录里的素材打成带清单的 zip（打包侧工具，测试也用它造包）。"""
    manifest = build_manifest(root, relative_paths, name=name, version=version)
    base = Path(root)
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            MANIFEST_NAME,
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        )
        for entry in manifest.files:
            archive.write(base / entry.path, entry.path)
    return manifest


# ── 校验与安装 ────────────────────────────────────────────
def verify_pack(
    zip_path: str | Path,
    manifest: AssetPackManifest | None = None,
) -> VerifyReport:
    """逐文件校验 SHA-256，不做任何写入。"""
    path = Path(zip_path)
    if manifest is None:
        manifest = read_manifest(path)
    report = VerifyReport()
    expected = manifest.by_path()

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise AssetPackError(f"不是合法的 zip: {path}") from exc

    with archive:
        present: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            try:
                member = normalize_member(info.filename)
            except AssetPackError:
                report.unsupported.append(info.filename)
                continue
            if member == MANIFEST_NAME:
                continue
            if not is_allowed(member):
                report.unsupported.append(member)
                continue
            if _is_symlink_member(info):
                report.unsupported.append(member)
                continue
            present[member] = info

        for member, entry in expected.items():
            report.checked += 1
            info = present.get(member)
            if info is None:
                report.missing.append(member)
                continue
            with archive.open(info) as stream:
                digest, _size = _sha256_stream(stream)
            if digest != entry.sha256:
                report.mismatched.append(member)

    report.ok = not (report.missing or report.mismatched or report.unsupported)
    return report


def _extract_verified(archive: zipfile.ZipFile, manifest: AssetPackManifest, target_dir: Path) -> dict[str, Path]:
    """把清单里的文件解到临时目录，返回 {member: 临时路径}。"""
    extracted: dict[str, Path] = {}
    for entry in manifest.files:
        info = None
        for candidate in archive.infolist():
            if candidate.is_dir():
                continue
            try:
                if normalize_member(candidate.filename) == entry.path:
                    info = candidate
                    break
            except AssetPackError:
                continue
        if info is None:
            raise AssetPackError(f"zip 里找不到 {entry.path}")
        destination = target_dir / entry.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(destination, "wb") as sink:
            shutil.copyfileobj(source, sink, CHUNK_SIZE)
        if sha256_file(destination) != entry.sha256:
            raise AssetPackError(f"解压后校验失败: {entry.path}")
        extracted[entry.path] = destination
    return extracted


def _timestamp_name() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


@dataclass
class _InstallState:
    """安装进度；异常中途抛出时外层仍能拿到已完成的部分用于回滚。"""

    installed: list[str] = field(default_factory=list)
    backed_up: list[str] = field(default_factory=list)
    backup_dir: Path | None = None


def _install_entries(
    extracted: Mapping[str, Path],
    app_root: Path,
    backup_root: Path,
    stamp: str,
    dry_run: bool,
    state: _InstallState,
) -> None:
    for member in sorted(extracted):
        target = app_root / member
        if target.exists():
            state.backup_dir = state.backup_dir or (backup_root / f"asset_pack_{stamp}")
            backup_target = state.backup_dir / member
            if not dry_run:
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_target)
            state.backed_up.append(member)
        if dry_run:
            state.installed.append(member)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(extracted[member], target)
        state.installed.append(member)


def _rollback(installed: Sequence[str], backup_dir: Path | None, app_root: Path) -> None:
    for member in installed:
        target = app_root / member
        backup_target = (backup_dir / member) if backup_dir else None
        try:
            if backup_target is not None and backup_target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_target, target)
            else:
                target.unlink(missing_ok=True)
        except OSError:
            pass


def install_pack(
    zip_path: str | Path,
    app_root: str | Path,
    backup_root: str | Path | None = None,
    dry_run: bool = False,
    on_installed: Callable[[InstallReport, AssetPackManifest], None] | None = None,
) -> InstallReport:
    """校验并安装素材包；校验不过时磁盘不会被修改。"""
    root = Path(app_root)
    try:
        manifest = read_manifest(zip_path)
    except AssetPackError as exc:
        return InstallReport(ok=False, message=str(exc))

    report = verify_pack(zip_path, manifest)
    if not report.ok:
        return InstallReport(ok=False, message=report.summary(), verify=report)

    root.mkdir(parents=True, exist_ok=True)
    extract_root = Path(tempfile.mkdtemp(prefix="asset_pack_", dir=str(root)))
    stamp = _timestamp_name()
    state = _InstallState()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            extracted = _extract_verified(archive, manifest, extract_root)
        backup_root_path = Path(backup_root) if backup_root is not None else (root / BACKUP_DIR_NAME)
        _install_entries(extracted, root, backup_root_path, stamp, dry_run, state)
    except Exception as exc:
        if not dry_run:
            _rollback(state.installed, state.backup_dir, root)
        return InstallReport(
            ok=False,
            message=f"安装中断已回滚: {exc}",
            installed=[],
            verify=report,
            backup_dir=state.backup_dir,
        )
    finally:
        shutil.rmtree(extract_root, ignore_errors=True)

    installed = state.installed
    backed = state.backed_up
    backup_dir = state.backup_dir

    result = InstallReport(
        ok=True,
        message=report.summary(),
        installed=installed,
        backed_up=backed,
        backup_dir=backup_dir,
        verify=report,
    )
    if backup_dir is not None and not dry_run:
        try:
            record = {
                "installed_at": time.time(),
                "stamp": stamp,
                "manifest": manifest.to_dict(),
                "installed": installed,
                "backed_up": backed,
            }
            (backup_dir / "install.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    if on_installed is not None and not dry_run:
        with contextlib.suppress(Exception):
            on_installed(result, manifest)
    return result


def download_pack(url: str, dest_dir: str | Path, timeout: float = 60.0) -> Path:
    """下载素材包到本地，返回文件路径（http/https 或本地路径都接受）。"""
    text = str(url or "").strip()
    if not text:
        raise AssetPackError("空的素材包地址")
    local = Path(text)
    if local.is_file():
        return local
    if not text.lower().startswith(("http://", "https://")):
        raise AssetPackError(f"无法识别的素材包地址: {text}")

    import urllib.parse
    import urllib.request

    name = Path(urllib.parse.urlparse(text).path).name or "asset_pack.zip"
    target = Path(dest_dir) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(text, headers={"User-Agent": "ok-ap asset pack"})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(target, "wb") as sink:
        shutil.copyfileobj(response, sink, CHUNK_SIZE)
    return target
