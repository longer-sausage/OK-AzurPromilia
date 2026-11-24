"""「工具与诊断」页：失败帧复测 + 素材包安装。

页面刻意做成懒加载（首次显示时才构建控件），避免拖慢启动；
复测要跑完全部模板（几十次模板匹配，约 1~3 秒），放在子线程里跑，
不阻塞 UI。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    TableWidget,
)

from src.core.asset_pack import install_pack
from src.core.diagnostics_bundle import build_bundle
from src.core.failure_frame_store import FailureFrameStore
from src.core.health_check import run_all
from src.core.lang_pack import apply_pack, list_backups, restore_backup, scan_packs
from src.core.template_audit import TemplateAuditError, audit_directory
from src.core.template_rescorer import TemplateRescorer

PREBUILD_DELAY_MS = 3000
PREVIEW_WIDTH = 480
PREVIEW_HEIGHT = 270
COLUMNS = ("模板", "得分", "阈值", "判定", "位置")


def _bgr_to_pixmap(frame, width: int = PREVIEW_WIDTH, height: int = PREVIEW_HEIGHT) -> QPixmap | None:
    """把 cv2 的 BGR 帧转成按比例缩放后的 QPixmap。"""
    if frame is None:
        return None
    try:
        height_px, width_px = frame.shape[:2]
        if frame.ndim == 2:
            image = QImage(frame.data, width_px, height_px, width_px, QImage.Format_Grayscale8)
        else:
            image = QImage(frame.data, width_px, height_px, width_px * 3, QImage.Format_BGR888)
        pixmap = QPixmap.fromImage(image.copy())
        return pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return None


class _ScoreItem(QTableWidgetItem):
    """带数值排序键的表格项（模板得分要按浮点比较，不能按字符串）。"""

    def __init__(self, text: str, sort_key=None):
        super().__init__(text)
        self._sort_key = sort_key if sort_key is not None else text

    def __lt__(self, other):
        try:
            return self._sort_key < other._sort_key
        except Exception:
            return super().__lt__(other)


class _RescoreWorker(QThread):
    """在子线程里跑全模板复测。"""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, rescorer: TemplateRescorer, frame, parent=None):
        super().__init__(parent)
        self._rescorer = rescorer
        self._frame = frame

    def run(self):
        try:
            result = self._rescorer.rescore(self._frame)
            self.done.emit(result)
        except Exception as exc:  # 子线程里必须自己兜住异常
            self.failed.emit(str(exc))


class DiagnosticsTab(CustomTab):
    """工具与诊断页。"""

    def __init__(self):
        super().__init__()
        self._built = False
        self._build_scheduled = False
        self._records: list = []
        self._worker: _RescoreWorker | None = None
        self._current_pixmap: QPixmap | None = None

        self.frame_combo: ComboBox | None = None
        self.preview: QLabel | None = None
        self.status_label: BodyLabel | None = None
        self.table: TableWidget | None = None
        self.rescore_button: PrimaryPushButton | None = None
        self.refresh_button: PushButton | None = None
        self.open_dir_button: PushButton | None = None
        self.clear_button: PushButton | None = None
        self.pack_input: LineEdit | None = None
        self.pack_status: BodyLabel | None = None
        self.install_button: PrimaryPushButton | None = None
        self.browse_button: PushButton | None = None
        self.include_shots: CheckBox | None = None
        self.bundle_status: BodyLabel | None = None
        self.lang_pack_combo: ComboBox | None = None
        self.lang_pack_status: BodyLabel | None = None
        self.audit_detail: QPlainTextEdit | None = None
        self.audit_status: BodyLabel | None = None
        self.health_detail: QPlainTextEdit | None = None
        self.health_status: BodyLabel | None = None
        self._packs: list = []

        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._schedule_build)

    # ── CustomTab 元信息 ─────────────────────────────────
    @property
    def name(self):
        return self.tr("工具与诊断")

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return True

    @property
    def icon(self):
        return FluentIcon.DEVELOPER_TOOLS

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_build()
        if self._built:
            self._refresh_frames()
            self.refresh_lang_packs()

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
        self.add_card(self.tr("失败帧复测"), self._build_failure_frame_card())
        self.add_card(self.tr("模板自检"), self._build_template_audit_card())
        self.add_card(self.tr("安装自检"), self._build_health_check_card())
        self.add_card(self.tr("素材包安装"), self._build_asset_pack_card())
        self.add_card(self.tr("诊断包"), self._build_bundle_card())
        self.add_card(self.tr("语言包"), self._build_lang_pack_card())
        QTimer.singleShot(0, self._refresh_frames)
        QTimer.singleShot(0, self.refresh_lang_packs)

    def _build_template_audit_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("检查全部模板的纹理与重复（纯色块永远匹配不上）")))
        audit = PrimaryPushButton(FluentIcon.CHECKBOX, self.tr("开始自检"))
        audit.clicked.connect(self._run_template_audit)
        row.addWidget(audit)
        row.addStretch(1)
        layout.addLayout(row)

        self.audit_detail = QPlainTextEdit()
        self.audit_detail.setReadOnly(True)
        self.audit_detail.setFixedHeight(120)
        layout.addWidget(self.audit_detail)

        self.audit_status = BodyLabel(self.tr("还没自检；自检只读 assets/coco_annotations.json，不改任何文件"))
        self.audit_status.setWordWrap(True)
        layout.addWidget(self.audit_status)
        return card

    def _run_template_audit(self):
        try:
            report = audit_directory(os.getcwd())
        except TemplateAuditError as exc:
            self._set_audit_status(str(exc))
            self._notify(str(exc), error=True)
            return
        lines = [report.summary(), ""]
        for item in report.worst(12):
            reasons = f"（{'；'.join(item.reasons)}）" if item.reasons else ""
            lines.append(f"{item.label()}  分数 {item.score()}{reasons}")
        if report.duplicates:
            lines.append("")
            lines.append(self.tr("重复模板："))
            for digest, names in report.duplicates.items():
                lines.append(f"  {digest[:8]}…  {'、'.join(names)}")
        self._set_audit_detail("\n".join(lines))
        self._set_audit_status(
            self.tr("自检完成：{summary}").format(summary=report.summary())
        )
        self._notify(
            self.tr("自检完成：{summary}").format(summary=report.summary()),
            error=bool(report.flat or report.duplicates),
        )

    def _set_audit_detail(self, text: str):
        if self.audit_detail is not None:
            self.audit_detail.setPlainText(text)

    def _set_audit_status(self, text: str):
        if self.audit_status is not None:
            self.audit_status.setText(text)

    def _build_bundle_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("把配置、日志、模板清单、失败帧与系统信息打成一个 zip")))
        self.include_shots = CheckBox(self.tr("包含截图"))
        row.addWidget(self.include_shots)
        generate = PrimaryPushButton(FluentIcon.SAVE, self.tr("生成诊断包"))
        generate.clicked.connect(self._make_bundle)
        row.addWidget(generate)
        row.addStretch(1)
        layout.addLayout(row)

        self.bundle_status = BodyLabel(self.tr("默认不含截图；记账只放合计，不放明细"))
        self.bundle_status.setWordWrap(True)
        layout.addWidget(self.bundle_status)
        return card

    def _build_lang_pack_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("语言包")))
        self.lang_pack_combo = ComboBox()
        self.lang_pack_combo.setMinimumWidth(320)
        row.addWidget(self.lang_pack_combo, 1)

        for text, icon, slot in (
            (self.tr("刷新"), FluentIcon.SYNC, self.refresh_lang_packs),
            (self.tr("应用"), FluentIcon.CHECKBOX, self._apply_lang_pack),
            (self.tr("还原最近备份"), FluentIcon.HISTORY, self._restore_lang_pack),
        ):
            button = PushButton(icon, text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)

        self.lang_pack_status = BodyLabel(
            self.tr("扫描程序目录下的「语言包<代码>」文件夹；应用前自动备份 i18n 与 assets/lang")
        )
        self.lang_pack_status.setWordWrap(True)
        layout.addWidget(self.lang_pack_status)
        return card

    def _build_failure_frame_card(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_layout.addWidget(BodyLabel(self.tr("失败帧列表")))
        self.frame_combo = ComboBox()
        self.frame_combo.setMinimumWidth(420)
        self.frame_combo.currentIndexChanged.connect(self._on_frame_selected)
        left_layout.addWidget(self.frame_combo, 1)

        self.refresh_button = PushButton(FluentIcon.SYNC, self.tr("刷新"))
        self.refresh_button.clicked.connect(self._refresh_frames)
        self.open_dir_button = PushButton(FluentIcon.FOLDER, self.tr("打开目录"))
        self.open_dir_button.clicked.connect(self._open_frame_dir)
        self.clear_button = PushButton(FluentIcon.DELETE, self.tr("清空"))
        self.clear_button.clicked.connect(self._clear_frames)
        for button in (self.refresh_button, self.open_dir_button, self.clear_button):
            left_layout.addWidget(button)
        layout.addWidget(left, 1)

        self.rescore_button = PrimaryPushButton(FluentIcon.SEARCH, self.tr("复测全部模板"))
        self.rescore_button.clicked.connect(self._start_rescore)
        layout.addWidget(self.rescore_button)

        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(8)
        card_layout.addWidget(container)

        self.status_label = BodyLabel(self.tr("失败帧库为空：任务失败时会自动保存识别帧"))
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.status_label)

        body = QHBoxLayout()
        body.setSpacing(12)

        self.preview = QLabel()
        self.preview.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("border: 1px solid rgba(128,128,128,96); border-radius: 4px;")
        body.addWidget(self.preview, 0)

        self.table = TableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels([self.tr(column) for column in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(TableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectRows)
        self.table.setMinimumHeight(PREVIEW_HEIGHT)
        self.table.horizontalHeader().setStretchLastSection(True)
        body.addWidget(self.table, 1)

        card_layout.addLayout(body)
        return card

    def _build_health_check_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("把流程 / 队列 / 方案 / 路线 / 配置 / i18n 一起体检一遍")))
        run_button = PrimaryPushButton(FluentIcon.CERTIFICATE, self.tr("开始自检"))
        run_button.clicked.connect(self._run_health_check)
        row.addWidget(run_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.health_detail = QPlainTextEdit()
        self.health_detail.setReadOnly(True)
        self.health_detail.setFixedHeight(150)
        layout.addWidget(self.health_detail)

        self.health_status = BodyLabel(self.tr("还没自检；自检只读文件，不改任何东西"))
        self.health_status.setWordWrap(True)
        layout.addWidget(self.health_status)
        return card

    def _run_health_check(self):
        try:
            report = run_all(os.getcwd())
        except Exception as exc:
            self._set_health_status(self.tr("自检失败：{error}").format(error=exc))
            self._notify(self.tr("自检失败：{error}").format(error=exc), error=True)
            return
        if self.health_detail is not None:
            self.health_detail.setPlainText("\n".join(report.lines()))
        self._set_health_status(report.summary())
        self._notify(report.summary(), error=not report.ok)

    def _set_health_status(self, text: str):
        if self.health_status is not None:
            self.health_status.setText(text)

    def _build_asset_pack_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("素材包路径或 URL")))
        self.pack_input = LineEdit()
        self.pack_input.setPlaceholderText(self.tr("本地 zip 路径，或 http(s) 直链"))
        row.addWidget(self.pack_input, 1)

        self.browse_button = PushButton(FluentIcon.FOLDER, self.tr("浏览…"))
        self.browse_button.clicked.connect(self._browse_pack)
        row.addWidget(self.browse_button)

        self.install_button = PrimaryPushButton(FluentIcon.DOWNLOAD, self.tr("安装"))
        self.install_button.clicked.connect(self._install_pack)
        row.addWidget(self.install_button)
        layout.addLayout(row)

        self.pack_status = BodyLabel(
            self.tr("安装前会逐个校验 SHA-256；校验不通过不会改动任何文件，旧素材会带时间戳备份。")
        )
        self.pack_status.setWordWrap(True)
        layout.addWidget(self.pack_status)
        return card

    # ── 失败帧：读取与预览 ───────────────────────────────
    def _store(self) -> FailureFrameStore:
        return FailureFrameStore()

    def _refresh_frames(self):
        if not self._built:
            return
        store = self._store()
        self._records = store.list()
        if self.frame_combo is not None:
            self.frame_combo.blockSignals(True)
            self.frame_combo.clear()
            for record in self._records:
                self.frame_combo.addItem(record.label())
            self.frame_combo.blockSignals(False)
        if not self._records:
            self._set_status(self.tr("失败帧库为空：任务失败时会自动保存识别帧"))
            self._clear_table()
            if self.preview is not None:
                self.preview.clear()
            self._current_pixmap = None
            return
        if self.frame_combo is not None:
            self.frame_combo.setCurrentIndex(0)
        self._on_frame_selected(0)

    def _on_frame_selected(self, index: int):
        if index < 0 or index >= len(self._records):
            return
        record = self._records[index]
        frame = self._store().load_image(record)
        pixmap = _bgr_to_pixmap(frame)
        self._current_pixmap = pixmap
        if self.preview is not None:
            if pixmap is not None:
                self.preview.setPixmap(pixmap)
            else:
                self.preview.clear()
        self._set_status(
            self.tr("{time}　{task}　{width}×{height}　{message}").format(
                time=record.created_text,
                task=record.task or self.tr("未知任务"),
                width=record.width,
                height=record.height,
                message=record.message,
            )
        )

    def _clear_table(self):
        if self.table is not None:
            self.table.setRowCount(0)

    def _set_status(self, text: str):
        if self.status_label is not None:
            self.status_label.setText(text)

    def _open_frame_dir(self):
        root = self._store().root
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(root))
        except Exception as exc:
            self._notify(self.tr("无法打开目录：{error}").format(error=exc), error=True)

    def _clear_frames(self):
        removed = self._store().clear()
        self._records = []
        self._refresh_frames()
        self._notify(self.tr("已清空 {count} 个失败帧").format(count=removed))

    # ── 复测 ─────────────────────────────────────────────
    def _current_frame(self):
        index = self.frame_combo.currentIndex() if self.frame_combo is not None else -1
        if index < 0 or index >= len(self._records):
            return None
        return self._store().load_image(self._records[index])

    def _feature_set(self):
        executor = getattr(self, "executor", None)
        feature_set = getattr(executor, "feature_set", None) if executor is not None else None
        if feature_set is None:
            try:
                from ok import og

                feature_set = getattr(getattr(og, "executor", None), "feature_set", None)
            except Exception:
                feature_set = None
        return feature_set

    def _display_threshold(self) -> float:
        try:
            from src.config import config as app_config

            return float(
                app_config.get("template_matching", {}).get("default_threshold", 0.8)
            )
        except Exception:
            return 0.8

    def _start_rescore(self):
        if self._worker is not None and self._worker.isRunning():
            return
        frame = self._current_frame()
        if frame is None:
            self._notify(self.tr("请选择一个失败帧"), error=True)
            return
        feature_set = self._feature_set()
        if feature_set is None:
            self._notify(self.tr("未识别到可用的模板特征集，请先连接游戏"), error=True)
            return

        width = getattr(feature_set, "width", 0) or (frame.shape[1] if frame is not None else 1920)
        rescorer = TemplateRescorer.from_feature_set(
            feature_set, width=int(width), threshold=self._display_threshold()
        )
        self._clear_table()
        self._set_status(self.tr("复测中…正在用这一帧跑全部模板"))
        if self.rescore_button is not None:
            self.rescore_button.setEnabled(False)

        self._worker = _RescoreWorker(rescorer, frame, self)
        self._worker.done.connect(self._on_rescore_done)
        self._worker.failed.connect(self._on_rescore_failed)
        self._worker.start()

    def _on_rescore_done(self, result):
        if self.rescore_button is not None:
            self.rescore_button.setEnabled(True)
        self._fill_table(result)
        self._set_status(
            self.tr("复测完成：{summary}").format(summary=result.summary())
        )
        self._notify(self.tr("复测完成：{summary}").format(summary=result.summary()))

    def _on_rescore_failed(self, error: str):
        if self.rescore_button is not None:
            self.rescore_button.setEnabled(True)
        self._set_status(self.tr("复测失败：{error}").format(error=error))
        self._notify(self.tr("复测失败：{error}").format(error=error), error=True)

    def _fill_table(self, result):
        if self.table is None:
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(result.scores))
        for row, score in enumerate(result.scores):
            center = score.center()
            position = f"{center[0]}, {center[1]}" if center else "-"
            cells = (
                _ScoreItem(score.name, score.name),
                _ScoreItem(f"{score.score:.3f}", score.score),
                _ScoreItem(f"{score.threshold:.2f}", score.threshold),
                _ScoreItem(score.verdict(), score.score),
                _ScoreItem(position, position),
            )
            color = None
            if not score.hit:
                color = QColor("#e07b39") if score.gap <= 0.08 else QColor("#d9534f")
            elif score.gap < 0:
                color = QColor("#3c9e4d")
            for column, item in enumerate(cells):
                if color is not None and column in (1, 3):
                    item.setForeground(color)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.DescendingOrder)
        self.table.resizeColumnsToContents()

    # ── 素材包 ───────────────────────────────────────────
    def _browse_pack(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, self.tr("选择素材包"), "", self.tr("素材包文件 (*.zip)")
        )
        if path and self.pack_input is not None:
            self.pack_input.setText(path)

    def _install_pack(self):
        text = self.pack_input.text().strip() if self.pack_input is not None else ""
        if not text:
            self._notify(self.tr("请填写素材包路径或 URL"), error=True)
            return
        if self.install_button is not None:
            self.install_button.setEnabled(False)
        self._set_pack_status(self.tr("正在校验素材包…"))
        try:
            zip_path = self._resolve_pack_path(text)
            report = install_pack(zip_path, os.getcwd(), on_installed=self._after_install)
        except Exception as exc:
            self._set_pack_status(self.tr("安装失败：{error}").format(error=exc))
            self._notify(self.tr("安装失败：{error}").format(error=exc), error=True)
            return
        finally:
            if self.install_button is not None:
                self.install_button.setEnabled(True)

        self._set_pack_status(report.summary())
        self._notify(report.summary(), error=not report.ok)

    def _resolve_pack_path(self, text: str) -> Path:
        if text.lower().startswith(("http://", "https://")):
            from src.core.asset_pack import download_pack

            self._set_pack_status(self.tr("正在下载素材包…"))
            return download_pack(text, Path(os.getcwd()) / "downloads")
        return Path(text)

    def _after_install(self, report, manifest):
        """安装成功后刷新框架的模板缓存，否则新素材不会生效。

        见 ``src/core/feature_reload.py``：``process_data()`` 会先清空再重建缓存，
        所以必须在它自己的锁里调用；任务正在跑时明确告警，而不是假装无事发生。
        """
        from src.core.feature_reload import reload_features

        executor = getattr(self, "executor", None)
        if executor is None:
            with contextlib.suppress(Exception):
                from ok import og

                executor = getattr(og, "executor", None)
        reloaded = reload_features(executor, log=self.logger.info)
        if not reloaded:
            self._set_pack_status(self.tr("素材已安装，但模板缓存刷新失败；重启程序后生效"))
        with contextlib.suppress(Exception):
            from src.patches.ocr_text_fix_patch import reload_ocr_text_fix

            reload_ocr_text_fix()

    def _set_pack_status(self, text: str):
        if self.pack_status is not None:
            self.pack_status.setText(text)

    # ── 诊断包 ───────────────────────────────────────────
    def _make_bundle(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("选择保存目录"), os.getcwd())
        if not folder:
            return
        include_shots = bool(self.include_shots and self.include_shots.isChecked())
        result = build_bundle(dest_dir=folder, app_root=os.getcwd(), include_screenshots=include_shots)
        text = result.summary()
        if self.bundle_status is not None:
            self.bundle_status.setText(text)
        self._notify(text, error=not result.ok)

    # ── 语言包 ───────────────────────────────────────────
    def refresh_lang_packs(self):
        if self.lang_pack_combo is None:
            return
        self._packs = scan_packs(os.getcwd())
        self.lang_pack_combo.blockSignals(True)
        self.lang_pack_combo.clear()
        if self._packs:
            for pack in self._packs:
                self.lang_pack_combo.addItem(pack.label())
        else:
            self.lang_pack_combo.addItem(self.tr("（没有找到语言包）"))
        self.lang_pack_combo.blockSignals(False)
        if self._packs:
            self._set_lang_status(
                self.tr("找到 {count} 个语言包；应用前会自动备份 i18n 与 assets/lang").format(
                    count=len(self._packs)
                )
            )
        else:
            self._set_lang_status(self.tr("把「语言包<代码>」文件夹放到程序目录下再刷新"))

    def _current_pack(self):
        if not self._packs or self.lang_pack_combo is None:
            return None
        index = self.lang_pack_combo.currentIndex()
        return self._packs[index] if 0 <= index < len(self._packs) else None

    def _apply_lang_pack(self):
        pack = self._current_pack()
        if pack is None:
            self._notify(self.tr("没有选中语言包"), error=True)
            return
        result = apply_pack(pack.path, os.getcwd())
        self._set_lang_status(result.summary())
        self._notify(result.summary(), error=not result.ok)

    def _restore_lang_pack(self):
        backups = list_backups()
        if not backups:
            self._notify(self.tr("没有可用的语言包备份"), error=True)
            return
        result = restore_backup(backups[0], os.getcwd())
        self._set_lang_status(result.summary() if result.ok else result.message)
        self._notify(
            self.tr("已还原 {name}").format(name=backups[0].name) if result.ok else result.message,
            error=not result.ok,
        )

    def _set_lang_status(self, text: str):
        if self.lang_pack_status is not None:
            self.lang_pack_status.setText(text)

    # ── 通知 ─────────────────────────────────────────────
    def _notify(self, message: str, error: bool = False):
        try:
            window = self.window()
            if error:
                InfoBar.error(
                    title=self.tr("工具与诊断"),
                    content=message,
                    orient=Qt.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.BOTTOM_RIGHT,
                    duration=5000,
                    parent=window,
                )
            else:
                InfoBar.success(
                    title=self.tr("工具与诊断"),
                    content=message,
                    orient=Qt.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.BOTTOM_RIGHT,
                    duration=3000,
                    parent=window,
                )
        except Exception:
            self.logger.info(message)
