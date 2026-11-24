from __future__ import annotations

import threading

from ok import ConfigOption
from ok.util.config import Config
from qfluentwidgets import FluentIcon

from src.core.runtime_config import RUNTIME_CONFIG_NAME, load_runtime_settings, runtime_config_option
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
    runtime_config_option,
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


def get_runtime_settings():
    """读取当前「超时/重试/自恢复」策略快照。

    每次调用都重新读一次 ``Config``（底层是磁盘 JSON 的 dict 视图，读取成本可忽略），
    这样用户在全局配置页改完参数后，下一次任务就会生效，不需要重启。
    """
    return load_runtime_settings(get_global_config(RUNTIME_CONFIG_NAME))
