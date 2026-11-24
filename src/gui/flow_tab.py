"""「流程与队列」页：管理 JSON 流程、任务队列与配置档。

这里只做**管理**（列表 / 校验 / 导入 / 删除 / 断点），不在这里直接发键鼠：
真正执行走 `FlowTask`（ok-script 的任务体系有暂停、停止、调度与账号上下文，
在界面线程里直接跑自动化容易和正在运行的任务抢输入）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
)

from src.core.flow import (
    FLOW_FOLDER,
    CheckpointStore,
    Flow,
    FlowFormatError,
    flow_path,
    list_flows,
    load_flow_by_name,
    save_flow,
    validate_flow,
)
from src.core.flow.recording import MAX_RECORDING_STEPS, convert_recording
from src.core.flow.templates import install_all_examples, load_template, missing_templates
from src.core.profile_store import ProfileError, ProfileStore
from src.core.queue import QUEUE_FOLDER, QueueFormatError, TaskQueue, list_queues, load_queue, save_queue

PREBUILD_DELAY_MS = 3000
DETAIL_HEIGHT = 150


class FlowTab(CustomTab):
    """流程 / 队列 / 配置档管理页。"""

    def __init__(self):
        super().__init__()
        self._built = False
        self._build_scheduled = False
        self._flows: list[str] = []
        self._queues: list[str] = []
        self._profiles: list[str] = []

        self.flow_combo: ComboBox | None = None
        self.flow_detail: QPlainTextEdit | None = None
        self.flow_status: BodyLabel | None = None
        self.queue_combo: ComboBox | None = None
        self.queue_detail: QPlainTextEdit | None = None
        self.queue_status: BodyLabel | None = None
        self.profile_combo: ComboBox | None = None
        self.profile_name: LineEdit | None = None
        self.profile_status: BodyLabel | None = None
        self.checkpoint_status: BodyLabel | None = None
        self.record_button: PushButton | None = None
        self.record_status: BodyLabel | None = None
        self._recording = False

        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._schedule_build)

    # ── CustomTab 元信息 ─────────────────────────────────
    @property
    def name(self):
        return self.tr("流程与队列")

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return True

    @property
    def icon(self):
        return FluentIcon.LIBRARY

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_build()
        if self._built:
            self.refresh_all()

    # ── 构建 ─────────────────────────────────────────────
    def _schedule_build(self):
        if self._build_scheduled:
            return
        self._build_scheduled = True
        QTimer.singleShot(0, self, self._build)

    def _build(self):
        if self._built:
            return
        self._built = True
        self.add_card(self.tr("流程"), self._build_flow_card())
        self.add_card(self.tr("任务队列"), self._build_queue_card())
        self.add_card(self.tr("配置档"), self._build_profile_card())
        QTimer.singleShot(0, self.refresh_all)

    def _build_flow_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("流程文件")))
        self.flow_combo = ComboBox()
        self.flow_combo.setMinimumWidth(320)
        self.flow_combo.currentIndexChanged.connect(self._on_flow_selected)
        row.addWidget(self.flow_combo, 1)

        for text, icon, slot in (
            (self.tr("刷新"), FluentIcon.SYNC, self.refresh_flows),
            (self.tr("校验"), FluentIcon.CHECKBOX, self._validate_current_flow),
            (self.tr("导入 JSON"), FluentIcon.ADD, self._import_flow),
            (self.tr("从录制 JSON 生成"), FluentIcon.PHOTO, self._import_recording),
            (self.tr("安装示例"), FluentIcon.DOWNLOAD, self._install_flow_templates),
            (self.tr("打开目录"), FluentIcon.FOLDER, lambda: self._open_dir(FLOW_FOLDER)),
            (self.tr("删除"), FluentIcon.DELETE, self._delete_flow),
        ):
            button = PushButton(icon, text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)

        # 录制动作：真正监听键鼠（框架 Recorder），停止后直接变成流程文件
        record_row = QHBoxLayout()
        record_row.setSpacing(6)
        self.record_button = PushButton(FluentIcon.MICROPHONE, self.tr("开始录制动作"))
        self.record_button.clicked.connect(self._toggle_recording)
        record_row.addWidget(self.record_button)
        self.record_status = BodyLabel(
            self.tr("录制会按当前游戏窗口换算坐标；录制期间请在游戏里正常操作，停止后自动生成流程")
        )
        self.record_status.setWordWrap(True)
        record_row.addWidget(self.record_status, 1)
        layout.addLayout(record_row)

        self.flow_detail = QPlainTextEdit()
        self.flow_detail.setReadOnly(True)
        self.flow_detail.setFixedHeight(DETAIL_HEIGHT)
        layout.addWidget(self.flow_detail)

        self.flow_status = BodyLabel(self.tr("还没有流程文件：用「导入 JSON」或「从录制 JSON 生成」创建"))
        self.flow_status.setWordWrap(True)
        layout.addWidget(self.flow_status)

        self.checkpoint_status = BodyLabel("")
        self.checkpoint_status.setWordWrap(True)
        layout.addWidget(self.checkpoint_status)
        return card

    def _build_queue_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("队列文件")))
        self.queue_combo = ComboBox()
        self.queue_combo.setMinimumWidth(320)
        self.queue_combo.currentIndexChanged.connect(self._on_queue_selected)
        row.addWidget(self.queue_combo, 1)

        for text, icon, slot in (
            (self.tr("刷新"), FluentIcon.SYNC, self.refresh_queues),
            (self.tr("校验"), FluentIcon.CHECKBOX, self._validate_current_queue),
            (self.tr("新建模板"), FluentIcon.ADD, self._create_queue_template),
            (self.tr("打开目录"), FluentIcon.FOLDER, lambda: self._open_dir(QUEUE_FOLDER)),
            (self.tr("删除"), FluentIcon.DELETE, self._delete_queue),
        ):
            button = PushButton(icon, text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)

        self.queue_detail = QPlainTextEdit()
        self.queue_detail.setReadOnly(True)
        self.queue_detail.setFixedHeight(DETAIL_HEIGHT)
        layout.addWidget(self.queue_detail)

        self.queue_status = BodyLabel(
            self.tr("队列在「流程 / 队列」任务里执行：任务类型选「执行队列」并填队列名字")
        )
        self.queue_status.setWordWrap(True)
        layout.addWidget(self.queue_status)
        return card

    def _build_profile_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("配置档")))
        self.profile_combo = ComboBox()
        self.profile_combo.setMinimumWidth(320)
        row.addWidget(self.profile_combo, 1)

        self.profile_name = LineEdit()
        self.profile_name.setPlaceholderText(self.tr("新配置档名字"))
        row.addWidget(self.profile_name, 1)

        save_button = PrimaryPushButton(FluentIcon.SAVE, self.tr("保存当前配置"))
        save_button.clicked.connect(self._save_profile)
        row.addWidget(save_button)

        apply_button = PushButton(FluentIcon.SYNC, self.tr("应用"))
        apply_button.clicked.connect(self._apply_profile)
        row.addWidget(apply_button)

        delete_button = PushButton(FluentIcon.DELETE, self.tr("删除"))
        delete_button.clicked.connect(self._delete_profile)
        row.addWidget(delete_button)
        layout.addLayout(row)

        self.profile_status = BodyLabel(
            self.tr("应用配置档前会自动把当前 configs/ 备份到 configs/profiles/.backup_<时间戳>/")
        )
        self.profile_status.setWordWrap(True)
        layout.addWidget(self.profile_status)
        return card

    # ── 刷新 ─────────────────────────────────────────────
    def refresh_all(self):
        self.refresh_flows()
        self.refresh_queues()
        self.refresh_profiles()

    def refresh_flows(self):
        self._flows = list_flows()
        self._fill_combo(self.flow_combo, self._flows, self.tr("（没有流程）"))
        self._on_flow_selected(self.flow_combo.currentIndex() if self.flow_combo else -1)

    def refresh_queues(self):
        self._queues = list_queues()
        self._fill_combo(self.queue_combo, self._queues, self.tr("（没有队列）"))
        self._on_queue_selected(self.queue_combo.currentIndex() if self.queue_combo else -1)

    def refresh_profiles(self):
        self._profiles = [info.name for info in ProfileStore().list()]
        self._fill_combo(self.profile_combo, self._profiles, self.tr("（没有配置档）"))

    def _fill_combo(self, combo: ComboBox | None, names: list[str], placeholder: str):
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        if names:
            combo.addItems(names)
        else:
            combo.addItem(placeholder)
        combo.blockSignals(False)

    # ── 流程 ─────────────────────────────────────────────
    def _current_flow_name(self) -> str:
        if self.flow_combo is None or not self._flows:
            return ""
        index = self.flow_combo.currentIndex()
        return self._flows[index] if 0 <= index < len(self._flows) else ""

    def _on_flow_selected(self, index: int):
        name = self._current_flow_name()
        if not name:
            self._set_detail(self.flow_detail, self.tr("还没有流程文件"))
            self._set_status(self.flow_status, self.tr("用「导入 JSON」或「从录制 JSON 生成」创建第一份流程"))
            self._set_status(self.checkpoint_status, "")
            return
        try:
            flow = load_flow_by_name(name)
        except FlowFormatError as exc:
            self._set_detail(self.flow_detail, f"{name}: {exc}")
            return
        self._set_detail(self.flow_detail, self._describe_flow(flow))
        self._set_status(
            self.flow_status,
            self.tr("共 {steps} 步，嵌套 {depth} 层，容器 {containers} 个").format(
                steps=flow.step_count(), depth=flow.depth(), containers=flow.container_count()
            ),
        )
        completed = CheckpointStore().for_flow(flow).load()
        if completed:
            self._set_status(
                self.checkpoint_status,
                self.tr("断点：已完成 {completed}/{total} 个顶层步骤，任务里勾「从断点继续」即可续跑").format(
                    completed=completed, total=len(flow.steps)
                ),
            )
        else:
            self._set_status(self.checkpoint_status, self.tr("断点：无"))

    def _describe_flow(self, flow: Flow) -> str:
        lines = [f"{flow.name or flow.key}  ({flow.flow_id or flow.key})"]
        if flow.description:
            lines.append(flow.description)
        lines.append("")
        for index, step in enumerate(flow.steps):
            lines.append(f"{index + 1:>2}. {self._describe_step(step, 0)}")
        return "\n".join(lines)

    def _describe_step(self, step, indent: int) -> str:
        pad = "   " * indent
        detail = {
            "wait": f"{step.seconds}s",
            "click": (
                (f"({step.x}, {step.y})" if step.x is not None else f"模板 {step.template}")
                + (f" 按住 {step.hold}s" if step.hold else "")
            ),
            "key": f"{step.key} {step.duration}s",
            "move": step.direction or f"Δ({step.dx}, {step.dy})",
            "action": step.action,
            "wait_template": f"{step.template} {step.expect}",
            "loop": f"times={step.times}" if step.times else f"until {step.until_template or step.until_not_template}",
            "if": f"if {step.template}",
        }.get(step.type, "")
        text = f"{pad}{step.type} {detail}"
        children = list(step.steps)
        if children:
            text += "\n" + "\n".join(self._describe_step(child, indent + 1) for child in children)
        if step.else_steps:
            text += "\n" + pad + "else:\n" + "\n".join(
                self._describe_step(child, indent + 1) for child in step.else_steps
            )
        return text

    def _validate_current_flow(self):
        name = self._current_flow_name()
        if not name:
            self._notify(self.tr("没有选中流程"), error=True)
            return
        try:
            flow = load_flow_by_name(name)
        except FlowFormatError as exc:
            self._set_status(self.flow_status, str(exc))
            self._notify(str(exc), error=True)
            return
        issues = validate_flow(flow)
        errors = [issue for issue in issues if issue.level == "error"]
        lines = [str(issue) for issue in issues] or [self.tr("校验通过，没有发现问题")]
        self._set_detail(self.flow_detail, "\n".join(lines))
        summary = self.tr("校验完成：{errors} 个错误").format(errors=len(errors))
        self._set_status(self.flow_status, summary)
        self._notify(summary, error=bool(errors))

    def _install_flow_templates(self):
        """把 assets 下的示例流程 / 队列 / 路线装进 configs（不覆盖同名）。"""
        try:
            report = install_all_examples()
        except Exception as exc:
            self._notify(self.tr("安装示例失败：{error}").format(error=exc), error=True)
            return
        self.refresh_all()
        self._notify(report.describe(), error=not report.installed)
        # 顺便告诉用户每份流程还缺哪些模板（骨架流程就靠这个清单去标）
        lines = [report.describe()]
        for item in report.installed:
            if not (item.path and item.path.parent.name == "flows"):
                continue
            try:
                flow = load_template(item.name)
                missing = missing_templates(flow)
            except Exception:
                continue
            if missing:
                lines.append(
                    self.tr("{name} 还需要标注：{names}").format(name=item.name, names="、".join(missing))
                )
            else:
                lines.append(self.tr("{name} 所需模板都已存在，可以直接跑").format(name=item.name))
        self._set_status(self.flow_status, "\n".join(lines))

    def _import_flow(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, self.tr("选择流程 JSON"), "", self.tr("JSON 文件 (*.json)")
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
            flow = Flow.from_json(text, source=Path(path).name)
        except (OSError, FlowFormatError) as exc:
            self._notify(self.tr("导入失败：{error}").format(error=exc), error=True)
            return
        target = save_flow(flow)
        self.refresh_flows()
        self._notify(self.tr("已导入流程：{name}").format(name=target.stem))

    # ── 录制动作 ─────────────────────────────────────────
    def _toggle_recording(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        """开始录制：真的挂键鼠监听（框架 ``Recorder``）。"""
        if self._recording:
            return
        running = ""
        try:
            from ok import og

            executor = getattr(og, "executor", None)
            task = getattr(executor, "current_task", None) if executor is not None else None
            running = getattr(task, "name", "") or (task.__class__.__name__ if task else "")
        except Exception:
            running = ""
        if running:
            self._notify(
                self.tr("有任务正在运行（{name}），先停止任务再录制").format(name=running),
                error=True,
            )
            return
        try:
            from ok.ui.qt.tasks.RecordScript import recorder
        except Exception as exc:
            self._notify(self.tr("录制功能不可用：{error}").format(error=exc), error=True)
            return

        target = ""
        try:
            from ok import og

            device_manager = getattr(og, "device_manager", None)
            getter = getattr(device_manager, "get_hwnd_name", None)
            if callable(getter):
                target = str(getter() or "")
        except Exception:
            target = ""
        try:
            recorder.start(target)
        except Exception as exc:
            self._notify(self.tr("开始录制失败：{error}").format(error=exc), error=True)
            return
        self._recording = True
        self.record_button.setText(self.tr("停止录制并生成流程"))
        self._set_status(self.record_status, self.tr("正在录制：请在游戏里操作，录完点「停止录制并生成流程」"))

    def _stop_recording(self):
        events = []
        try:
            from ok.ui.qt.tasks.RecordScript import recorder

            recorder.stop()
            events = list(getattr(recorder, "events", None) or [])
        except Exception as exc:
            self._notify(self.tr("停止录制失败：{error}").format(error=exc), error=True)
        finally:
            self._recording = False
            if getattr(self, "record_button", None) is not None:
                self.record_button.setText(self.tr("开始录制动作"))

        if not events:
            self._set_status(self.record_status, self.tr("没有录到任何操作（检查游戏窗口是否已连接）"))
            self._notify(self.tr("没有录到任何操作"), error=True)
            return

        conversion = convert_recording(events, max_steps=MAX_RECORDING_STEPS)
        name = f"录制流程_{time.strftime('%m%d_%H%M%S')}"
        try:
            flow = Flow.from_dict(conversion.to_flow(name))
            target = save_flow(flow)
        except Exception as exc:
            self._notify(self.tr("生成流程失败：{error}").format(error=exc), error=True)
            return
        self.refresh_flows()
        self._set_status(self.flow_status, conversion.summary())
        self._set_status(self.record_status, self.tr("录制结束：{summary}").format(summary=conversion.summary()))
        self._notify(
            self.tr("已生成流程 {name}：{summary}").format(name=target.stem, summary=conversion.summary()),
            error=conversion.truncated,
        )

    def _import_recording(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, self.tr("选择录制 JSON"), "", self.tr("JSON 文件 (*.json)")
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._notify(self.tr("读取录制失败：{error}").format(error=exc), error=True)
            return
        events = data.get("events") if isinstance(data, dict) else data
        if not isinstance(events, list):
            self._notify(self.tr("录制文件里没有 events 数组"), error=True)
            return

        conversion = convert_recording(events, max_steps=MAX_RECORDING_STEPS)
        flow = Flow.from_dict(conversion.to_flow(Path(path).stem))
        target = save_flow(flow)
        self.refresh_flows()
        self._set_status(self.flow_status, conversion.summary())
        self._notify(
            self.tr("已生成流程 {name}：{summary}").format(name=target.stem, summary=conversion.summary()),
            error=conversion.truncated,
        )

    def _delete_flow(self):
        name = self._current_flow_name()
        if not name:
            return
        try:
            flow_path(name).unlink(missing_ok=True)
        except OSError as exc:
            self._notify(self.tr("删除失败：{error}").format(error=exc), error=True)
            return
        self.refresh_flows()
        self._notify(self.tr("已删除流程：{name}").format(name=name))

    # ── 队列 ─────────────────────────────────────────────
    def _current_queue_name(self) -> str:
        if self.queue_combo is None or not self._queues:
            return ""
        index = self.queue_combo.currentIndex()
        return self._queues[index] if 0 <= index < len(self._queues) else ""

    def _on_queue_selected(self, index: int):
        name = self._current_queue_name()
        if not name:
            self._set_detail(self.queue_detail, self.tr("还没有队列文件"))
            return
        try:
            queue = load_queue(name)
        except QueueFormatError as exc:
            self._set_detail(self.queue_detail, f"{name}: {exc}")
            return
        self._set_detail(self.queue_detail, self._describe_queue(queue))

    def _describe_queue(self, queue: TaskQueue) -> str:
        lines = [
            f"{queue.name}",
            self.tr("失败策略：{policy}").format(
                policy=self.tr("失败即停") if queue.stop_on_failure else self.tr("失败后继续")
            ),
            self.tr("启用 {enabled} 项，总预算 {budget} 分钟").format(
                enabled=len(queue.enabled_items), budget=round(queue.total_budget_minutes(), 1)
            ),
            "",
        ]
        for index, item in enumerate(queue.items):
            state = "" if item.enabled else self.tr("（已禁用）")
            budget = f"{item.budget_minutes} 分钟" if item.budget_minutes else self.tr("不限时")
            lines.append(f"{index + 1:>2}. [{item.kind}] {item.ref}  {budget}{state}")
        return "\n".join(lines)

    def _validate_current_queue(self):
        name = self._current_queue_name()
        if not name:
            self._notify(self.tr("没有选中队列"), error=True)
            return
        try:
            queue = load_queue(name)
        except QueueFormatError as exc:
            self._set_status(self.queue_status, str(exc))
            self._notify(str(exc), error=True)
            return
        self._set_detail(self.queue_detail, self._describe_queue(queue))
        summary = self.tr("队列校验通过：{enabled} 项启用").format(enabled=len(queue.enabled_items))
        self._set_status(self.queue_status, summary)
        self._notify(summary)

    def _create_queue_template(self):
        name = self.tr("新队列")
        queue = TaskQueue(
            name=name,
            items=(),
            stop_on_failure=True,
            description=self.tr("示例队列：把一条龙和流程串起来"),
        )
        save_queue(queue)
        self.refresh_queues()
        self._notify(self.tr("已创建空队列模板：{name}").format(name=name))

    def _delete_queue(self):
        name = self._current_queue_name()
        if not name:
            return
        try:
            from src.core.queue.model import queue_path

            queue_path(name).unlink(missing_ok=True)
        except OSError as exc:
            self._notify(self.tr("删除失败：{error}").format(error=exc), error=True)
            return
        self.refresh_queues()
        self._notify(self.tr("已删除队列：{name}").format(name=name))

    # ── 配置档 ───────────────────────────────────────────
    def _save_profile(self):
        name = self.profile_name.text().strip() if self.profile_name else ""
        if not name:
            self._notify(self.tr("请填写配置档名字"), error=True)
            return
        try:
            info = ProfileStore().save(name)
        except ProfileError as exc:
            self._notify(str(exc), error=True)
            return
        self.refresh_profiles()
        self._set_status(self.profile_status, info.label())
        self._notify(self.tr("已保存配置档：{name}").format(name=info.name))

    def _apply_profile(self):
        if self.profile_combo is None or not self.profiles_exist():
            self._notify(self.tr("没有选中配置档"), error=True)
            return
        name = self.profile_combo.currentText()
        try:
            result = ProfileStore().apply(name)
        except ProfileError as exc:
            self._notify(str(exc), error=True)
            return
        self._set_status(self.profile_status, result.summary())
        self._notify(result.summary())
        self.refresh_all()

    def profiles_exist(self) -> bool:
        return bool(self._profiles)

    def _delete_profile(self):
        if self.profile_combo is None or not self.profiles_exist():
            return
        name = self.profile_combo.currentText()
        try:
            removed = ProfileStore().delete(name)
        except ProfileError as exc:
            self._notify(str(exc), error=True)
            return
        self.refresh_profiles()
        if removed:
            self._notify(self.tr("已删除配置档：{name}").format(name=name))

    # ── 工具 ─────────────────────────────────────────────
    def _open_dir(self, folder: Path):
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            os.startfile(str(Path(folder).resolve()))
        except Exception as exc:
            self._notify(self.tr("无法打开目录：{error}").format(error=exc), error=True)

    def _set_detail(self, widget: QPlainTextEdit | None, text: str):
        if widget is not None:
            widget.setPlainText(text)

    def _set_status(self, widget: BodyLabel | None, text: str):
        if widget is not None:
            widget.setText(text)

    def _notify(self, message: str, error: bool = False):
        try:
            window = self.window()
            factory = InfoBar.error if error else InfoBar.success
            factory(
                title=self.tr("流程与队列"),
                content=message,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=5000 if error else 3000,
                parent=window,
            )
        except Exception:
            self.logger.info(message)
