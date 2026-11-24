"""诊断包：把复现问题需要的东西打成一个 zip。

用户报「跑不动 / 识别不到」时，最缺的是上下文：什么配置、什么日志、哪个模板、
最后一帧长什么样、跑过几次。手工收集一轮要来回好几条消息，所以一键打包：

```text
ok-ap-diagnostics-20261124_013000.zip
├── manifest.json         # 每个文件的大小与 SHA-256（判断包有没有损坏）
├── system.json           # 平台、Python、版本、分辨率、时间
├── configs/…             # 运行配置（不含 profiles 里的大快照）
├── logs/ok-script.log    # 日志尾部（默认最多 3 个文件、每个 2MB）
├── templates.json        # 模板清单（coco categories）
├── runs.json             # 运行报表统计（只放统计，不放逐条记录）
├── ledger.json           # 记账汇总（只放收支合计，不放明细）
└── failure_frames/…      # 最新失败帧 + 索引
```

隐私相关的默认取舍：**截图默认不打包**（`include_screenshots=False`），
记账只给合计不给明细；失败帧因为已经过了 UID 遮挡处理器，可以带。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.redaction import RedactionReport, redact_config_with_report, redact_text

MANIFEST_NAME = "manifest.json"
SYSTEM_NAME = "system.json"
MAX_LOG_FILES = 3
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_FRAMES = 1


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tail_bytes(path: Path, limit: int) -> bytes | None:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            return handle.read()
    except OSError:
        return None


def _redact_config_bytes(path: Path, raw: bytes, report: RedactionReport) -> bytes:
    """把一份配置脱敏后重新序列化；JSON 坏了就原样返回（坏文件本身也是线索）。"""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        return raw
    redacted, file_report = redact_config_with_report(data)
    for key in file_report.keys:
        report.add(f"{path.name}:{key}")
    return json.dumps(redacted, ensure_ascii=False, indent=2).encode("utf-8")


@dataclass
class BundleResult:
    path: Path | None = None
    files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None

    def summary(self) -> str:
        if not self.ok:
            return f"诊断包生成失败：{self.error}"
        return f"诊断包已生成：{self.path.name}（{len(self.files)} 个文件）"


def system_info(app_root: Path, extra: dict | None = None) -> dict:
    info = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "app_root": str(app_root.resolve()),
        "cwd": os.getcwd(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    try:
        from src.config import config as app_config

        info["version"] = app_config.get("version", "")
        info["gui_title"] = app_config.get("gui_title", "")
        windows = app_config.get("windows", {}) or {}
        info["game_exe"] = windows.get("exe", [])
        info["supported_resolution"] = app_config.get("supported_resolution", {})
    except Exception as exc:  # 配置读不到不该让诊断包失败
        info["config_error"] = str(exc)
    if extra:
        info.update(extra)
    return info


def _template_catalog(app_root: Path) -> dict:
    data = _read_json(app_root / "assets" / "coco_annotations.json")
    if not isinstance(data, dict):
        return {"error": "没有 assets/coco_annotations.json"}
    categories = [item.get("name", "") for item in data.get("categories", []) if isinstance(item, dict)]
    return {"count": len(categories), "names": sorted(name for name in categories if name)}


def _run_stats(app_root: Path) -> dict:
    from src.core.ledger import RunLog

    log = RunLog(app_root / "runs.jsonl")
    rows = log.rows()
    return {
        "total_runs": len(rows),
        "stats": log.stats(),
        "per_task": _per_task_stats(rows),
    }


def _per_task_stats(rows: Iterable[dict]) -> dict:
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(str(row.get("task", "")), []).append(row)
    result = {}
    for task, items in buckets.items():
        count = len(items)
        success = sum(1 for item in items if item.get("ok"))
        seconds = sum(float(item.get("seconds", 0) or 0) for item in items)
        result[task] = {
            "count": count,
            "success": success,
            "success_rate": round(success / count, 4) if count else 0.0,
            "avg_seconds": round(seconds / count, 2) if count else 0.0,
        }
    return result


def _ledger_totals(app_root: Path) -> dict:
    from src.core.ledger import Ledger

    ledger = Ledger(app_root / "ledger.jsonl")
    try:
        totals = ledger.totals()
    except Exception as exc:
        return {"error": str(exc)}
    summary = ledger.summary()
    return {
        "totals": totals,
        "items": dict(sorted(summary.items())[:50]),
    }


def build_bundle(
    dest_dir: str | Path = ".",
    app_root: str | Path = ".",
    include_screenshots: bool = False,
    max_logs: int = MAX_LOG_FILES,
    max_frames: int = MAX_FRAMES,
    extra_system: dict | None = None,
) -> BundleResult:
    """生成诊断包，返回结果（失败不抛异常，交给调用方决定怎么提示）。"""
    root = Path(app_root)
    destination = Path(dest_dir)
    try:
        destination.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        bundle_path = destination / f"ok-ap-diagnostics-{stamp}.zip"
        result = BundleResult(path=bundle_path)

        payloads: dict[str, bytes] = {}
        payloads[SYSTEM_NAME] = json.dumps(
            system_info(root, extra_system), ensure_ascii=False, indent=2
        ).encode("utf-8")
        payloads["templates.json"] = json.dumps(
            _template_catalog(root), ensure_ascii=False, indent=2
        ).encode("utf-8")
        payloads["runs.json"] = json.dumps(_run_stats(root), ensure_ascii=False, indent=2).encode("utf-8")
        payloads["ledger.json"] = json.dumps(_ledger_totals(root), ensure_ascii=False, indent=2).encode("utf-8")

        # 配置（跳过 profiles 大快照）；**打包前脱敏**：Notification 配置里有
        # Discord Webhook / Telegram Bot Token 这类凭据，原样带走等于把权限一起送出去
        redaction = RedactionReport()
        config_dir = root / "configs"
        if config_dir.is_dir():
            for path in sorted(config_dir.glob("*.json")):
                raw = _tail_bytes(path, MAX_LOG_BYTES)
                if raw is None:
                    continue
                payloads[f"configs/{path.name}"] = _redact_config_bytes(path, raw, redaction)

        # 日志尾部（同样脱敏：DEBUG 日志会把整份配置打出来）
        logs_dir = root / "logs"
        if logs_dir.is_dir():
            logs = sorted(logs_dir.glob("ok-script*.log"), reverse=True)[: max(1, int(max_logs))]
            for path in logs:
                data = _tail_bytes(path, MAX_LOG_BYTES)
                if data is not None:
                    text, changed = redact_text(data.decode("utf-8", errors="replace"))
                    redaction.log_lines += changed
                    payloads[f"logs/{path.name}"] = text.encode("utf-8")

        # 失败帧
        frames_dir = root / "failure_frames"
        if frames_dir.is_dir():
            index_path = frames_dir / "index.json"
            index_data = _tail_bytes(index_path, MAX_LOG_BYTES)
            if index_data is not None:
                payloads["failure_frames/index.json"] = index_data
            frames = sorted(frames_dir.glob("*.png"), reverse=True)[: max(0, int(max_frames))]
            for path in frames:
                data = path.read_bytes()
                payloads[f"failure_frames/{path.name}"] = data

        # 截图（可选）
        if include_screenshots:
            shots_dir = root / "screenshots"
            if shots_dir.is_dir():
                for path in sorted(shots_dir.glob("*.png"), reverse=True)[:5]:
                    payloads[f"screenshots/{path.name}"] = path.read_bytes()

        manifest = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "app_root": str(root.resolve()),
            "include_screenshots": bool(include_screenshots),
            "redacted": redaction.to_dict(),
            "files": {
                name: {"size": len(data), "sha256": _sha256_bytes(data)}
                for name, data in sorted(payloads.items())
            },
        }
        payloads[MANIFEST_NAME] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(payloads):
                archive.writestr(name, payloads[name])
                result.files.append(name)
        return result
    except Exception as exc:
        return BundleResult(error=str(exc))
