"""文件名小工具：导出时补全扩展名。

用户在「另存为」对话框里手输文件名（比如 `养成材料单`）时，Qt 不会自动加扩展名。
写出来的文件没有 `.csv` / `.png`，用户双击打不开，会以为导出失败了。
所以导出函数自己把扩展名补上——**所有调用方都受益**，不用每个按钮各写一遍。
"""

from __future__ import annotations

from pathlib import Path


def ensure_suffix(path: str | Path, suffix: str) -> Path:
    """确保 ``path`` 以 ``suffix`` 结尾（大小写不敏感）；已有别的扩展名也会被替换。

    >>> ensure_suffix("材料单", ".csv")
    PosixPath('材料单.csv')
    >>> ensure_suffix("材料单.CSV", ".csv")
    PosixPath('材料单.CSV')
    >>> ensure_suffix("旧.png", ".csv")
    PosixPath('旧.csv')
    """
    target = Path(path)
    wanted = suffix if suffix.startswith(".") else f".{suffix}"
    current = target.suffix.lower()
    if current == wanted.lower():
        return target
    if current and len(current) <= 6:
        return target.with_suffix(wanted)
    # 没有扩展名（或「扩展名」长得不像扩展名，比如带空格），直接拼在后面
    return target.with_name(f"{target.name}{wanted}")
