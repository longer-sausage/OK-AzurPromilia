"""流程模型与校验。

JSON 结构（顶层）::

    {
      "name": "每日刷图",
      "description": "可选的说明",
      "version": 1,
      "steps": [ ... ]
    }

步骤是递归结构，``steps`` 是循环体 / if 的 then 分支，``else`` 是 if 的 else 分支。
所有步骤都支持公共字段 ``timeout``（秒）与 ``comment``（备注）。

硬限制（超了直接算错误，不会「尽力执行」）：

============  ==================================
总步数         500（含嵌套的容器步骤）
单层循环次数   1000
嵌套深度       4
============  ==================================
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_STEPS = 500
MAX_LOOP_TIMES = 1000
MAX_DEPTH = 4

#: 流程文件默认目录（与队列、配置档同一层）
FLOW_FOLDER = Path("configs") / "flows"

_SAFE_FLOW_NAME = re.compile(r"[^0-9A-Za-z_.\u4e00-\u9fff-]+")

STEP_TYPES = ("move", "click", "action", "key", "wait", "wait_template", "loop", "if")
MOVE_DIRECTIONS = ("w", "a", "s", "d", "wa", "wd", "sa", "sd", "aw", "dw", "as", "ds")
EXPECT_MODES = ("appear", "disappear")

COMMON_KEYS = {"type", "timeout", "comment"}
TYPE_KEYS = {
    "move": {"direction", "duration", "dx", "dy"},
    "click": {"x", "y", "template", "duration", "times", "hold"},
    "action": {"action", "args"},
    "key": {"key", "duration", "times"},
    "wait": {"seconds"},
    "wait_template": {"template", "expect", "settle", "threshold"},
    "loop": {"times", "max", "until_template", "until_not_template", "steps"},
    "if": {"template", "steps", "else", "threshold"},
}


class FlowFormatError(ValueError):
    """流程结构本身无法解析（不是 dict、步骤不是列表、type 不认识等）。"""


def parse_flow(data: Mapping[str, Any], source: str = "") -> Flow:
    """便捷入口：等价于 ``Flow.from_dict``。"""
    return Flow.from_dict(data, source=source)


def load_flow(path: str | Path) -> Flow:
    """从 JSON 文件读一份流程。"""
    return Flow.from_file(path)


# ── 流程文件（configs/flows） ─────────────────────────────
def flow_path(name: str, folder: str | Path | None = None) -> Path:
    """流程文件路径；名字里的非法字符会被替换成下划线。"""
    folder = Path(folder) if folder is not None else FLOW_FOLDER
    token = _SAFE_FLOW_NAME.sub("_", str(name or "")).strip("._")
    if not token:
        raise FlowFormatError("流程名字不能为空")
    return folder / f"{token}.json"


def save_flow(flow: Flow, folder: str | Path | None = None) -> Path:
    path = flow_path(flow.name or flow.key, folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(flow.to_json(), encoding="utf-8")
    os.replace(tmp, path)
    return path


def list_flows(folder: str | Path | None = None) -> list[str]:
    folder = Path(folder) if folder is not None else FLOW_FOLDER
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.json"))


def load_flow_by_name(name: str, folder: str | Path | None = None) -> Flow:
    """按名字读流程：先试清洗后的文件名，再试原名（兼容历史安装的旧文件）。"""
    path = flow_path(name, folder)
    if not path.is_file():
        legacy = (Path(folder) if folder is not None else FLOW_FOLDER) / f"{name}.json"
        if legacy.is_file():
            path = legacy
        else:
            raise FlowFormatError(f"流程不存在: {name}")
    return Flow.from_file(path)


@dataclass(frozen=True)
class FlowIssue:
    level: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.path}: {self.message}"


@dataclass(frozen=True)
class FlowStep:
    """一个步骤。按 ``type`` 使用不同字段，未用到的字段保持默认值。"""

    type: str
    timeout: float | None = None
    comment: str = ""
    path: str = ""

    # move
    direction: str | None = None
    duration: float = 0.0
    dx: float | None = None
    dy: float | None = None

    # click
    x: float | None = None
    y: float | None = None

    # key / click / wait_template / loop / if
    key: str | None = None
    template: str | None = None
    expect: str = "appear"
    settle: float = 0.0
    threshold: float | None = None
    times: int | None = None
    #: click 的「按住多久再松开」（长按 / 蓄力）；不填就是普通点击
    hold: float | None = None

    # wait
    seconds: float = 0.0

    # action
    action: str | None = None
    args: Mapping[str, Any] = field(default_factory=dict)

    # loop / if
    max_times: int | None = None
    until_template: str | None = None
    until_not_template: str | None = None
    steps: tuple[FlowStep, ...] = ()
    else_steps: tuple[FlowStep, ...] = ()


@dataclass
class Flow:
    """一份流程定义。"""

    steps: tuple[FlowStep, ...] = ()
    name: str = ""
    description: str = ""
    flow_id: str = ""
    version: int = 1
    issues: list[FlowIssue] = field(default_factory=list)
    source: str = ""

    # ── 构造 ─────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any], source: str = "") -> Flow:
        if not isinstance(data, Mapping):
            raise FlowFormatError("流程顶层必须是 JSON 对象")
        raw_steps = data.get("steps")
        if raw_steps is None:
            raise FlowFormatError("流程缺少 steps")
        if not isinstance(raw_steps, list):
            raise FlowFormatError("steps 必须是数组")

        flow = cls(
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or ""),
            flow_id=str(data.get("id", "") or data.get("name", "") or ""),
            version=int(data.get("version", 1) or 1),
            source=source,
        )
        flow.steps = _parse_steps(raw_steps, "steps", 1, flow.issues)
        return flow

    @classmethod
    def from_json(cls, text: str, source: str = "") -> Flow:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise FlowFormatError(f"JSON 解析失败: {exc}") from exc
        return cls.from_dict(data, source=source)

    @classmethod
    def from_file(cls, path: str | Path) -> Flow:
        file_path = Path(path)
        return cls.from_json(file_path.read_text(encoding="utf-8"), source=file_path.name)

    # ── 序列化 ───────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "steps": [_step_to_dict(step) for step in self.steps],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    # ── 统计 ─────────────────────────────────────────────
    @property
    def key(self) -> str:
        """断点文件用的稳定标识。"""
        return self.flow_id or self.name or "flow"

    def step_count(self) -> int:
        return count_steps(self.steps)

    def depth(self) -> int:
        return measure_depth(self.steps)

    def container_count(self) -> int:
        return sum(1 for step in _walk(self.steps) if step.type in ("loop", "if"))


# ── 解析 ─────────────────────────────────────────────────
def _parse_steps(raw: Iterable[Any], path: str, depth: int, issues: list[FlowIssue]) -> tuple[FlowStep, ...]:
    steps = []
    for index, item in enumerate(raw):
        child_path = f"{path}[{index}]"
        if not isinstance(item, Mapping):
            issues.append(FlowIssue("error", child_path, "步骤必须是 JSON 对象"))
            continue
        steps.append(_parse_step(item, child_path, depth, issues))
    return tuple(steps)


def _parse_step(data: Mapping[str, Any], path: str, depth: int, issues: list[FlowIssue]) -> FlowStep:
    step_type = str(data.get("type", "") or "").strip()
    if step_type not in STEP_TYPES:
        raise FlowFormatError(f"{path}: 不认识的步骤类型 {step_type!r}（支持 {', '.join(STEP_TYPES)}）")

    allowed = COMMON_KEYS | TYPE_KEYS[step_type]
    for key in data:
        if key not in allowed:
            issues.append(FlowIssue("error", f"{path}.{key}", f"{step_type} 不支持字段 {key}"))

    def _child(name: str) -> tuple[FlowStep, ...]:
        raw = data.get(name)
        if raw is None:
            return ()
        if not isinstance(raw, list):
            issues.append(FlowIssue("error", f"{path}.{name}", "必须是数组"))
            return ()
        return _parse_steps(raw, f"{path}.{name}", depth + 1, issues)

    return FlowStep(
        type=step_type,
        timeout=_opt_float(data.get("timeout"), f"{path}.timeout", issues, positive=True),
        comment=str(data.get("comment", "") or ""),
        path=path,
        direction=_opt_str(data.get("direction")),
        duration=_opt_float(data.get("duration"), f"{path}.duration", issues, default=0.0),
        dx=_opt_float(data.get("dx"), f"{path}.dx", issues, allow_negative=True),
        dy=_opt_float(data.get("dy"), f"{path}.dy", issues, allow_negative=True),
        x=_opt_float(data.get("x"), f"{path}.x", issues),
        y=_opt_float(data.get("y"), f"{path}.y", issues),
        key=_opt_str(data.get("key")),
        template=_opt_str(data.get("template")),
        expect=str(data.get("expect", "appear") or "appear"),
        settle=_opt_float(data.get("settle"), f"{path}.settle", issues, default=0.0),
        threshold=_opt_float(data.get("threshold"), f"{path}.threshold", issues),
        times=_opt_int(data.get("times"), f"{path}.times", issues),
        hold=_opt_float(data.get("hold"), f"{path}.hold", issues),
        seconds=_opt_float(data.get("seconds"), f"{path}.seconds", issues, default=0.0),
        action=_opt_str(data.get("action")),
        args=_opt_mapping(data.get("args"), f"{path}.args", issues),
        max_times=_opt_int(data.get("max"), f"{path}.max", issues),
        until_template=_opt_str(data.get("until_template")),
        until_not_template=_opt_str(data.get("until_not_template")),
        steps=_child("steps"),
        else_steps=_child("else"),
    )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_float(value: Any, path: str, issues: list[FlowIssue], default: float | None = None,
               positive: bool = False, allow_negative: bool = False) -> float | None:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        issues.append(FlowIssue("error", path, "必须是数字"))
        return default
    if positive and number <= 0:
        issues.append(FlowIssue("error", path, "必须大于 0"))
        return default
    if number < 0 and not allow_negative:
        issues.append(FlowIssue("error", path, "不能是负数"))
        return default
    return number


def _opt_int(value: Any, path: str, issues: list[FlowIssue]) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        issues.append(FlowIssue("error", path, "必须是整数"))
        return None


def _opt_mapping(value: Any, path: str, issues: list[FlowIssue]) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        issues.append(FlowIssue("error", path, "必须是对象"))
        return {}
    return dict(value)


# ── 校验 ─────────────────────────────────────────────────
def validate_flow(flow: Flow) -> list[FlowIssue]:
    """返回全部问题（``error`` 表示不可执行；``warning`` 只是提示）。"""
    issues: list[FlowIssue] = list(flow.issues)

    total = flow.step_count()
    if total == 0:
        issues.append(FlowIssue("error", "steps", "流程没有任何步骤"))
    if total > MAX_STEPS:
        issues.append(FlowIssue("error", "steps", f"总步数 {total} 超过上限 {MAX_STEPS}"))

    depth = flow.depth()
    if depth > MAX_DEPTH:
        issues.append(FlowIssue("error", "steps", f"嵌套深度 {depth} 超过上限 {MAX_DEPTH}"))

    for step in _walk(flow.steps):
        _validate_step(step, issues)
    return issues


def _validate_step(step: FlowStep, issues: list[FlowIssue]) -> None:
    path = step.path or step.type

    if step.type == "move":
        if step.direction and (step.dx is not None or step.dy is not None):
            issues.append(FlowIssue("error", path, "direction 与 dx/dy 只能二选一"))
        elif not step.direction and step.dx is None and step.dy is None:
            issues.append(FlowIssue("error", path, "move 需要 direction 或 dx/dy"))
        if step.direction and step.direction.lower() not in MOVE_DIRECTIONS:
            issues.append(
                FlowIssue("error", f"{path}.direction", f"不支持的方向 {step.direction!r}（可用 {', '.join(MOVE_DIRECTIONS)}）")
            )

    elif step.type == "click":
        has_point = step.x is not None and step.y is not None
        has_template = bool(step.template)
        if has_point and has_template:
            issues.append(FlowIssue("error", path, "x/y 与 template 只能二选一"))
        elif not has_point and not has_template:
            issues.append(FlowIssue("error", path, "click 需要 x/y 或 template"))
        if has_point:
            _check_ratio(step.x, f"{path}.x", issues)
            _check_ratio(step.y, f"{path}.y", issues)
        if step.times is not None and step.times < 1:
            issues.append(FlowIssue("error", f"{path}.times", "次数至少为 1"))
        if step.hold is not None and step.hold < 0:
            issues.append(FlowIssue("error", f"{path}.hold", "按住时长不能是负数"))

    elif step.type == "key":
        if not step.key:
            issues.append(FlowIssue("error", path, "key 不能为空"))
        if step.times is not None and step.times < 1:
            issues.append(FlowIssue("error", f"{path}.times", "次数至少为 1"))

    elif step.type == "action":
        if not step.action:
            issues.append(FlowIssue("error", path, "action 不能为空"))

    elif step.type == "wait":
        if step.seconds is None or step.seconds < 0:
            issues.append(FlowIssue("error", f"{path}.seconds", "等待时间不能是负数"))

    elif step.type == "wait_template":
        if not step.template:
            issues.append(FlowIssue("error", path, "wait_template 需要 template"))
        if step.expect not in EXPECT_MODES:
            issues.append(
                FlowIssue("error", f"{path}.expect", f"expect 只能是 {', '.join(EXPECT_MODES)}")
            )
        if step.threshold is not None:
            _check_ratio(step.threshold, f"{path}.threshold", issues)

    elif step.type == "loop":
        modes = [
            ("times", step.times is not None),
            ("until_template", bool(step.until_template)),
            ("until_not_template", bool(step.until_not_template)),
        ]
        chosen = [name for name, present in modes if present]
        if len(chosen) != 1:
            issues.append(
                FlowIssue("error", path, "loop 需要在 times / until_template / until_not_template 中选且只选一个")
            )
        if step.times is not None and not (1 <= step.times <= MAX_LOOP_TIMES):
            issues.append(
                FlowIssue("error", f"{path}.times", f"循环次数必须在 1..{MAX_LOOP_TIMES} 之间")
            )
        if step.max_times is not None and not (1 <= step.max_times <= MAX_LOOP_TIMES):
            issues.append(
                FlowIssue("error", f"{path}.max", f"循环上限必须在 1..{MAX_LOOP_TIMES} 之间")
            )
        if not step.steps:
            issues.append(FlowIssue("error", f"{path}.steps", "循环体不能为空"))

    elif step.type == "if":
        if not step.template:
            issues.append(FlowIssue("error", path, "if 需要 template"))
        if not step.steps and not step.else_steps:
            issues.append(FlowIssue("error", path, "if 的两个分支都为空，没有意义"))
        if step.threshold is not None:
            _check_ratio(step.threshold, f"{path}.threshold", issues)


def _check_ratio(value: float | None, path: str, issues: list[FlowIssue]) -> None:
    if value is None:
        return
    if not (0 <= value <= 1):
        issues.append(FlowIssue("error", path, "比例坐标必须在 0..1 之间"))


# ── 遍历 ─────────────────────────────────────────────────
def _walk(steps: Iterable[FlowStep]) -> Iterable[FlowStep]:
    for step in steps:
        yield step
        if step.steps:
            yield from _walk(step.steps)
        if step.else_steps:
            yield from _walk(step.else_steps)


def count_steps(steps: Iterable[FlowStep]) -> int:
    return sum(1 for _ in _walk(steps))


def measure_depth(steps: Iterable[FlowStep], level: int = 1) -> int:
    deepest = 0
    for step in steps:
        deepest = max(deepest, level)
        for branch in (step.steps, step.else_steps):
            if branch:
                deepest = max(deepest, measure_depth(branch, level + 1))
    return deepest


# ── 序列化单步 ───────────────────────────────────────────
def _step_to_dict(step: FlowStep) -> dict:
    data: dict[str, Any] = {"type": step.type}
    if step.timeout is not None:
        data["timeout"] = step.timeout
    if step.comment:
        data["comment"] = step.comment
    if step.type == "move":
        if step.direction:
            data["direction"] = step.direction
        if step.dx is not None:
            data["dx"] = step.dx
        if step.dy is not None:
            data["dy"] = step.dy
        if step.duration:
            data["duration"] = step.duration
    elif step.type == "click":
        if step.template:
            data["template"] = step.template
        else:
            data["x"] = step.x
            data["y"] = step.y
        if step.times is not None:
            data["times"] = step.times
        if step.hold:
            data["hold"] = step.hold
        if step.duration:
            data["duration"] = step.duration
    elif step.type == "key":
        data["key"] = step.key
        if step.duration:
            data["duration"] = step.duration
        if step.times is not None:
            data["times"] = step.times
    elif step.type == "wait":
        data["seconds"] = step.seconds
    elif step.type == "wait_template":
        data["template"] = step.template
        if step.expect != "appear":
            data["expect"] = step.expect
        if step.settle:
            data["settle"] = step.settle
        if step.threshold is not None:
            data["threshold"] = step.threshold
    elif step.type == "action":
        data["action"] = step.action
        if step.args:
            data["args"] = dict(step.args)
    elif step.type == "loop":
        if step.times is not None:
            data["times"] = step.times
        if step.until_template:
            data["until_template"] = step.until_template
        if step.until_not_template:
            data["until_not_template"] = step.until_not_template
        if step.max_times is not None:
            data["max"] = step.max_times
        data["steps"] = [_step_to_dict(child) for child in step.steps]
    elif step.type == "if":
        data["template"] = step.template
        if step.threshold is not None:
            data["threshold"] = step.threshold
        if step.steps:
            data["steps"] = [_step_to_dict(child) for child in step.steps]
        if step.else_steps:
            data["else"] = [_step_to_dict(child) for child in step.else_steps]
    return data
