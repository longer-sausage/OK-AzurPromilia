"""崩溃诊断补丁：在启动最早期把异常钩子挂上。

之前 :mod:`src.core.crash_guard` 只有实现和测试、**从来没被安装过** —— 真崩了不会留下任何
记录。补丁把它接到 ``install_startup_patches()`` 最前面：

- 三个钩子（``sys.excepthook`` / ``threading.excepthook`` / ``faulthandler``）早挂早生效，
  连其它补丁初始化时的异常也能记下来；
- 写 ``crashes/crash_<时间>.txt`` + 自动附诊断包，并弹一次通知（拿不到通知通道就只写日志）；
- 环境变量 ``OK_AP_DISABLE_CRASH_GUARD=1`` 可关掉（跑测试或排查钩子冲突时用）；
- 安装失败只记日志，不影响启动。
"""

from __future__ import annotations

import os
from pathlib import Path

DISABLE_ENV = "OK_AP_DISABLE_CRASH_GUARD"
_guard = None


def _notify(message: str) -> None:
    try:
        from ok import communicate

        communicate.notification.emit(message, "崩溃诊断", True, True, False, None, None)
    except Exception:
        pass


def installed_guard():
    """已经装上的 guard（没装就是 None），调试与测试用。"""
    return _guard


def install_crash_guard_patch(app_root: str | Path | None = None) -> bool:
    """幂等安装；``OK_AP_DISABLE_CRASH_GUARD=1`` 时直接跳过。"""
    global _guard
    if os.environ.get(DISABLE_ENV):
        return False
    if _guard is not None and getattr(_guard, "installed", False):
        return True
    try:
        from src.core.crash_guard import CrashGuard

        guard = CrashGuard(app_root=Path(app_root) if app_root is not None else Path("."), notify=_notify)
        if not guard.install():
            return False
        _guard = guard
        return True
    except Exception:
        return False
