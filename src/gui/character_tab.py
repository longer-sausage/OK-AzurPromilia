from dataclasses import replace
from functools import partial
from threading import Thread

import numpy as np
from ok import Icon, og
from ok.ui.qt.kit.buttons import Button
from ok.ui.qt.kit.cards import Card
from ok.ui.qt.kit.inputs import ComboBox
from ok.ui.qt.kit.labels import Label
from ok.ui.qt.widget.CustomTab import CustomTab
from PySide6.QtCore import QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.char import BASE_CHAR_NAME
from src.combat.team_manager import TeamManager, TeamSlot, team_manager
from src.core.game_window import ManualCaptureError, capture_manual_frame
from src.image.team_portraits import SLOT_COUNT


def _pixmap(image: np.ndarray) -> QPixmap:
    h, w = image.shape[:2]
    return QPixmap.fromImage(QImage(image.data, w, h, image.strides[0], QImage.Format_BGR888).copy())


class SlotCard(Card):
    def __init__(self, page: "CharacterTab", index: int):
        super().__init__(page)
        self.page = page
        self.slot = TeamSlot()
        self.editing = False
        self.placeholder = page.tr("槽位 {number}").format(number=index + 1)
        layout = QVBoxLayout(self)
        self.preview = Label(self.placeholder, "secondary")
        self.preview.setFixedSize(86, 86)
        self.preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.preview, alignment=Qt.AlignHCenter)

        name_field = QWidget()
        name_row = QHBoxLayout(name_field)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.addWidget(Label(page.tr("角色"), "secondary"))
        self.name_label = Label()
        self.selector = ComboBox()
        self.selector.setMinimumContentsLength(2)
        self.selector.setSizeAdjustPolicy(ComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        name_field.setMinimumHeight(self.selector.sizeHint().height())
        self.selector.hide()
        name_row.addWidget(self.name_label, 1)
        name_row.addWidget(self.selector, 1)
        layout.addWidget(name_field)
        self.confidence_label = Label("", "secondary")
        layout.addWidget(self.confidence_label)
        self.edit_button = Button(page.tr("编辑"))
        self.edit_button.clicked.connect(self.edit_or_save)
        layout.addWidget(self.edit_button, alignment=Qt.AlignHCenter)
        self.show_slot(self.slot)

    def show_slot(self, slot: TeamSlot):
        self.slot = slot
        if slot.portrait is None:
            self.preview.setText(self.placeholder)
        else:
            self.preview.setPixmap(_pixmap(slot.portrait).scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.name_label.setText(self.page.display_name(slot.char_name))
        self.confidence_label.setText(self.page.tr("置信度 {score}").format(score=f"{slot.confidence:.0%}"))
        self.edit_button.setEnabled(slot.template_id is not None)

    def set_editing(self, editing: bool):
        self.editing = editing
        self.name_label.setVisible(not editing)
        self.selector.setVisible(editing)
        self.edit_button.setText(self.page.tr("保存") if editing else self.page.tr("编辑"))

    def edit_or_save(self):
        if self.editing:
            if self.page.save_binding(self.slot.template_id, self.selector.currentData()):
                self.set_editing(False)
            return
        self.selector.clear()
        self.selector.addItem(self.page.display_name(BASE_CHAR_NAME), BASE_CHAR_NAME)
        for spec in self.page.manager.factory.characters:
            self.selector.addItem(self.page.tr(spec.display_msgid), spec.canonical_name)
        self.selector.setCurrentIndex(max(0, self.selector.findData(self.slot.char_name)))
        self.set_editing(True)
        self.selector.setFocus()


class _CaptureEvents(QObject):
    finished = Signal(object, str, str)


class CharacterTab(CustomTab):
    name = "角色管理"
    icon = Icon.PEOPLE
    add_after_default_tabs = False

    def __init__(self, manager: TeamManager = team_manager):
        super().__init__()
        self.manager = manager
        # 切换语言会销毁旧页面。让取帧线程持有独立的信号源，由 Qt 断开已销毁的接收方。
        self.capture_events = _CaptureEvents()
        self.cards: list[SlotCard] = []

        header = QHBoxLayout()
        header.addWidget(Label(self.tr("当前队伍"), "subtitle"))
        self.capture_button = Button(self.tr("截图"), variant="primary")
        self.capture_button.clicked.connect(self.capture)
        header.addWidget(self.capture_button)
        header.addStretch()
        self.addLayout(header)
        squad = QHBoxLayout()
        for index in range(SLOT_COUNT):
            card = SlotCard(self, index)
            squad.addWidget(card, 1)
            self.cards.append(card)
        self.addLayout(squad)
        self.message = Label("", "secondary", word_wrap=True)
        self.message.hide()
        self.add_widget(self.message)

        self.add_widget(Label(self.tr("全部模板"), "subtitle"))
        self.table = QTableWidget(0, 4)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setHorizontalHeaderLabels([self.tr("头像"), self.tr("角色名"), self.tr("模板名"), ""])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.setIconSize(QSize(46, 46))
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        columns = self.table.horizontalHeader()
        columns.setStretchLastSection(False)
        columns.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for column in (0, 1):
            columns.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        columns.setSectionResizeMode(2, QHeaderView.Stretch)
        columns.setSectionResizeMode(3, QHeaderView.Fixed)
        self.add_widget(self.table, 1)
        self.capture_events.finished.connect(self._on_capture_finished)

    def display_name(self, name: str | None) -> str:
        return self.tr(self.manager.factory.display_msgid(name))

    def showEvent(self, event):
        super().showEvent(event)
        if not any(card.editing for card in self.cards):
            for card, slot in zip(self.cards, self.manager.current_team, strict=True):
                card.show_slot(slot)
        self.refresh_library()

    def _status(self, text: str):
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _error(self, error: Exception):
        self.logger.error("Character template library", error)
        self._status(self.tr("读写角色模板失败。{error}").format(error=error))

    def refresh_library(self):
        try:
            templates = self.manager.store.templates()
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self.table.setRowCount(0)
        self.table.setRowCount(len(templates))
        for row, template in enumerate(templates):
            preview = QTableWidgetItem()
            if template.image is not None:
                preview.setIcon(QIcon(_pixmap(template.image)))
            self.table.setItem(row, 0, preview)
            self.table.setItem(row, 1, QTableWidgetItem(self.display_name(template.char_name)))
            filename = f"{template.uuid}.png"
            item = QTableWidgetItem(filename)
            item.setToolTip(filename)
            self.table.setItem(row, 2, item)
            button = Button(self.tr("删除"))
            button.clicked.connect(partial(self.delete_template, template.uuid))
            self.table.setCellWidget(row, 3, button)
            if row == 0:
                # Qt 将单元格左右内边距从控件可用宽度中扣除。
                self.table.setColumnWidth(3, button.sizeHint().width() + 30)

        by_id = {template.uuid: template for template in templates}
        for card in self.cards:
            slot = card.slot
            if slot.template_id is None:
                continue
            template = by_id.get(slot.template_id)
            if template is None:
                card.set_editing(False)
                card.show_slot(TeamSlot(slot.portrait))
            else:
                name = self.manager.factory.resolve_name(template.char_name)
                if name != slot.char_name:
                    card.show_slot(replace(slot, char_name=name))

    @Slot()
    def capture(self):
        if not self.capture_button.isEnabled():
            return
        device = getattr(self.executor, "device_manager", None) or og.device_manager
        if getattr(device, "capture_method", None) is None:
            self._status(self.tr("请先连接游戏窗口，再截图。"))
            return
        self.capture_button.setEnabled(False)
        self.table.setEnabled(False)
        for card in self.cards:
            card.setEnabled(False)
        self._status(self.tr("正在截取队伍头像…"))
        Thread(target=self._capture_frame, args=(device,), name="CharacterCapture", daemon=True).start()

    def _capture_frame(self, device):
        try:
            frame = capture_manual_frame(device)
            slots = self.manager.observe(frame, capture=True)
            if not any(slot.template_id for slot in slots):
                self.capture_events.finished.emit(slots, "请在游戏中显示队伍头像后再截图。", "")
                return
            self.capture_events.finished.emit(slots, "", "")
        except ManualCaptureError as exc:
            self.capture_events.finished.emit(None, str(exc), "")
        except Exception as exc:
            self.logger.error("Character capture failed", exc)
            self.capture_events.finished.emit(None, "截图失败。{error}", str(exc))

    @Slot(object, str, str)
    def _on_capture_finished(self, slots, message, error):
        self.capture_button.setEnabled(True)
        self.table.setEnabled(True)
        for card in self.cards:
            card.setEnabled(True)
        self._status(self.tr(message).format(error=error) if message else "")
        if slots is not None:
            for card, slot in zip(self.cards, slots, strict=True):
                if card.slot.template_id != slot.template_id:
                    card.set_editing(False)
                card.show_slot(slot)
            self.refresh_library()

    def save_binding(self, key: str, name: str | None) -> bool:
        try:
            self.manager.bind(key, name)
        except KeyError as exc:
            self.refresh_library()
            self._error(exc)
            return False
        except (OSError, ValueError) as exc:
            self._error(exc)
            return False
        self._status("")
        self.refresh_library()
        return True

    def delete_template(self, key: str):
        try:
            self.manager.delete(key)
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self._status("")
        self.refresh_library()
