"""战斗角色包。绑定索引使用跨语言的稳定类名。"""

BASE_CHAR_NAME = "BaseChar"


def normalize_binding_name(name: str | None) -> str:
    """兼容旧绑定的空值、未知标识和旧基类名。"""
    if name is None or name in ("unknown", "BaseChara"):
        return BASE_CHAR_NAME
    return name
