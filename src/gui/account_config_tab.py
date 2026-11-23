from __future__ import annotations

import copy
from collections import OrderedDict
from typing import Any, Dict

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    TextEdit,
)

from ok.gui.tasks.ConfigCard import ConfigCard, og
from ok.gui.tasks.LabelAndWidget import LabelAndWidget
from ok.gui.widget.CustomTab import CustomTab

from src.tasks.account.account_scope_store import (
    load_overrides,
    parse_account_list_text,
    sync_account_list_text,
    update_overrides,
)

_EDITOR_CARD_CACHE_MAX = 16


class InMemoryConfig(dict):
    """A lightweight config object used by ConfigCard for account overrides."""

    def __init__(self, initial: Dict[str, Any], defaults: Dict[str, Any]):
        super().__init__(initial)
        self.default = defaults

    def get_default(self, key):
        return self.default.get(key)

    def has_user_config(self):
        return any(not str(key).startswith("_") for key in self.keys())


class AccountConfigTab(CustomTab):
    ALWAYS_HIDDEN_CONFIG_KEYS = {"多账户模式", "多账户独立配置", "账号列表"}

    def __init__(self):
        super().__init__()
        self._loaded_once = False
        self._building = False

        self.overrides_data: Dict[str, Any] = {"accounts": {}}
        self.task_map: Dict[str, Any] = {}
        self.current_virtual_config: InMemoryConfig | None = None
        self.current_task = None
        self.current_account_key = ""
        self.current_account_name = ""
        self.current_editor_card = None
        self.account_display_to_key: Dict[str, str] = {}
        self.account_display_to_name: Dict[str, str] = {}
        self._editor_cards: OrderedDict[str, ConfigCard] = OrderedDict()

        self._build_ui()

    @property
    def name(self):
        # MainWindow 会对 tab 的 name 统一调用 self.app.tr(name)，
        # 这里必须返回源 key（"账号配置"）而非已翻译文本。
        return "账号配置"

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return False

    @property
    def icon(self):
        return FluentIcon.PEOPLE

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once and self.executor is not None:
            self._loaded_once = True
            self.refresh_from_source()
        elif self.executor is not None:
            self.sync_from_source()

    # ---------- UI ----------
    def _build_ui(self):
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        tip = BodyLabel(og.app.tr(
            "按账号和任务配置独立参数。先选账号，再选任务，下面会自动出现该任务的属性控件。"
            "账号页只需要填写账号名，无需填写密码。系统兼容旧格式 `账号,密码` 但不会保存密码。"
        ))
        tip.setWordWrap(True)
        header_layout.addWidget(tip)
        self.add_card(og.app.tr("账号配置中心"), header)

        base_widget = QWidget()
        base_layout = QVBoxLayout(base_widget)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(8)

        account_list_row = LabelAndWidget("账号列表", "每行一个账号名，无需密码")
        self.account_list_edit = TextEdit()
        self.account_list_edit.setFixedWidth(420)
        self.account_list_edit.setMinimumHeight(120)
        self.account_list_edit.setPlaceholderText(og.app.tr("账号A\n账号B"))
        account_list_row.add_widget(self.account_list_edit, stretch=0)
        base_layout.addWidget(account_list_row)

        base_action_row = LabelAndWidget("账号列表操作")
        base_action_layout = QHBoxLayout()
        base_action_layout.addStretch(1)
        self.save_base_button = PrimaryPushButton(og.app.tr("保存账号列表"))
        base_action_layout.addWidget(self.save_base_button)
        base_action_row.add_layout(base_action_layout, stretch=1)
        base_layout.addWidget(base_action_row)

        self.add_card(og.app.tr("账号基础设置"), base_widget)

        selector_widget = QWidget()
        selector_layout = QVBoxLayout(selector_widget)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(8)

        account_selector_row = LabelAndWidget("账号", "从账号列表或已有覆盖中选择")
        self.account_selector = ComboBox()
        self.account_selector.setMinimumWidth(220)
        account_selector_row.add_widget(self.account_selector, stretch=0)
        selector_layout.addWidget(account_selector_row)

        task_selector_row = LabelAndWidget("任务", "选择任务后自动渲染属性控件")
        self.task_selector = ComboBox()
        self.task_selector.setMinimumWidth(280)
        task_selector_row.add_widget(self.task_selector, stretch=0)
        selector_layout.addWidget(task_selector_row)

        action_row = LabelAndWidget("账号任务覆盖操作")
        action_layout = QHBoxLayout()
        action_layout.addStretch(1)
        self.save_current_config_button = PrimaryPushButton(og.app.tr("保存当前账号配置"))
        self.clear_task_override_button = PushButton(og.app.tr("清空当前任务覆盖"))
        self.clear_account_override_button = PushButton(og.app.tr("清空当前账号全部覆盖"))
        action_layout.addWidget(self.save_current_config_button)
        action_layout.addWidget(self.clear_task_override_button)
        action_layout.addWidget(self.clear_account_override_button)
        action_row.add_layout(action_layout, stretch=1)
        selector_layout.addWidget(action_row)

        self.add_card(og.app.tr("账号任务选择"), selector_widget)

        status_widget = QWidget()
        status_layout = QVBoxLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)
        self.status_label = BodyLabel(og.app.tr("就绪"))
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        self.add_card(og.app.tr("状态"), status_widget)

        editor_widget = QWidget()
        self.editor_layout = QVBoxLayout(editor_widget)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(8)
        self.editor_empty_label = BodyLabel(og.app.tr("请先选择账号与任务"))
        self.editor_layout.addWidget(self.editor_empty_label)
        self.add_card(og.app.tr("任务属性配置"), editor_widget)

        self.save_base_button.clicked.connect(self.save_base_settings)
        self.account_selector.currentTextChanged.connect(self.on_account_changed)
        self.task_selector.currentTextChanged.connect(self.on_task_changed)
        self.save_current_config_button.clicked.connect(self.save_current_account_config)
        self.clear_task_override_button.clicked.connect(self.clear_current_task_override)
        self.clear_account_override_button.clicked.connect(self.clear_current_account_overrides)

    def _set_status(self, text: str):
        self.status_label.setText(text)

    # ---------- 数据 ----------
    def sync_from_source(self):
        self._building = True
        try:
            self.overrides_data = load_overrides()
            account_list = str(self.overrides_data.get("account_list_text", "") or "")
            self.account_list_edit.setPlainText(account_list)
            self.rebuild_account_selector()
            self.rebuild_task_selector()
            self.render_task_editor()
        finally:
            self._building = False

    def refresh_from_source(self):
        self.sync_from_source()

    def _collect_tasks(self):
        if self.executor is None:
            return []
        tasks = []
        seen = set()
        for task in list(getattr(self.executor, "onetime_tasks", [])) + list(getattr(self.executor, "trigger_tasks", [])):
            if not getattr(task, "support_multi_account", False):
                # 声明了子任务参数的任务也纳入：家园每日等子任务的参数
                # 平时只存在子任务自己的配置文件里，按账号覆盖时在账号配置页编辑
                if not getattr(task, "sub_task_default_config", None):
                    continue
            class_name = task.__class__.__name__
            if class_name in seen:
                continue
            seen.add(class_name)
            tasks.append(task)
        return tasks

    def _current_account_key(self) -> str:
        display = self.account_selector.currentText().strip()
        return self.account_display_to_key.get(display, "")

    def _current_account_name(self) -> str:
        display = self.account_selector.currentText().strip()
        return self.account_display_to_name.get(display, "")

    def _current_task(self):
        display = self.task_selector.currentText().strip()
        return self.task_map.get(display)

    # ---------- 选择器 ----------
    def rebuild_account_selector(self, keep_selection: bool = True):
        current_key = self._current_account_key() if keep_selection else ""

        raw_items: list[tuple[str, str]] = []
        registry = self.overrides_data.get("account_registry") or {}
        for entry in parse_account_list_text(self.account_list_edit.toPlainText()):
            username = entry["username"]
            key = next(
                (k for k, meta in registry.items()
                 if isinstance(meta, dict) and meta.get("username") == username),
                username,
            )
            raw_items.append((key, username))
        for key in (self.overrides_data.get("accounts") or {}).keys():
            meta = registry.get(key)
            name = str(meta.get("username", "") or key) if isinstance(meta, dict) else key
            raw_items.append((str(key), name))

        display_to_key, display_to_name, displays = {}, {}, []
        for key, name in raw_items:
            if key in display_to_key.values():
                continue
            display = name or key
            if display in display_to_key:
                display = f"{display} ({key[-6:]})"
            displays.append(display)
            display_to_key[display] = key
            display_to_name[display] = name or key

        self.account_selector.blockSignals(True)
        try:
            current_displays = [self.account_selector.itemText(i) for i in range(self.account_selector.count())]
            if current_displays != displays:
                self.account_selector.clear()
                for display in displays:
                    self.account_selector.addItem(display)
            self.account_display_to_key = display_to_key
            self.account_display_to_name = display_to_name
            selected = next((d for d, k in display_to_key.items() if k == current_key), displays[0] if displays else "")
            if selected and self.account_selector.currentText() != selected:
                self.account_selector.setCurrentText(selected)
        finally:
            self.account_selector.blockSignals(False)

    def rebuild_task_selector(self, keep_selection: bool = True):
        task_map = {}
        displays = []
        for task in self._collect_tasks():
            display = f"{og.app.tr(task.name)} ({task.__class__.__name__})"
            task_map[display] = task
            displays.append(display)

        self.task_selector.blockSignals(True)
        try:
            current_displays = [self.task_selector.itemText(i) for i in range(self.task_selector.count())]
            if current_displays != displays:
                self.task_selector.clear()
                for display in displays:
                    self.task_selector.addItem(display)
            self.task_map = task_map
        finally:
            self.task_selector.blockSignals(False)

    # ---------- 渲染 ----------
    def on_account_changed(self, _):
        if self._building:
            return
        self._save_pending_changes()
        self.render_task_editor()

    def on_task_changed(self, _):
        if self._building:
            return
        self._save_pending_changes()
        self.render_task_editor()

    def _config_key_set(self, task, attribute: str) -> set[str]:
        value = getattr(task, attribute, None)
        if isinstance(value, str):
            return {value}
        if isinstance(value, (list, tuple, set)):
            return {str(key) for key in value}
        return set()

    @staticmethod
    def _coerce_like(base_value, override_value):
        """Coerce override_value to match the type of base_value."""
        if base_value is None or override_value is None:
            return override_value
        if isinstance(base_value, bool):
            if isinstance(override_value, bool):
                return override_value
            if isinstance(override_value, str):
                v = override_value.strip().lower()
                if v in {"true", "1", "yes", "on"}:
                    return True
                if v in {"false", "0", "no", "off"}:
                    return False
            return base_value
        if isinstance(base_value, int) and not isinstance(base_value, bool):
            if isinstance(override_value, int):
                return override_value
            if isinstance(override_value, str):
                try:
                    return int(override_value.strip())
                except ValueError:
                    return base_value
            return base_value
        if isinstance(base_value, float):
            if isinstance(override_value, (int, float)):
                return float(override_value)
            if isinstance(override_value, str):
                try:
                    return float(override_value.strip())
                except ValueError:
                    return base_value
            return base_value
        if isinstance(base_value, list):
            if isinstance(override_value, list):
                return override_value
            return base_value
        if isinstance(base_value, str):
            return str(override_value)
        return override_value

    def _editor_card_key(self, account_key: str, task_class: str) -> str:
        return f"{account_key}::{task_class}"

    def _evict_editor_cache(self):
        while len(self._editor_cards) > _EDITOR_CARD_CACHE_MAX:
            self._editor_cards.popitem(last=False)

    def _build_virtual_config(self, task, account_key: str, account_name: str):
        accounts = self.overrides_data.get("accounts") or {}
        account_map = accounts.get(account_key, {})
        task_class = task.__class__.__name__
        task_override = account_map.get(task_class, {}) if isinstance(account_map, dict) else {}

        blacklist = self.ALWAYS_HIDDEN_CONFIG_KEYS | self._config_key_set(task, "account_config_blacklist")
        whitelist = self._config_key_set(task, "account_config_whitelist")

        defaults = {}
        initial = {}
        editable_keys = []
        for key, default_value in (task.default_config or {}).items():
            if key in blacklist or str(key).startswith("_"):
                continue
            if key in whitelist or isinstance(default_value, (bool, int, float, str, list)):
                value = task_override.get(key, default_value)
                defaults[key] = default_value
                initial[key] = value
                editable_keys.append(key)

        return InMemoryConfig(initial, defaults), editable_keys

    def render_task_editor(self):
        if self.current_editor_card is not None:
            self.current_editor_card.hide()
            self.current_editor_card = None
        self.current_virtual_config = None
        self.current_task = None

        account_key = self._current_account_key()
        account_name = self._current_account_name()
        task = self._current_task()
        if not account_key or task is None:
            self.editor_empty_label.setText(og.app.tr("请先选择账号与任务"))
            self.editor_empty_label.show()
            return

        task_class = task.__class__.__name__
        cache_key = self._editor_card_key(account_key, task_class)

        if cache_key in self._editor_cards:
            card = self._editor_cards[cache_key]
            self._editor_cards.move_to_end(cache_key)
            card.show()
            self.editor_layout.addWidget(card)
            self.current_virtual_config = card.config
            self.current_task = task
            self.current_account_key = account_key
            self.current_account_name = account_name
            self.current_editor_card = card
            self.editor_empty_label.hide()
            return

        virtual_config, editable_keys = self._build_virtual_config(task, account_key, account_name)
        if not editable_keys:
            self.editor_empty_label.setText(og.app.tr("该任务暂无可编辑配置项"))
            self.editor_empty_label.show()
            return
        self.editor_empty_label.hide()

        config_description = dict(task.config_description or {})
        config_type = dict(task.config_type or {})
        config_type = {key: value for key, value in config_type.items() if key in editable_keys}

        card = ConfigCard(
            None,
            task.name,
            virtual_config,
            "保存当前账号的完整任务配置快照。任务默认值变化不会影响已保存账号。",
            {},
            config_description,
            config_type,
            task.icon,
        )
        card.card.setTitle(f"{og.app.tr(task.name)} - {account_name or account_key}")
        card.show()
        self.editor_layout.addWidget(card)

        self._editor_cards[cache_key] = card
        self._evict_editor_cache()

        self.current_virtual_config = card.config
        self.current_task = task
        self.current_account_key = account_key
        self.current_account_name = account_name
        self.current_editor_card = card

    # ---------- 保存 / 清空 ----------
    def _apply_current_task_override(self) -> bool:
        if self.current_virtual_config is None or self.current_task is None or not self.current_account_key:
            return False

        accounts = self.overrides_data.setdefault("accounts", {})
        account_map = accounts.setdefault(self.current_account_key, {})
        task_class = self.current_task.__class__.__name__
        existing_config = account_map.get(task_class, {})
        full_config = {key: copy.deepcopy(value) for key, value in self.current_virtual_config.items()}
        changed = full_config != existing_config
        if full_config:
            account_map[task_class] = full_config
        else:
            account_map.pop(task_class, None)
        if not account_map:
            accounts.pop(self.current_account_key, None)
        return changed

    def _save_pending_changes(self, show_status: bool = False) -> bool:
        if self.current_virtual_config is None or self.current_task is None:
            return False
        changed = False

        def merge(latest):
            nonlocal changed
            self.overrides_data = latest
            changed = self._apply_current_task_override()
            return self.overrides_data

        self.overrides_data = update_overrides(merge)
        if show_status:
            account = self.current_account_name or self.current_account_key
            message = og.app.tr("已保存当前账号配置" if changed else "当前账号配置没有变化")
            print(f"{message}：{account}")
        return changed

    def save_base_settings(self):
        account_list = self.account_list_edit.toPlainText().strip()
        summary = sync_account_list_text(account_list)
        self.overrides_data = load_overrides()
        self.rebuild_account_selector()
        self.render_task_editor()
        self._set_status(og.app.tr("账号列表已保存（新建 {created}，复用 {reused}）").format(
            created=summary.get('created_count', 0),
            reused=summary.get('reused_count', 0),
        ))

    def save_current_account_config(self):
        if not self.current_account_key:
            self._set_status(og.app.tr("请先选择账号"))
            return
        self._save_pending_changes(show_status=True)

    def clear_current_task_override(self):
        account_key = self._current_account_key()
        task = self._current_task()
        if not account_key or task is None:
            self._set_status(og.app.tr("请先选择账号与任务"))
            return
        task_class = task.__class__.__name__

        def clear_task(latest):
            self.overrides_data = latest
            accounts = self.overrides_data.get("accounts", {})
            account_map = accounts.get(account_key, {})
            account_map.pop(task_class, None)
            if not account_map:
                accounts.pop(account_key, None)
            return self.overrides_data

        self.overrides_data = update_overrides(clear_task)
        self._editor_cards.pop(self._editor_card_key(account_key, task_class), None)
        self.render_task_editor()
        self._set_status(og.app.tr("已清空：{account} / {task} 覆盖").format(
            account=self.current_account_name or account_key, task=task.name
        ))

    def clear_current_account_overrides(self):
        account_key = self._current_account_key()
        if not account_key:
            self._set_status(og.app.tr("请先选择账号"))
            return

        def clear_account(latest):
            self.overrides_data = latest
            accounts = self.overrides_data.get("accounts", {})
            accounts.pop(account_key, None)
            return self.overrides_data

        self.overrides_data = update_overrides(clear_account)
        keys_to_remove = [k for k in self._editor_cards if k.startswith(f"{account_key}::")]
        for k in keys_to_remove:
            self._editor_cards.pop(k, None)
        self.render_task_editor()
        self._set_status(og.app.tr("已清空账号全部覆盖：{account}").format(
            account=self.current_account_name or account_key
        ))