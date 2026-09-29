from __future__ import annotations

import threading

from ok import ConfigOption
from ok.util.config import Config
from ok.util.file import get_relative_path, read_json_file, write_json_file
from qfluentwidgets import FluentIcon

from src.icons import Icons
from src.interaction.key_config import DEFAULT_COMMON_KEYS

KEY_CONFIG_NAME = "Game Hotkey Config"
ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME = "Ensure Main Once Action Sleep"


key_config_option = ConfigOption(
    KEY_CONFIG_NAME,
    DEFAULT_COMMON_KEYS,
    description="游戏内快捷键配置",
    icon=Icons.Keyboard
)
ensure_main_once_action_sleep_option = ConfigOption(
    ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME,
    {"SingleActionWithDelay": 1.5},
    description="主界面单次动作后延迟",
    icon=FluentIcon.DATE_TIME
)

GLOBAL_CONFIG_OPTIONS = [
    key_config_option,
    ensure_main_once_action_sleep_option,
]

_LOCK = threading.Lock()
_CONFIGS: dict[str, Config] = {}
_OPTIONS = {option.name: option for option in GLOBAL_CONFIG_OPTIONS}


def get_global_config(name: str) -> Config:
    with _LOCK:
        option = _OPTIONS.get(name)
        if option is None:
            for config in _CONFIGS.values():
                if name in config:
                    return config
            raise RuntimeError(f"Can not find config {name}")

        config = _CONFIGS.get(option.name)
        if config is None:
            config = Config(option.name, option.default_config, validator=option.validator)
            _CONFIGS[option.name] = config
        return config


def get_all_visible_configs():
    configs = []
    for option in GLOBAL_CONFIG_OPTIONS:
        if not option.name.startswith("_"):
            configs.append((option.name, get_global_config(option.name), option))
    return sorted(configs, key=lambda item: item[0])