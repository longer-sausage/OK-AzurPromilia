# 默认游戏通用热键映射表（按需修改成目标游戏的默认键位）
DEFAULT_COMMON_KEYS = {
    'Interact Key': 'f',
    'Backpack Key': 'b',
    'Map Key': 'm',
    'Jump Key': 'space',
}

type_to_key_map = {
    'common': DEFAULT_COMMON_KEYS,
}


class KeyConfigManager:
    """游戏热键配置管理器，负责替换逻辑"""

    def __init__(self, key_config: dict = None):
        """
        初始化热键配置管理器。

        Args:
            key_config: 用户自定义的热键配置字典（通常来自全局配置）
        """
        self.key_config = key_config or {}

    def update_config(self, key_config: dict):
        """更新用户配置。"""
        self.key_config = key_config or {}

    def resolve_key(self, key: str, key_type: str = 'common') -> str:
        config_key_name = None

        default_map = type_to_key_map.get(key_type, {})

        for key_name, key_value in default_map.items():
            if key_value == key:
                config_key_name = key_name
                break

        if config_key_name:
            return self.key_config.get(config_key_name, key)

        return key