"""示例流程模板：把 ``assets/flow_templates/`` 里的流程装进 ``configs/flows/``。

模板的价值在于**告诉你要标哪些模板**：:func:`required_templates` 把一份流程里用到的
所有模板名收集出来，你可以直接拿这个名字去标注工具里命名，对上就能跑。

- 「周常活跃奖励」只用到仓库里已有的模板，**装好就能跑**；
- 「危机讨伐（骨架）」「剧情委托（骨架）」各差 3 个模板，名字已经写死在流程里，
  标注时照抄即可。

安装默认**不覆盖同名文件**（你可能已经改过一份了）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from src.core.flow.model import FLOW_FOLDER, Flow, FlowFormatError, parse_flow

TEMPLATE_FOLDER = Path("assets") / "flow_templates"


class FlowTemplateError(ValueError):
    """模板目录或模板文件不合法。"""


@dataclass
class TemplateInstall:
    name: str
    ok: bool = False
    path: Path | None = None
    message: str = ""

    def summary(self) -> str:
        if self.ok:
            return f"{self.name} → {self.path}"
        return f"{self.name}：{self.message}"


@dataclass
class InstallReport:
    results: list[TemplateInstall] = field(default_factory=list)

    @property
    def installed(self) -> list[TemplateInstall]:
        return [item for item in self.results if item.ok]

    @property
    def skipped(self) -> list[TemplateInstall]:
        return [item for item in self.results if not item.ok]

    def describe(self) -> str:
        parts = []
        if self.installed:
            parts.append(f"装好 {len(self.installed)} 份：" + "、".join(item.name for item in self.installed))
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 份：" + "、".join(f"{item.name}（{item.message}）" for item in self.skipped))
        return "；".join(parts) or "没有可安装的模板"


def template_folder(folder: str | Path | None = None) -> Path:
    return Path(folder) if folder is not None else TEMPLATE_FOLDER


def list_templates(folder: str | Path | None = None) -> list[str]:
    base = template_folder(folder)
    if not base.is_dir():
        return []
    return sorted(path.stem for path in base.glob("*.json"))


def load_template(name: str, folder: str | Path | None = None) -> Flow:
    base = template_folder(folder)
    path = base / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FlowTemplateError(f"读不到模板 {name}: {exc}") from exc
    except ValueError as exc:
        raise FlowTemplateError(f"模板 {name} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FlowTemplateError(f"模板 {name} 顶层必须是对象")
    return parse_flow(data, source=str(path))


def required_templates(flow: Flow) -> list[str]:
    """流程里用到的全部模板名（去重、保序），用来告诉用户「还要标哪些」。"""
    names: list[str] = []

    def _add(value: str | None) -> None:
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)

    def _walk(steps) -> None:
        for step in steps:
            _add(step.template)
            _add(step.until_template)
            _add(step.until_not_template)
            _walk(step.steps)
            _walk(step.else_steps)

    _walk(flow.steps)
    return names


def feature_names() -> set[str]:
    """当前可识别的模板名（来自 COCO 标注，标注后热更新即可生效）。

    ``FeatureList`` 是 Enum，成员名是属性名、``.value`` 才是模板名（小写带下划线），
    流程里写的也是 ``.value``，所以这里取 value 而不是 ``str(member)``。
    """
    try:
        from src.data.feature_list import FeatureList
    except Exception:
        return set()

    names: set[str] = set()
    try:
        members = list(FeatureList)
    except TypeError:
        members = []
    for member in members:
        value = getattr(member, "value", member)
        if isinstance(value, str) and value:
            names.add(value)
    return names


def missing_templates(flow: Flow, available: set[str] | None = None) -> list[str]:
    """流程需要、但当前还**认不出**的模板名 —— 这些就是你还要去标的。"""
    known = feature_names() if available is None else set(available)
    return [name for name in required_templates(flow) if name not in known]


# ── 其它类型的示例（队列 / 路线） ────────────────────────
KIND_FLOW = "flow"
KIND_QUEUE = "queue"
KIND_ROUTE = "route"
EXAMPLE_KINDS = (KIND_FLOW, KIND_QUEUE, KIND_ROUTE)

EXAMPLE_SOURCES = {
    KIND_FLOW: TEMPLATE_FOLDER,
    KIND_QUEUE: Path("assets") / "queue_templates",
    KIND_ROUTE: Path("assets") / "route_templates",
}


def example_target(kind: str, folder: str | Path | None = None) -> Path:
    if kind == KIND_FLOW:
        return Path(folder) if folder is not None else FLOW_FOLDER
    if kind == KIND_QUEUE:
        return Path(folder) if folder is not None else Path("configs") / "queues"
    if kind == KIND_ROUTE:
        return Path(folder) if folder is not None else Path("configs") / "routes"
    raise FlowTemplateError(f"不认识的示例类型 {kind!r}")


def destination_path(kind: str, name: str, target: Path) -> Path:
    """示例落盘时的文件名：走各类型自己的 ``*_path``（会清洗非法字符）。

    真实 bug：示例名叫「剧情委托（骨架）」，直接拼 ``target/名字.json`` 会写出带
    全角括号的文件；而 ``load_flow_by_name`` 用 ``flow_path`` 再清洗一遍得到
    「剧情委托_骨架.json」，于是「能列出来、点开却说不存在」。
    """
    try:
        if kind == KIND_FLOW:
            from src.core.flow.model import flow_path

            return flow_path(name, target)
        if kind == KIND_QUEUE:
            from src.core.queue.model import queue_path

            return queue_path(name, target)
        if kind == KIND_ROUTE:
            from src.core.route import route_path

            return route_path(name, target)
    except Exception:
        pass
    return target / f"{name}.json"


def list_examples(kind: str, folder: str | Path | None = None) -> list[str]:
    base = Path(folder) if folder is not None else EXAMPLE_SOURCES[kind]
    if not base.is_dir():
        return []
    return sorted(path.stem for path in base.glob("*.json"))


def _load_example_payload(kind: str, name: str, folder: str | Path | None) -> tuple[dict, str]:
    """读出示例 JSON 并**用对应的模型校验一遍**，返回 ``(payload, json_text)``。"""
    if kind == KIND_FLOW:
        flow = load_template(name, folder)
        return json.loads(flow.to_json()), flow.to_json()

    from src.core.queue import QueueFormatError, TaskQueue
    from src.core.route import Route

    base = Path(folder) if folder is not None else EXAMPLE_SOURCES[kind]
    path = base / f"{name}.json"
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except OSError as exc:
        raise FlowTemplateError(f"读不到示例 {name}: {exc}") from exc
    except ValueError as exc:
        raise FlowTemplateError(f"示例 {name} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FlowTemplateError(f"示例 {name} 顶层必须是对象")

    if kind == KIND_QUEUE:
        try:
            queue = TaskQueue.from_dict(data)
        except QueueFormatError as exc:
            raise FlowTemplateError(f"队列示例 {name}：{exc}") from exc
        return json.loads(queue.to_json()), queue.to_json()

    try:
        route = Route.from_dict(data)
    except Exception as exc:
        raise FlowTemplateError(f"路线示例 {name}：{exc}") from exc
    issues = route.validate()
    if issues:
        raise FlowTemplateError(f"路线示例 {name}：" + "；".join(issues))
    return json.loads(route.to_json()), route.to_json()


def install_examples(
    kind: str,
    target_folder: str | Path | None = None,
    folder: str | Path | None = None,
    overwrite: bool = False,
) -> InstallReport:
    """把某一类示例逐份装到对应目录；单份失败不影响其它份。"""
    target = example_target(kind, target_folder)
    source = Path(folder) if folder is not None else EXAMPLE_SOURCES[kind]
    report = InstallReport()
    for name in list_examples(kind, source):
        try:
            _payload, text = _load_example_payload(kind, name, source)
        except FlowTemplateError as exc:
            report.results.append(TemplateInstall(name=name, message=str(exc)))
            continue
        try:
            path = destination_path(kind, name, target)
            if path.exists() and not overwrite:
                report.results.append(TemplateInstall(name=name, message="同名文件已存在，不覆盖"))
                continue
            target.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            report.results.append(TemplateInstall(name=name, ok=True, path=path))
        except (OSError, FlowFormatError) as exc:
            report.results.append(TemplateInstall(name=name, message=str(exc)))
    return report


def install_all_examples(
    overwrite: bool = False,
    targets: dict[str, str | Path] | None = None,
) -> InstallReport:
    """流程 / 队列 / 路线三类示例一次装完。

    ``targets`` 可以按类型覆盖安装目录（测试用；默认各自装进 ``configs/<kind>s``）。
    """
    merged = InstallReport()
    for kind in EXAMPLE_KINDS:
        target = (targets or {}).get(kind)
        merged.results.extend(install_examples(kind, target_folder=target, overwrite=overwrite).results)
    return merged


def install_templates(
    target_folder: str | Path | None = None,
    folder: str | Path | None = None,
    overwrite: bool = False,
) -> InstallReport:
    """把流程模板逐份装到 ``configs/flows/``（保留旧入口，等价于安装 flow 类示例）。"""
    return install_examples(KIND_FLOW, target_folder=target_folder, folder=folder, overwrite=overwrite)
