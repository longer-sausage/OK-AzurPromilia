"""导航路线：把「怎么走到某个界面」写成可保存、可复用的步骤串。

一条路线就是一串**航点**（waypoint），每个航点做四件事里的一件或几件：

```text
页面校验   当前是否已经处于该界面（走 Page 拓扑导航）
等模板     等某个模板出现（超时算失败）
点击       按比例坐标点一下
等待       停一会儿
```

```json
{
  "name": "每日委托路线",
  "description": "从主界面到委托面板",
  "waypoints": [
    {"page": "page_main"},
    {"template": "commission_icon", "timeout": 10},
    {"click": [0.5, 0.5], "wait": 1.0},
    {"page": "page_commission"}
  ]
}
```

文件放在 `configs/routes/<名字>.json`。断点按**整条航点**记录（复用流程那套
``CheckpointStore``），跑通的路线下次可以「从断点继续」。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROUTE_FOLDER = Path("configs") / "routes"
DEFAULT_STEP_TIMEOUT = 30.0
DEFAULT_POLL_INTERVAL = 0.1

_SAFE_NAME = re.compile(r"[^0-9A-Za-z_.\u4e00-\u9fff-]+")


class RouteFormatError(ValueError):
    """路线 JSON 结构不合法。"""


@dataclass(frozen=True)
class Waypoint:
    page: str = ""
    template: str = ""
    click: tuple[float, float] | None = None
    wait: float = 0.0
    timeout: float = DEFAULT_STEP_TIMEOUT
    note: str = ""

    @property
    def has_action(self) -> bool:
        return bool(self.page or self.template or self.click or self.wait)

    def label(self, index: int) -> str:
        parts = []
        if self.page:
            parts.append(f"页面 {self.page}")
        if self.template:
            parts.append(f"模板 {self.template}")
        if self.click:
            parts.append(f"点击 ({self.click[0]:.2f}, {self.click[1]:.2f})")
        if self.wait:
            parts.append(f"等待 {self.wait:g}s")
        return f"{index + 1}. " + " → ".join(parts or ["空航点"])


@dataclass
class Route:
    name: str = "新路线"
    description: str = ""
    waypoints: tuple[Waypoint, ...] = ()
    source: str = ""

    # ── 载入 ─────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Route:
        if not isinstance(data, Mapping):
            raise RouteFormatError("路线顶层必须是 JSON 对象")
        raw = data.get("waypoints")
        if raw is None:
            raise RouteFormatError("路线缺少 waypoints")
        if not isinstance(raw, list):
            raise RouteFormatError("waypoints 必须是数组")
        waypoints = []
        for index, item in enumerate(raw):
            waypoints.append(_parse_waypoint(item, f"waypoints[{index}]"))
        return cls(
            name=str(data.get("name", "") or "新路线"),
            description=str(data.get("description", "") or ""),
            waypoints=tuple(waypoints),
        )

    @classmethod
    def from_json(cls, text: str) -> Route:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise RouteFormatError(f"JSON 解析失败: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, path: str | Path) -> Route:
        file_path = Path(path)
        route = cls.from_json(file_path.read_text(encoding="utf-8"))
        route.source = file_path.name
        if not route.name or route.name == "新路线":
            route.name = file_path.stem
        return route

    # ── 序列化 ───────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "waypoints": [
                {
                    **({"page": point.page} if point.page else {}),
                    **({"template": point.template} if point.template else {}),
                    **({"click": list(point.click)} if point.click else {}),
                    **({"wait": point.wait} if point.wait else {}),
                    **({"timeout": point.timeout} if point.timeout != DEFAULT_STEP_TIMEOUT else {}),
                    **({"note": point.note} if point.note else {}),
                }
                for point in self.waypoints
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    # ── 校验 ─────────────────────────────────────────────
    def validate(self, known_pages: set[str] | None = None) -> list[str]:
        """返回问题列表（空表表示可以执行）。"""
        issues: list[str] = []
        if not self.waypoints:
            issues.append("路线没有任何航点")
        for index, point in enumerate(self.waypoints):
            prefix = f"waypoints[{index}]"
            if not point.has_action:
                issues.append(f"{prefix}: 空航点（page/template/click/wait 至少要有一个）")
            if point.timeout <= 0:
                issues.append(f"{prefix}.timeout: 必须大于 0")
            if point.click is not None:
                x, y = point.click
                if not (0 <= x <= 1 and 0 <= y <= 1):
                    issues.append(f"{prefix}.click: 比例坐标必须在 0..1 之间")
            if known_pages is not None and point.page and point.page not in known_pages:
                issues.append(f"{prefix}.page: 未注册的界面 {point.page}")
        return issues

    def labels(self) -> list[str]:
        return [point.label(index) for index, point in enumerate(self.waypoints)]


def _parse_waypoint(data: Any, path: str) -> Waypoint:
    if not isinstance(data, Mapping):
        raise RouteFormatError(f"{path} 必须是 JSON 对象")
    allowed = {"page", "template", "click", "wait", "timeout", "note"}
    for key in data:
        if key not in allowed:
            raise RouteFormatError(f"{path}: 不支持字段 {key}")

    click = data.get("click")
    if click is not None:
        if not isinstance(click, (list, tuple)) or len(click) != 2:
            raise RouteFormatError(f"{path}.click 必须是 [x, y]")
        try:
            click = (float(click[0]), float(click[1]))
        except (TypeError, ValueError) as exc:
            raise RouteFormatError(f"{path}.click 必须是数字") from exc

    def number(key: str, default: float) -> float:
        try:
            return float(data.get(key, default) or default)
        except (TypeError, ValueError) as exc:
            raise RouteFormatError(f"{path}.{key} 必须是数字") from exc

    return Waypoint(
        page=str(data.get("page", "") or "").strip(),
        template=str(data.get("template", "") or "").strip(),
        click=click,
        wait=number("wait", 0.0),
        timeout=number("timeout", DEFAULT_STEP_TIMEOUT),
        note=str(data.get("note", "") or ""),
    )


# ── 存取 ─────────────────────────────────────────────────
def route_fingerprint(route: Route) -> str:
    """航点结构的摘要：路线被改过之后，旧断点就不可信了（与流程同一套判断）。"""
    from src.core.flow.executor import structure_fingerprint

    return structure_fingerprint(
        [
            {
                "page": point.page,
                "template": point.template,
                "click": list(point.click) if point.click else None,
                "wait": point.wait,
                "timeout": point.timeout,
            }
            for point in route.waypoints
        ]
    )


def route_path(name: str, folder: str | Path | None = None) -> Path:
    folder = Path(folder) if folder is not None else ROUTE_FOLDER
    token = _SAFE_NAME.sub("_", str(name or "")).strip("._")
    if not token:
        raise RouteFormatError("路线名字不能为空")
    return folder / f"{token}.json"


def save_route(route: Route, folder: str | Path | None = None) -> Path:
    path = route_path(route.name, folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(route.to_json(), encoding="utf-8")
    os.replace(tmp, path)
    return path


def list_routes(folder: str | Path | None = None) -> list[str]:
    folder = Path(folder) if folder is not None else ROUTE_FOLDER
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.json"))


def load_route(name: str, folder: str | Path | None = None) -> Route:
    path = route_path(name, folder)
    if not path.is_file():
        raise RouteFormatError(f"路线不存在: {name}")
    return Route.from_file(path)


# ── 执行 ─────────────────────────────────────────────────
@dataclass
class RouteResult:
    ok: bool = False
    completed: int = 0
    total: int = 0
    resumed_from: int = 0
    failed_at: str = ""
    error: str | None = None
    elapsed: float = 0.0

    def summary(self) -> str:
        if self.ok:
            return f"路线完成：{self.completed}/{self.total} 个航点，用时 {self.elapsed:.1f}s"
        where = f"（{self.failed_at}）" if self.failed_at else ""
        return f"路线失败{where}：{self.error}（已完成 {self.completed}/{self.total}）"


class RouteContext:
    """把 ``BaseGameTask`` 适配成路线原语。"""

    def __init__(self, task, poll_interval: float = DEFAULT_POLL_INTERVAL):
        self.task = task
        self.poll_interval = max(0.01, float(poll_interval or DEFAULT_POLL_INTERVAL))

    def now(self) -> float:
        active_time = getattr(self.task, "active_time", None)
        if callable(active_time):
            try:
                return float(active_time())
            except Exception:
                pass
        import time

        return time.monotonic()

    def goto_page(self, page: str, timeout: float) -> bool:
        """走 Page 拓扑导航到目标界面。"""
        ui_ensure = getattr(self.task, "ui_ensure", None)
        if not callable(ui_ensure):
            raise RouteFormatError("当前任务不支持界面导航（缺少 ui_ensure）")
        try:
            return bool(ui_ensure(page, time_out=timeout))
        except Exception:
            return False

    def page_visible(self, page: str) -> bool:
        appear = getattr(self.task, "ui_page_appear", None)
        if not callable(appear):
            return False
        try:
            return bool(appear(page))
        except Exception:
            return False

    def template_hit(self, name: str, timeout: float) -> bool:
        find_one = getattr(self.task, "find_one", None)
        if not callable(find_one):
            raise RouteFormatError("当前任务不支持模板识别（缺少 find_one）")
        deadline = self.now() + float(timeout or DEFAULT_STEP_TIMEOUT)
        while True:
            try:
                if find_one(feature=name):
                    return True
            except Exception:
                return False
            if self.now() >= deadline:
                return False
            self.sleep(self.poll_interval)

    def click(self, x: float, y: float) -> None:
        click_relative = getattr(self.task, "click_relative", None)
        if callable(click_relative):
            click_relative(float(x), float(y))
            return
        self.task.click(float(x), float(y))

    def sleep(self, seconds: float) -> None:
        if seconds and seconds > 0:
            self.task.sleep(seconds)


class RouteRunner:
    """按顺序执行航点；``page`` 用导航、``template`` 用等待、``click``/``wait`` 直接执行。"""

    def __init__(self, route: Route, context: RouteContext, checkpoint=None,
                 log=None, default_timeout: float = DEFAULT_STEP_TIMEOUT):
        self.route = route
        self.context = context
        self.checkpoint = checkpoint
        self._log = log
        self.default_timeout = float(default_timeout or DEFAULT_STEP_TIMEOUT)

    def run(self, resume: bool = False) -> RouteResult:
        issues = self.route.validate()
        if issues:
            raise RouteFormatError("路线校验未通过：" + "；".join(issues))

        total = len(self.route.waypoints)
        start = 0
        if resume and self.checkpoint is not None:
            start = min(self.checkpoint.load(), total)

        result = RouteResult(ok=True, total=total, completed=start, resumed_from=start)
        started = self.context.now()
        if start:
            self._info(f"从断点继续：跳过前 {start} 个航点")

        for index in range(start, total):
            point = self.route.waypoints[index]
            try:
                self._run_waypoint(point)
            except Exception as exc:
                result.ok = False
                result.failed_at = point.label(index)
                result.error = str(exc)
                self._info(f"航点失败：{result.error}")
                break
            result.completed = index + 1
            if self.checkpoint is not None:
                self.checkpoint.save(result.completed, total)

        if result.ok and self.checkpoint is not None and result.completed >= total:
            self.checkpoint.clear()
        result.elapsed = self.context.now() - started
        return result

    def _run_waypoint(self, point: Waypoint) -> None:
        timeout = point.timeout or self.default_timeout
        if point.page and not self.context.goto_page(point.page, timeout):
            raise RouteFormatError(f"无法到达界面 {point.page}")
        if point.template and not self.context.template_hit(point.template, timeout):
            raise RouteFormatError(f"模板 {point.template} 没有出现")
        if point.click is not None:
            self.context.click(*point.click)
        if point.wait:
            self.context.sleep(point.wait)

    def _info(self, message: str) -> None:
        if self._log:
            with contextlib.suppress(Exception):
                self._log(message)
