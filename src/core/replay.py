"""轮椅方案：把几段录制串成一套固定流程，按顺序回放。

「轮椅」= 重复度极高的固定操作。它不需要新的执行引擎 —— 录制段就是流程步骤
（见 :func:`src.core.flow.recording.convert_recording`），所以方案只是
**把若干段步骤拼起来**，然后交给 ``FlowExecutor`` 跑：

```json
{
  "name": "深渊轮椅",
  "loop": 2,
  "between_wait": 0.5,
  "segments": [
    {"label": "进场", "events": [ ... 录制事件 ... ]},
    {"label": "输出循环", "events": [ ... ]}
  ]
}
```

拼接规则：

- 段与段之间插入 ``between_wait`` 秒的 ``wait``（默认 0.5s，0 表示不留缝）；
- ``loop`` > 1 时整串步骤会包进一个 ``loop`` 步骤（受流程的 1000 次上限约束）；
- 每段转换时都会统计跳过的输入（右键/滚轮等），汇总在 ``SchemeReport`` 里；
- 拼接后的流程同样要通过 ``validate_flow``（500 步上限），超了直接报错而不是硬跑。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.flow import (
    MAX_LOOP_TIMES,
    Flow,
    convert_recording,
    parse_flow,
    save_flow,
    validate_flow,
)

SCHEME_FOLDER = Path("configs") / "schemes"
DEFAULT_BETWEEN_WAIT = 0.5

_SAFE_NAME = re.compile(r"[^0-9A-Za-z_.\u4e00-\u9fff-]+")


class SchemeFormatError(ValueError):
    """方案文件不合法。"""


@dataclass
class SchemeSegment:
    label: str = ""
    events: tuple[dict, ...] = ()
    #: 直接给步骤（已经是流程步骤时用，优先于 events）
    steps: tuple[dict, ...] = ()

    def source_label(self) -> str:
        return self.label or "录制段"


@dataclass
class SchemeReport:
    segments: int = 0
    converted: int = 0
    skipped: int = 0
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = f"{self.segments} 段 / {self.converted} 步"
        if self.skipped:
            text += f"，跳过 {self.skipped} 个无法表达的输入"
        if self.truncated:
            text += "，已按上限截断"
        return text


@dataclass
class Scheme:
    name: str = "新方案"
    description: str = ""
    loop: int = 1
    between_wait: float = DEFAULT_BETWEEN_WAIT
    segments: tuple[SchemeSegment, ...] = ()
    source: str = ""

    # ── 载入 ─────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Scheme:
        if not isinstance(data, Mapping):
            raise SchemeFormatError("方案顶层必须是 JSON 对象")
        raw = data.get("segments")
        if not isinstance(raw, list) or not raw:
            raise SchemeFormatError("方案缺少 segments")
        segments = []
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise SchemeFormatError(f"segments[{index}] 必须是对象")
            events = item.get("events") or []
            steps = item.get("steps") or []
            if not isinstance(events, list) or not isinstance(steps, list):
                raise SchemeFormatError(f"segments[{index}] 的 events/steps 必须是数组")
            segments.append(
                SchemeSegment(
                    label=str(item.get("label", "") or ""),
                    events=tuple(event for event in events if isinstance(event, Mapping)),
                    steps=tuple(step for step in steps if isinstance(step, Mapping)),
                )
            )
        raw_loop = data.get("loop", 1)
        try:
            loop = 1 if raw_loop is None else int(raw_loop)
        except (TypeError, ValueError) as exc:
            raise SchemeFormatError("loop 必须是整数") from exc
        if not 1 <= loop <= MAX_LOOP_TIMES:
            raise SchemeFormatError(f"loop 必须在 1..{MAX_LOOP_TIMES} 之间")
        try:
            between_wait = float(data.get("between_wait", DEFAULT_BETWEEN_WAIT) or 0.0)
        except (TypeError, ValueError) as exc:
            raise SchemeFormatError("between_wait 必须是数字") from exc
        if between_wait < 0:
            # 负的间隔会被静默忽略（等于 0），不如直接说清楚
            raise SchemeFormatError(f"between_wait 不能是负数（收到 {between_wait:g}）")
        return cls(
            name=str(data.get("name", "") or "新方案"),
            description=str(data.get("description", "") or ""),
            loop=loop,
            between_wait=max(0.0, between_wait),
            segments=tuple(segments),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> Scheme:
        file_path = Path(path)
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SchemeFormatError(f"读不到方案文件: {exc}") from exc
        except ValueError as exc:
            raise SchemeFormatError(f"方案文件不是合法 JSON: {exc}") from exc
        scheme = cls.from_dict(data)
        scheme.source = file_path.name
        if not scheme.name or scheme.name == "新方案":
            scheme.name = file_path.stem
        return scheme

    # ── 导出 ─────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "loop": self.loop,
            "between_wait": self.between_wait,
            "segments": [
                {
                    "label": segment.label,
                    **({"steps": list(segment.steps)} if segment.steps else {}),
                    **({"events": [dict(event) for event in segment.events]} if segment.events else {}),
                }
                for segment in self.segments
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    # ── 拼装 ─────────────────────────────────────────────
    def build_steps(self) -> tuple[list[dict], SchemeReport]:
        """把各段录制拼成流程步骤，并给出转换报告。"""
        report = SchemeReport(segments=len(self.segments))
        steps: list[dict] = []

        for segment in self.segments:
            if segment.steps:
                segment_steps = [dict(step) for step in segment.steps]
            else:
                conversion = convert_recording(segment.events)
                segment_steps = list(conversion.steps)
                report.skipped += conversion.skipped
                report.truncated = report.truncated or conversion.truncated
                report.notes.extend(conversion.notes)

            if not segment_steps:
                report.notes.append(f"{segment.source_label()} 没有可用步骤，已跳过")
                continue

            if steps and self.between_wait > 0:
                steps.append({"type": "wait", "seconds": round(self.between_wait, 3)})
            steps.extend(segment_steps)
            report.converted += len(segment_steps)

        if self.loop > 1:
            steps = [{"type": "loop", "times": self.loop, "steps": steps}]
        return steps, report

    def to_flow(self) -> tuple[Flow, SchemeReport]:
        """拼成 Flow 并做与流程一致的校验。"""
        steps, report = self.build_steps()
        flow = parse_flow(
            {
                "name": self.name,
                "description": self.description or f"轮椅方案：{len(self.segments)} 段",
                "steps": steps,
            }
        )
        issues = [issue for issue in validate_flow(flow) if issue.level == "error"]
        if issues:
            raise SchemeFormatError("方案拼出来的流程不合法：" + "；".join(str(issue) for issue in issues))
        return flow, report


# ── 存取 ─────────────────────────────────────────────────
def scheme_path(name: str, folder: str | Path | None = None) -> Path:
    folder = Path(folder) if folder is not None else SCHEME_FOLDER
    token = _SAFE_NAME.sub("_", str(name or "")).strip("._")
    if not token:
        raise SchemeFormatError("方案名字不能为空")
    return folder / f"{token}.json"


def save_scheme(scheme: Scheme, folder: str | Path | None = None) -> Path:
    path = scheme_path(scheme.name, folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(scheme.to_json(), encoding="utf-8")
    os.replace(tmp, path)
    return path


def list_schemes(folder: str | Path | None = None) -> list[str]:
    folder = Path(folder) if folder is not None else SCHEME_FOLDER
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.json"))


def load_scheme(name: str, folder: str | Path | None = None) -> Scheme:
    path = scheme_path(name, folder)
    if not path.is_file():
        raise SchemeFormatError(f"方案不存在: {name}")
    return Scheme.from_file(path)


def scheme_to_flow_file(scheme: Scheme, flow_name: str | None = None) -> Path:
    """把方案导出成流程文件（之后就能在「流程 / 队列」任务里跑）。"""
    flow, _report = scheme.to_flow()
    if flow_name:
        flow.name = flow_name
        flow.flow_id = flow_name
    return save_flow(flow)


__all__ = [
    "DEFAULT_BETWEEN_WAIT",
    "SCHEME_FOLDER",
    "Scheme",
    "SchemeFormatError",
    "SchemeReport",
    "SchemeSegment",
    "list_schemes",
    "load_scheme",
    "save_scheme",
    "scheme_path",
    "scheme_to_flow_file",
]
