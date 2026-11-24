"""安装自检：把你自己的流程 / 队列 / 轮椅方案 / 路线 / 配置一次性体检一遍。

跑起来应该是「跑之前先自检一次」的位置：这些文件都是**用户手改的**，
格式错了不会在保存时报错，而是在跑任务中间才炸。这里提前把话说清楚：

| 检查 | 发现什么 |
|------|----------|
| 模板 | 纯色块、重复模板（用 :mod:`src.core.template_audit`） |
| 流程 | 结构错误、超 500 步 / 4 层 / 1000 次、字段拼错、比例越界 |
| 队列 | 队列项类型不认识、引用为空、配额/预算不合法 |
| 轮椅方案 | 分段为空、循环次数不合法、拼不成流程 |
| 导航路线 | 航点缺动作、页面名为空 |
| 配置 | `configs/*.json` 是不是合法 JSON（改坏了能立刻发现） |
| i18n | 6 个语种目录/文件是否齐全、msgstr 是否有空 |

每一项都不会抛异常：坏文件被转成一条检查结果，其它项继续查。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"

LANGS = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool = True
    level: str = LEVEL_INFO
    message: str = ""
    details: tuple[str, ...] = ()

    def line(self) -> str:
        head = "✅" if self.ok else ("⚠️" if self.level == LEVEL_WARNING else "❌")
        text = f"{head} {self.name}"
        if self.message:
            text += f"：{self.message}"
        return text


@dataclass
class HealthReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(result.level == LEVEL_ERROR for result in self.results)

    @property
    def errors(self) -> list[CheckResult]:
        return [result for result in self.results if result.level == LEVEL_ERROR]

    @property
    def warnings(self) -> list[CheckResult]:
        return [result for result in self.results if result.level == LEVEL_WARNING]

    def summary(self) -> str:
        total = len(self.results)
        bad = len(self.errors)
        warn = len(self.warnings)
        if bad:
            return f"{total} 项检查，{bad} 项有问题，{warn} 项提示"
        if warn:
            return f"{total} 项检查通过（{warn} 项提示）"
        return f"{total} 项检查全部通过"

    def lines(self) -> list[str]:
        lines = [result.line() for result in self.results]
        for result in self.results:
            for detail in result.details:
                lines.append(f"    · {detail}")
        lines.append("")
        lines.append(self.summary())
        return lines


def _missing(name: str, folder: Path) -> CheckResult:
    return CheckResult(name=name, ok=True, message=f"没有文件（{folder}）")


# ── 单项检查 ─────────────────────────────────────────────
def check_templates(root: str | Path = ".") -> CheckResult:
    from src.core.template_audit import TemplateAuditError, audit_directory

    base = Path(root)
    try:
        report = audit_directory(base)
    except TemplateAuditError as exc:
        return CheckResult(name="模板", ok=True, message=f"跳过：{exc}")
    except Exception as exc:
        return CheckResult(name="模板", ok=False, level=LEVEL_ERROR, message=f"自检失败：{exc}")

    details = [f"{item.label()}  分数 {item.score()}" for item in report.worst(5) if not item.ok]
    for digest, names in report.duplicates.items():
        details.append(f"重复模板 {digest[:8]}…：{'、'.join(names)}")
    level = LEVEL_ERROR if report.flat else (LEVEL_WARNING if (report.weak or report.duplicates) else LEVEL_INFO)
    return CheckResult(
        name="模板",
        ok=level != LEVEL_ERROR,
        level=level,
        message=report.summary(),
        details=tuple(details),
    )


def check_flows(folder: str | Path | None = None) -> CheckResult:
    from src.core.flow.model import list_flows, load_flow_by_name, validate_flow

    base = Path(folder) if folder is not None else Path("configs") / "flows"
    names = list_flows(base)
    if not names:
        return _missing("流程", base)

    problems: list[str] = []
    for name in names:
        try:
            flow = load_flow_by_name(name, base)
        except Exception as exc:
            problems.append(f"{name}：{exc}")
            continue
        for issue in validate_flow(flow):
            if issue.level == "error":
                problems.append(f"{name} {issue}")
    if problems:
        return CheckResult(name="流程", ok=False, level=LEVEL_ERROR, message=f"{len(problems)} 个问题",
                           details=tuple(problems[:10]))
    return CheckResult(name="流程", message=f"{len(names)} 份都能解析")


def check_queues(folder: str | Path | None = None) -> CheckResult:
    from src.core.queue import list_queues, load_queue

    base = Path(folder) if folder is not None else Path("configs") / "queues"
    names = list_queues(base)
    if not names:
        return _missing("队列", base)

    problems: list[str] = []
    for name in names:
        try:
            queue = load_queue(name, base)
        except Exception as exc:
            problems.append(f"{name}：{exc}")
            continue
        if queue.is_empty():
            problems.append(f"{name}：队列是空的")
    if problems:
        return CheckResult(name="队列", ok=False, level=LEVEL_ERROR, message=f"{len(problems)} 个问题",
                           details=tuple(problems[:10]))
    return CheckResult(name="队列", message=f"{len(names)} 份都能解析")


def check_schemes(folder: str | Path | None = None) -> CheckResult:
    from src.core.replay import list_schemes, load_scheme

    base = Path(folder) if folder is not None else Path("configs") / "schemes"
    names = list_schemes(base)
    if not names:
        return _missing("轮椅方案", base)

    problems: list[str] = []
    for name in names:
        try:
            scheme = load_scheme(name, base)
            scheme.to_flow()  # 拼装一次，暴露分段为空 / 步数超限
        except Exception as exc:
            problems.append(f"{name}：{exc}")
    if problems:
        return CheckResult(name="轮椅方案", ok=False, level=LEVEL_ERROR, message=f"{len(problems)} 个问题",
                           details=tuple(problems[:10]))
    return CheckResult(name="轮椅方案", message=f"{len(names)} 份都能拼装成流程")


def check_routes(folder: str | Path | None = None) -> CheckResult:
    from src.core.route import list_routes, load_route

    base = Path(folder) if folder is not None else Path("configs") / "routes"
    names = list_routes(base)
    if not names:
        return _missing("导航路线", base)

    problems: list[str] = []
    for name in names:
        try:
            route = load_route(name, base)
        except Exception as exc:
            problems.append(f"{name}：{exc}")
            continue
        for issue in route.validate():
            problems.append(f"{name}：{issue}")
    if problems:
        return CheckResult(name="导航路线", ok=False, level=LEVEL_ERROR, message=f"{len(problems)} 个问题",
                           details=tuple(problems[:10]))
    return CheckResult(name="导航路线", message=f"{len(names)} 份都能解析")


def check_config_files(folder: str | Path | None = None) -> CheckResult:
    base = Path(folder) if folder is not None else Path("configs")
    if not base.is_dir():
        return _missing("配置", base)

    files = sorted(base.glob("*.json"))
    if not files:
        return _missing("配置", base)

    problems: list[str] = []
    for path in files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{path.name}：{exc}")
    if problems:
        return CheckResult(name="配置", ok=False, level=LEVEL_ERROR, message=f"{len(problems)} 个文件坏了",
                           details=tuple(problems[:10]))
    return CheckResult(name="配置", message=f"{len(files)} 个 JSON 都能解析")


def check_i18n(root: str | Path = ".") -> CheckResult:
    base = Path(root) / "i18n"
    if not base.is_dir():
        return CheckResult(name="i18n", ok=True, message="没有 i18n 目录（跳过）")

    missing: list[str] = []
    empty: list[str] = []
    for lang in LANGS:
        po_path = base / lang / "LC_MESSAGES" / "ok.po"
        if not po_path.is_file():
            missing.append(lang)
            continue
        try:
            text = po_path.read_text(encoding="utf-8")
        except OSError as exc:
            missing.append(f"{lang}（{exc}）")
            continue
        if not text.strip():
            missing.append(f"{lang}（空文件）")
            continue
        try:
            import polib

            for entry in polib.pofile(str(po_path)):
                if entry.msgid and not entry.obsolete and not entry.msgstr and not entry.msgstr_plural:
                    empty.append(f"{lang}：{entry.msgid}")
        except ImportError:
            continue
    problems = missing + empty[:10]
    if problems:
        return CheckResult(name="i18n", ok=False, level=LEVEL_ERROR, message=f"{len(problems)} 个问题",
                           details=tuple(problems[:10]))
    return CheckResult(name="i18n", message=f"{len(LANGS)} 个语种齐全")


CHECKS = (
    check_templates,
    check_flows,
    check_queues,
    check_schemes,
    check_routes,
    check_config_files,
    check_i18n,
)


def run_all(root: str | Path = ".", configs: str | Path | None = None) -> HealthReport:
    """跑全部检查；任何一项都不允许把整体搞崩。"""
    base = Path(root)
    config_root = Path(configs) if configs is not None else base / "configs"
    report = HealthReport()
    for check in CHECKS:
        try:
            if check in (check_templates, check_i18n):
                report.results.append(check(base))
            elif check is check_config_files:
                report.results.append(check(config_root))
            else:
                folder = {
                    check_flows: "flows",
                    check_queues: "queues",
                    check_schemes: "schemes",
                    check_routes: "routes",
                }[check]
                report.results.append(check(config_root / folder))
        except Exception as exc:  # 防御：单项检查自身出错也不能中断体检
            report.results.append(
                CheckResult(name=check.__name__, ok=False, level=LEVEL_ERROR, message=f"检查本身出错：{exc}")
            )
    return report
