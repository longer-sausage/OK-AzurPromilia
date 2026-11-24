"""「分享仓库」页：填一个索引地址，浏览并一键安装别人分享的流程 / 队列 / 方案 / 模板包。

索引长什么样、安装规则（同名不覆盖、可选 SHA-256）见
:mod:`src.core.repo_index` 与 [隐私遮挡与支持工具](../docs/dev/privacy_and_support.md)。

页面只做三件事：加载索引、列出来、装选中项。所有网络与文件逻辑都在 core 里，
这里失败就弹通知，不抛异常。
"""

from __future__ import annotations

from pathlib import Path

from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    TableWidget,
)

from src.core.repo_index import (
    INDEX_KINDS,
    InstallOutcome,
    RepoClient,
    RepoEntry,
    RepoError,
    RepoIndex,
    load_index,
)

PREBUILD_DELAY_MS = 2500
KIND_LABELS = {
    "flow": "流程",
    "queue": "队列",
    "scheme": "轮椅方案",
    "template": "素材包",
}


class RepoTab(CustomTab):
    """在线仓库：加载索引 → 选条目 → 安装。"""

    def __init__(self):
        super().__init__()
        self._built = False
        self._build_scheduled = False
        self._index: RepoIndex | None = None
        self._entries: list[RepoEntry] = []

        self.address_edit: LineEdit | None = None
        self.table: TableWidget | None = None
        self.overwrite_box: CheckBox | None = None
        self.status_label: BodyLabel | None = None

        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._schedule_build)

    # ── CustomTab 元信息 ─────────────────────────────────
    @property
    def name(self):
        return self.tr("分享仓库")

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return True

    @property
    def icon(self):
        return FluentIcon.GLOBE

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_build()

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
        self.add_card(self.tr("在线仓库"), self._build_repo_card())
        self._set_status(self.tr("填入索引地址后点「加载」。索引就是一个返回 JSON 的链接，也可以填本地文件路径"))

    def _build_repo_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("索引地址")))
        self.address_edit = LineEdit()
        self.address_edit.setPlaceholderText(self.tr("https://…/index.json 或本地 index.json 路径"))
        self.address_edit.returnPressed.connect(self.load_index_clicked)
        row.addWidget(self.address_edit, 1)
        load_button = PrimaryPushButton(FluentIcon.SYNC, self.tr("加载"))
        load_button.clicked.connect(self.load_index_clicked)
        row.addWidget(load_button)
        layout.addLayout(row)

        self.table = TableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([self.tr("类型"), self.tr("名称"), self.tr("说明"), self.tr("标签")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(self.table.SelectionMode.SingleSelection)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(220)
        self.table.doubleClicked.connect(lambda _index: self.install_selected())
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        install = PrimaryPushButton(FluentIcon.DOWNLOAD, self.tr("安装选中"))
        install.clicked.connect(self.install_selected)
        buttons.addWidget(install)
        self.overwrite_box = CheckBox(self.tr("允许覆盖同名文件"))
        buttons.addWidget(self.overwrite_box)
        open_flows = PushButton(FluentIcon.FOLDER, self.tr("打开流程目录"))
        open_flows.clicked.connect(lambda: self._open_folder("flows"))
        buttons.addWidget(open_flows)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status_label = BodyLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return card

    # ── 加载 ─────────────────────────────────────────────
    def load_index_clicked(self):
        address = self.address_edit.text().strip() if self.address_edit else ""
        if not address:
            self._notify(self.tr("先填索引地址"), error=True)
            return
        try:
            self._index = load_index(address)
        except RepoError as exc:
            self._index = None
            self._entries = []
            self._fill_table()
            self._set_status(str(exc))
            self._notify(str(exc), error=True)
            return

        self._entries = list(self._index.entries)
        self._fill_table()
        counts = self._index.kinds()
        detail = "、".join(f"{KIND_LABELS.get(kind, kind)} {count}" for kind, count in counts.items() if count)
        name = self._index.name or address
        self._set_status(
            self.tr("已加载「{name}」：共 {count} 项（{detail}）").format(
                name=name, count=len(self._entries), detail=detail or self.tr("没有条目")
            )
        )
        self._notify(self.tr("索引已加载"))

    def _fill_table(self):
        if self.table is None:
            return
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            values = [
                KIND_LABELS.get(entry.kind, entry.kind),
                entry.name,
                entry.description,
                "、".join(entry.tags),
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setToolTip(entry.url if column == 1 else str(text))
                if column == 1:
                    item.setData(Qt.UserRole, entry)
                self.table.setItem(row, column, item)

    def selected_entry(self) -> RepoEntry | None:
        if self.table is None:
            return None
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None

    # ── 安装 ─────────────────────────────────────────────
    def install_selected(self):
        entry = self.selected_entry()
        if entry is None:
            self._notify(self.tr("先在列表里选中一项"), error=True)
            return
        client = RepoClient(allow_overwrite=bool(self.overwrite_box and self.overwrite_box.isChecked()))
        outcome: InstallOutcome = client.install(entry)
        self._set_status(outcome.summary())
        self._notify(outcome.summary(), error=not outcome.ok)

    def install_entry(self, entry: RepoEntry, allow_overwrite: bool = False) -> InstallOutcome:
        """给测试/脚本用的直通入口。"""
        return RepoClient(allow_overwrite=allow_overwrite).install(entry)

    # ── 工具 ─────────────────────────────────────────────
    def _open_folder(self, name: str):
        folder = Path("configs") / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            import os

            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:
            self._notify(self.tr("打不开目录：{error}").format(error=exc), error=True)

    def _set_status(self, text: str):
        if self.status_label is not None:
            self.status_label.setText(text)

    def _notify(self, message: str, error: bool = False):
        try:
            window = self.window()
            factory = InfoBar.error if error else InfoBar.success
            factory(
                title=self.tr("分享仓库"),
                content=message,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=6000 if error else 3000,
                parent=window,
            )
        except Exception:
            self.logger.info(message)


__all__ = ["INDEX_KINDS", "RepoTab"]
