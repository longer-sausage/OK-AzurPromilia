"""在线仓库：填一个索引地址，就能浏览别人分享的流程 / 队列 / 轮椅方案 / 模板包并一键安装。

索引就是一个返回 JSON 的 http(s) 链接（也可以是本地文件，方便离线）：

```json
{
  "name": "社区分享站",
  "description": "大家的流程和队列",
  "entries": [
    {
      "kind": "flow",
      "name": "深渊速刷",
      "description": "两分钟一把",
      "url": "https://example.com/flows/abyss.json",
      "sha256": "…（可选，给了就校验）",
      "version": 1,
      "tags": ["刷图"]
    },
    {"kind": "template", "name": "2K 素材包", "url": "https://example.com/packs/2k.zip"}
  ]
}
```

规则：

- ``kind`` 只认 ``flow`` / ``queue`` / ``scheme`` / ``template``，各装到自己的目录；
- **同名不覆盖**：目标文件已存在就拒绝（避免别人的流程盖掉你的）；
- 给了 ``sha256`` 就校验，不匹配不落盘；
- 模板包（zip）交给 :mod:`src.core.asset_pack`，那里已经有逐文件校验 + 原子安装 + 回滚。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.asset_pack import AssetPackError, install_pack
from src.core.flow.model import flow_path
from src.core.queue.model import queue_path
from src.core.replay import scheme_path

INDEX_KINDS = ("flow", "queue", "scheme", "template")
CHUNK = 1 << 16
DOWNLOAD_DIR = Path("downloads")

#: 每种类型装到哪里
KIND_TARGETS = {
    "flow": flow_path,
    "queue": queue_path,
    "scheme": scheme_path,
}


class RepoError(ValueError):
    """索引或安装过程出错。"""


@dataclass(frozen=True)
class RepoEntry:
    kind: str
    name: str
    url: str
    description: str = ""
    sha256: str = ""
    version: int = 1
    tags: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in INDEX_KINDS:
            raise RepoError(f"不支持的条目类型 {self.kind!r}（支持 {', '.join(INDEX_KINDS)}）")
        if not self.name:
            raise RepoError("条目缺少 name")
        if not self.url:
            raise RepoError(f"条目 {self.name} 缺少 url")
        if self.sha256 and len(self.sha256) != 64:
            raise RepoError(f"条目 {self.name} 的 sha256 长度不对")

    def label(self) -> str:
        suffix = f" · {self.description}" if self.description else ""
        tags = f" [{', '.join(self.tags)}]" if self.tags else ""
        return f"{self.kind} · {self.name}{suffix}{tags}"

    def to_dict(self) -> dict:
        data = {"kind": self.kind, "name": self.name, "url": self.url}
        if self.description:
            data["description"] = self.description
        if self.sha256:
            data["sha256"] = self.sha256
        if self.version != 1:
            data["version"] = self.version
        if self.tags:
            data["tags"] = list(self.tags)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RepoEntry:
        if not isinstance(data, Mapping):
            raise RepoError("索引条目必须是对象")
        try:
            version = int(data.get("version", 1) or 1)
        except (TypeError, ValueError) as exc:
            raise RepoError("version 必须是整数") from exc
        tags = data.get("tags") or []
        if not isinstance(tags, (list, tuple)):
            raise RepoError("tags 必须是数组")
        return cls(
            kind=str(data.get("kind", "") or "").strip(),
            name=str(data.get("name", "") or "").strip(),
            url=str(data.get("url", "") or "").strip(),
            description=str(data.get("description", "") or ""),
            sha256=str(data.get("sha256", "") or "").strip().lower(),
            version=version,
            tags=tuple(str(tag) for tag in tags),
        )


@dataclass
class RepoIndex:
    name: str = ""
    description: str = ""
    entries: tuple[RepoEntry, ...] = ()
    source: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], source: str = "") -> RepoIndex:
        if not isinstance(data, Mapping):
            raise RepoError("索引顶层必须是 JSON 对象")
        raw = data.get("entries")
        if not isinstance(raw, list):
            raise RepoError("索引缺少 entries 数组")
        return cls(
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or ""),
            entries=tuple(RepoEntry.from_dict(item) for item in raw),
            source=source,
        )

    @classmethod
    def from_json(cls, text: str, source: str = "") -> RepoIndex:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise RepoError(f"索引不是合法 JSON: {exc}") from exc
        return cls.from_dict(data, source=source)

    def filter(self, kind: str | None = None, keyword: str = "") -> list[RepoEntry]:
        text = str(keyword or "").strip().lower()
        result = []
        for entry in self.entries:
            if kind and entry.kind != kind:
                continue
            if text and text not in entry.name.lower() and text not in entry.description.lower():
                continue
            result.append(entry)
        return result

    def kinds(self) -> dict[str, int]:
        summary = dict.fromkeys(INDEX_KINDS, 0)
        for entry in self.entries:
            summary[entry.kind] = summary.get(entry.kind, 0) + 1
        return summary


def load_index(source: str) -> RepoIndex:
    """从 URL 或本地文件读索引。"""
    text = str(source or "").strip()
    if not text:
        raise RepoError("索引地址为空")
    if text.lower().startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(text, timeout=30) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise RepoError(f"下载索引失败: {exc}") from exc
        return RepoIndex.from_json(payload, source=text)
    path = Path(text)
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RepoError(f"读不到本地索引: {exc}") from exc
    return RepoIndex.from_json(payload, source=str(path))


@dataclass
class InstallOutcome:
    entry: RepoEntry
    ok: bool = False
    path: Path | None = None
    message: str = ""

    def summary(self) -> str:
        if self.ok:
            return f"已安装 {self.entry.kind} · {self.entry.name} → {self.path}"
        return f"安装失败 {self.entry.kind} · {self.entry.name}：{self.message}"


def _download(entry: RepoEntry, download_dir: Path) -> Path:
    """把条目内容落到本地临时文件（http(s) 下载或本地复制）。"""
    download_dir.mkdir(parents=True, exist_ok=True)
    if entry.url.lower().startswith(("http://", "https://")):
        filename = Path(urllib.parse.urlparse(entry.url).path).name or f"{entry.name}.bin"
        target = download_dir / filename
        request = urllib.request.Request(entry.url, headers={"User-Agent": "ok-ap repo"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as handle:
                shutil.copyfileobj(response, handle, CHUNK)
        except Exception as exc:
            raise RepoError(f"下载失败: {exc}") from exc
        return target

    source = Path(entry.url)
    if not source.is_file():
        raise RepoError(f"本地文件不存在: {source}")
    target = download_dir / source.name
    shutil.copy2(source, target)
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class RepoClient:
    """按索引安装条目。"""

    app_root: Path = field(default_factory=lambda: Path("."))
    download_dir: Path = field(default_factory=lambda: DOWNLOAD_DIR)
    allow_overwrite: bool = False

    def install(self, entry: RepoEntry) -> InstallOutcome:
        try:
            downloaded = _download(entry, self.app_root / self.download_dir)
            if entry.sha256 and _sha256(downloaded) != entry.sha256:
                return InstallOutcome(entry=entry, ok=False, message="SHA-256 不匹配，未安装")
            if entry.kind == "template":
                return self._install_template(entry, downloaded)
            return self._install_json(entry, downloaded)
        except (RepoError, AssetPackError) as exc:
            return InstallOutcome(entry=entry, ok=False, message=str(exc))
        except Exception as exc:  # 兜底：安装失败不该炸调用方
            return InstallOutcome(entry=entry, ok=False, message=str(exc))

    def _target(self, entry: RepoEntry) -> Path:
        builder = KIND_TARGETS[entry.kind]
        return Path(builder(entry.name, self.app_root / "configs" / f"{entry.kind}s"))

    def _install_json(self, entry: RepoEntry, downloaded: Path) -> InstallOutcome:
        target = self._target(entry)
        if target.exists() and not self.allow_overwrite:
            return InstallOutcome(entry=entry, ok=False, message=f"同名已存在：{target.name}（不覆盖）")
        try:
            payload = downloaded.read_text(encoding="utf-8")
            json.loads(payload)
        except (OSError, ValueError) as exc:
            return InstallOutcome(entry=entry, ok=False, message=f"内容不是合法 JSON: {exc}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
        return InstallOutcome(entry=entry, ok=True, path=target)

    def _install_template(self, entry: RepoEntry, downloaded: Path) -> InstallOutcome:
        report = install_pack(downloaded, self.app_root)
        return InstallOutcome(entry=entry, ok=report.ok, path=report.backup_dir, message=report.summary())
