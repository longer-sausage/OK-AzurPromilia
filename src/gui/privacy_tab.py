"""「隐私遮挡」页：最多 8 块 UID / 昵称 / 聊天的纯色或马赛克遮挡。

坐标一律用 **1920×1080 参考空间**，换分辨率不用重调。左边是块列表，右边是预览
（黑底 + 按比例画出来的遮挡框），下方是当前块的参数编辑。

「坐标拾取」采用的是**延时取点**：点按钮后倒计时 1 秒，然后取当前鼠标位置与
该位置的屏幕颜色，换算成参考坐标填进当前块。全屏十字线覆盖层需要窗口句柄与多屏
坐标换算，且只能在真机上验证，所以先把可靠的取点做了。
"""

from __future__ import annotations

from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
)

from src.core.coord_picker import client_to_reference
from src.core.privacy import (
    MAX_BLOCKS,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    PrivacyBlock,
    PrivacyConfig,
    PrivacyConfigError,
    load_config,
    normalize_blocks,
    save_config,
)

PREBUILD_DELAY_MS = 3000
PREVIEW_WIDTH = 384
PREVIEW_HEIGHT = 216
PICK_DELAY_MS = 1000


class PrivacyPreview(QLabel):
    """黑底 + 按参考坐标比例画出的遮挡框。"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self._config = PrivacyConfig(blocks=())
        self._selected = -1
        self.setStyleSheet("background: #10131a; border: 1px solid rgba(128,128,128,96);")

    def set_config(self, config: PrivacyConfig, selected: int = -1):
        self._config = config
        self._selected = selected
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        scale_x = PREVIEW_WIDTH / REFERENCE_WIDTH
        scale_y = PREVIEW_HEIGHT / REFERENCE_HEIGHT
        for index, block in enumerate(self._config.blocks):
            rect = QRect(
                int(block.x * scale_x),
                int(block.y * scale_y),
                max(1, int(block.width * scale_x)),
                max(1, int(block.height * scale_y)),
            )
            color = QColor(block.color if block.color.startswith("#") else "#000000")
            if not color.isValid():
                color = QColor("#000000")
            painter.fillRect(rect, color)
            pen = QPen(QColor("#4aa3ff") if index == self._selected else QColor("#6b7280"))
            pen.setWidth(2 if index == self._selected else 1)
            painter.setPen(pen)
            painter.drawRect(rect)
        painter.end()


class PrivacyTab(CustomTab):
    """遮挡块的增删改 + 预览 + 保存 + 延时取点。"""

    def __init__(self):
        super().__init__()
        self._built = False
        self._build_scheduled = False
        self._config = load_config()
        self._selected = 0
        self._picking = False

        self.block_list: QListWidget | None = None
        self.preview: PrivacyPreview | None = None
        self.x_box: QSpinBox | None = None
        self.y_box: QSpinBox | None = None
        self.width_box: QSpinBox | None = None
        self.height_box: QSpinBox | None = None
        self.color_edit: QLineEdit | None = None
        self.mode_combo: QComboBox | None = None
        self.label_edit: QLineEdit | None = None
        self.enabled_combo: QComboBox | None = None
        self.status_label: BodyLabel | None = None

        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._schedule_build)

    # ── CustomTab 元信息 ─────────────────────────────────
    @property
    def name(self):
        return self.tr("隐私遮挡")

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return True

    @property
    def icon(self):
        return FluentIcon.PALETTE

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_build()
        if self._built:
            self.reload()

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
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)

        self.block_list = QListWidget()
        self.block_list.setFixedWidth(260)
        self.block_list.setFixedHeight(PREVIEW_HEIGHT)
        self.block_list.currentRowChanged.connect(self._on_select)
        top.addWidget(self.block_list)

        self.preview = PrivacyPreview()
        top.addWidget(self.preview)

        editor = QVBoxLayout()
        editor.setSpacing(4)
        self.x_box = self._spin(0, REFERENCE_WIDTH, "X")
        self.y_box = self._spin(0, REFERENCE_HEIGHT, "Y")
        self.width_box = self._spin(1, REFERENCE_WIDTH, self.tr("宽"))
        self.height_box = self._spin(1, REFERENCE_HEIGHT, self.tr("高"))
        for widget in (self.x_box, self.y_box, self.width_box, self.height_box):
            editor.addWidget(widget)

        self.color_edit = QLineEdit("#000000")
        self.color_edit.setPlaceholderText(self.tr("#RRGGBB"))
        editor.addWidget(self.color_edit)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([self.tr("纯色"), self.tr("马赛克")])
        editor.addWidget(self.mode_combo)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText(self.tr("标签，如 UID / 昵称"))
        editor.addWidget(self.label_edit)

        self.enabled_combo = QComboBox()
        self.enabled_combo.addItems([self.tr("启用遮挡"), self.tr("关闭遮挡")])
        editor.addWidget(self.enabled_combo)
        top.addLayout(editor)
        layout.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(6)
        for text, icon, slot in (
            (self.tr("添加"), FluentIcon.ADD, self._add_block),
            (self.tr("删除"), FluentIcon.DELETE, self._remove_block),
            (self.tr("应用修改"), FluentIcon.EDIT, self._apply_editor),
            (self.tr("坐标拾取"), FluentIcon.MOVE, self._start_pick),
            (self.tr("恢复默认"), FluentIcon.HISTORY, self._restore_default),
            (self.tr("保存"), FluentIcon.SAVE, self._save),
        ):
            button = PrimaryPushButton(icon, text) if text == self.tr("保存") else PushButton(icon, text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self.status_label = BodyLabel(
            self.tr(
                "坐标用 1920×1080 参考空间；最多 {max_blocks} 块。遮挡只作用于落盘截图与失败帧。"
            ).format(max_blocks=MAX_BLOCKS)
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.add_card(self.tr("UID / 昵称 / 聊天遮挡"), card)
        QTimer.singleShot(0, self.reload)

    def _spin(self, minimum: int, maximum: int, prefix: str) -> QSpinBox:
        box = QSpinBox()
        box.setRange(minimum, maximum)
        box.setPrefix(f"{prefix} ")
        return box

    # ── 数据 ─────────────────────────────────────────────
    def reload(self):
        if not self._built:
            return
        self._config = load_config()
        self.block_list.clear()
        for index, block in enumerate(self._config.blocks):
            item = QListWidgetItem(f"{block.label_or_default(index)}  {int(block.width)}×{int(block.height)}")
            self.block_list.addItem(item)
        if self.block_list.count():
            self._selected = min(self._selected, self.block_list.count() - 1)
            self.block_list.setCurrentRow(self._selected)
        else:
            self._selected = -1
        if self.enabled_combo is not None:
            self.enabled_combo.setCurrentIndex(0 if self._config.enabled else 1)
        self._refresh_preview()
        self._set_status(self.tr("已载入 {count} 块遮挡").format(count=self._config.count))

    def _refresh_preview(self):
        if self.preview is not None:
            self.preview.set_config(self._config, self._selected)

    def _current_block(self) -> PrivacyBlock | None:
        if 0 <= self._selected < self._config.count:
            return self._config.blocks[self._selected]
        return None

    def _on_select(self, index: int):
        if index < 0:
            return
        self._selected = index
        block = self._current_block()
        if block is None:
            return
        for box, value in (
            (self.x_box, block.x),
            (self.y_box, block.y),
            (self.width_box, block.width),
            (self.height_box, block.height),
        ):
            if box is not None:
                box.setValue(round(value))
        if self.color_edit is not None:
            self.color_edit.setText(block.color)
        if self.mode_combo is not None:
            self.mode_combo.setCurrentIndex(0 if block.mode == "solid" else 1)
        if self.label_edit is not None:
            self.label_edit.setText(block.label)
        self._refresh_preview()

    def _editor_block(self) -> PrivacyBlock | None:
        if self.x_box is None or self.y_box is None or self.width_box is None or self.height_box is None:
            return None
        mode = "solid" if (self.mode_combo is None or self.mode_combo.currentIndex() == 0) else "mosaic"
        try:
            return PrivacyBlock(
                x=self.x_box.value(),
                y=self.y_box.value(),
                width=self.width_box.value(),
                height=self.height_box.value(),
                color=(self.color_edit.text().strip() if self.color_edit else "#000000") or "#000000",
                mode=mode,
                label=self.label_edit.text().strip() if self.label_edit else "",
            )
        except PrivacyConfigError as exc:
            self._notify(str(exc), error=True)
            return None

    # ── 操作 ─────────────────────────────────────────────
    def _add_block(self):
        if not self._config.can_add():
            self._notify(self.tr("最多 {max_blocks} 块遮挡").format(max_blocks=MAX_BLOCKS), error=True)
            return
        block = self._editor_block() or PrivacyBlock(x=0, y=0, width=100, height=30)
        try:
            self._config = self._config.with_block(block)
        except PrivacyConfigError as exc:
            self._notify(str(exc), error=True)
            return
        self._selected = self._config.count - 1
        self._rebuild_list()
        self._set_status(self.tr("已添加 1 块，记得点保存"))

    def _remove_block(self):
        if self._selected < 0:
            self._notify(self.tr("没有选中遮挡块"), error=True)
            return
        try:
            self._config = self._config.without_block(self._selected)
        except PrivacyConfigError as exc:
            self._notify(str(exc), error=True)
            return
        self._selected = max(0, self._selected - 1)
        self._rebuild_list()
        self._set_status(self.tr("已删除 1 块，记得点保存"))

    def _apply_editor(self):
        block = self._editor_block()
        if block is None:
            return
        if self._selected < 0 or self._selected >= self._config.count:
            self._notify(self.tr("没有选中遮挡块"), error=True)
            return
        self._config = self._config.replace_block(self._selected, block)
        self._rebuild_list()
        self._set_status(self.tr("已应用修改，记得点保存"))

    def _restore_default(self):
        from src.core.privacy import default_config

        self._config = default_config()
        self._selected = 0
        self._rebuild_list()
        self._set_status(self.tr("已恢复默认遮挡，记得点保存"))

    def _rebuild_list(self):
        if self.block_list is None:
            return
        self.block_list.blockSignals(True)
        self.block_list.clear()
        for index, block in enumerate(self._config.blocks):
            self.block_list.addItem(QListWidgetItem(f"{block.label_or_default(index)}  {int(block.width)}×{int(block.height)}"))
        self.block_list.blockSignals(False)
        if self.block_list.count():
            self.block_list.setCurrentRow(min(self._selected, self.block_list.count() - 1))
        self._refresh_preview()

    def _save(self):
        enabled = self.enabled_combo is None or self.enabled_combo.currentIndex() == 0
        self._config = PrivacyConfig(
            enabled=enabled,
            only_foreground=self._config.only_foreground,
            blocks=normalize_blocks(self._config.blocks),
        )
        try:
            path = save_config(self._config)
        except Exception as exc:
            self._notify(self.tr("保存失败：{error}").format(error=exc), error=True)
            return
        self._set_status(self.tr("已保存到 {path}（下次截图生效）").format(path=path))
        self._notify(self.tr("遮挡配置已保存"))

    # ── 延时取点 ─────────────────────────────────────────
    def _start_pick(self):
        if self._picking:
            return
        self._picking = True
        self._set_status(self.tr("1 秒后取当前鼠标位置与颜色，请把鼠标移到目标位置"))
        QTimer.singleShot(PICK_DELAY_MS, self, self._finish_pick)

    def _finish_pick(self):
        self._picking = False
        try:
            position = QGuiApplication.primaryScreen().geometry()
            cursor: QPoint = QCursorPos()
        except Exception as exc:  # pragma: no cover - 依赖真实桌面
            self._notify(str(exc), error=True)
            return

        client_x = cursor.x() - position.x()
        client_y = cursor.y() - position.y()
        ref_x, ref_y = client_to_reference(client_x, client_y, position.width(), position.height())
        color = self._screen_color(client_x, client_y)

        if self.x_box is not None:
            self.x_box.setValue(int(ref_x))
        if self.y_box is not None:
            self.y_box.setValue(int(ref_y))
        if self.color_edit is not None:
            self.color_edit.setText(color)
        self._set_status(
            self.tr("取到客户区 ({x}, {y}) → 参考 ({rx:g}, {ry:g})，颜色 {color}").format(
                x=client_x,
                y=client_y,
                rx=ref_x,
                ry=ref_y,
                color=color,
            )
        )

    def _screen_color(self, x: int, y: int) -> str:
        try:
            pixmap = QGuiApplication.primaryScreen().grabWindow(0, x, y, 1, 1)
            image = pixmap.toImage()
            pixel = image.pixelColor(0, 0)
            return pixel.name().upper()
        except Exception:
            return "#000000"

    # ── 工具 ─────────────────────────────────────────────
    def _set_status(self, text: str):
        if self.status_label is not None:
            self.status_label.setText(text)

    def _notify(self, message: str, error: bool = False):
        try:
            window = self.window()
            factory = InfoBar.error if error else InfoBar.success
            factory(
                title=self.tr("隐私遮挡"),
                content=message,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=5000 if error else 3000,
                parent=window,
            )
        except Exception:
            self.logger.info(message)


def QCursorPos() -> QPoint:
    """当前光标位置（抽出来方便测试替换）。"""
    from PySide6.QtGui import QCursor

    return QCursor.pos()
