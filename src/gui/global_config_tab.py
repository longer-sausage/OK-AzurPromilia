from PySide6.QtCore import QTimer
from qfluentwidgets import FluentIcon, NavigationItemPosition

from ok.gui.tasks.ConfigCard import ConfigCard
from ok.gui.widget.CustomTab import CustomTab

from src.core.global_config_store import get_all_visible_configs

GLOBAL_CONFIG_GROUPS = {
    "基础配置": ["Ensure Main Once Action Sleep"],
    "键位配置": ["Game Hotkey Config"],
}

PREBUILD_DELAY_MS = 3000


class GlobalConfigTab(CustomTab):
    def __init__(self):
        super().__init__()
        self._pending_cards = []
        self._build_scheduled = False
        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._prewarm_build)

    @property
    def name(self):
        return "全局配置"

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return False

    @property
    def icon(self):
        return FluentIcon.SETTING

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_build()

    def _prewarm_build(self):
        self._schedule_build()

    def _schedule_build(self):
        if self._build_scheduled:
            return
        self._build_scheduled = True
        self._pending_cards = self._collect_cards()
        QTimer.singleShot(0, self, self._build_next_card)

    def _collect_cards(self):
        visible_configs = {name: (config, option) for name, config, option in get_all_visible_configs()}
        shown = set()
        cards = []
        for group_name, config_names in GLOBAL_CONFIG_GROUPS.items():
            for config_name in config_names:
                config_and_option = visible_configs.get(config_name)
                if config_and_option is None:
                    continue
                config, option = config_and_option
                shown.add(config_name)
                cards.append((group_name, config, option))

        for config_name, (config, option) in visible_configs.items():
            if config_name in shown:
                continue
            cards.append(("其他配置", config, option))
        return cards

    def _build_next_card(self):
        if not self._pending_cards:
            return
        group_name, config, option = self._pending_cards.pop(0)
        card = ConfigCard(
            None,
            group_name,
            config,
            option.description,
            option.default_config,
            option.config_description,
            option.config_type,
            option.icon,
        )
        self.add_widget(card)
        if self._pending_cards:
            QTimer.singleShot(0, self, self._build_next_card)
