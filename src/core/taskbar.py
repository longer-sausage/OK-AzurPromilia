"""任务栏进度：按历史平均用时估算，最多到 97%，结束才消失。

Windows 的进度条要 ``ITaskbarList3``（pywin32 / ctypes 都行）。这里把它拆成两层：

- :class:`TaskbarProgress` —— **纯逻辑**：算比例、节流（变化小于 1% 不推）、清除；
- :class:`WindowsTaskbar` —— 真正调 ``ITaskbarList3`` 的后端，拿不到句柄或没有
  pywin32 时**安静降级**成 ``NullTaskbar``，绝不因为进度条把主流程搞挂。

比例一律来自 :meth:`src.core.ledger.RunLog.progress_ratio`（封顶 0.97），
任务结束时调用 :meth:`TaskbarProgress.clear` 把进度条撤掉。
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from src.core.ledger import RunLog

TASKBAR_STATE_NORMAL = "normal"
TASKBAR_STATE_ERROR = "error"
TASKBAR_STATE_PAUSED = "paused"
MIN_DELTA = 0.01


class TaskbarBackend(Protocol):
    """进度条后端。``value=None`` 表示撤掉进度条。"""

    def set_progress(self, value: float | None) -> bool: ...

    def set_state(self, state: str) -> bool: ...


@dataclass
class NullTaskbar:
    """什么都不做的后端（非 Windows / 没有 pywin32 时用）。"""

    calls: list[object] = field(default_factory=list)

    def set_progress(self, value: float | None) -> bool:
        self.calls.append(value)
        return True

    def set_state(self, state: str) -> bool:
        self.calls.append(state)
        return True


class WindowsTaskbar:
    """用 ctypes 调 ``ITaskbarList3``（不依赖 pywin32）。

    只用到两个方法：``SetProgressValue``（vtable 第 9 项）与
    ``SetProgressState``（第 10 项）。任何一步失败就把 ``available`` 置 False，
    之后不再尝试 —— 进度条不值得让主流程承担风险。
    """

    def __init__(self, hwnd: int = 0):
        self.hwnd = int(hwnd or 0)
        self.available = False
        self._taskbar = None
        self._ctypes = None
        if self.hwnd:
            self.available = self._init_com()

    def _init_com(self) -> bool:
        try:
            import ctypes

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            clsid = GUID(
                0x56FDF344,
                0xFD6D,
                0x11D0,
                (ctypes.c_ubyte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90),
            )
            iid = GUID(
                0xEA1AFB91,
                0x9E28,
                0x4B86,
                (ctypes.c_ubyte * 8)(0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF),
            )
            ole32 = ctypes.windll.ole32
            # COM 是**按线程**初始化的：在哪个线程 CoCreateInstance，就得在那个线程调方法。
            # 套间模型用默认的 STA（COINIT_APARTMENTTHREADED=2），和 Explorer 的
            # ITaskbarList3 一致；重复 CoInitialize 返回 S_FALSE(1) 也无所谓。
            self._com_initialized = False
            with contextlib.suppress(Exception):
                hr = ole32.CoInitialize(None)
                self._com_initialized = hr in (0, 1)
            taskbar = ctypes.c_void_p()
            if ole32.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(taskbar)
            ) != 0:
                self._release_com()
                return False
            self._taskbar = taskbar
            self._ctypes = ctypes
            return True
        except Exception:
            self._taskbar = None
            return False

    def _release_com(self) -> None:
        """只释放**本线程**初始化过的 COM（别把别人的计数减掉）。"""
        if not getattr(self, "_com_initialized", False):
            return
        self._com_initialized = False
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.ole32.CoUninitialize()

    def close(self) -> bool:
        """在**创建它的那个线程**里调用：撤掉进度条并释放 COM。"""
        with contextlib.suppress(Exception):
            self.set_progress(None)
        self.available = False
        self._taskbar = None
        self._release_com()
        return True

    def _method(self, index: int, arg_types: tuple):
        ctypes = self._ctypes
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arg_types)
        vtable = ctypes.cast(
            self._taskbar, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        return prototype(vtable[index])

    def set_progress(self, value: float | None) -> bool:
        if not (self.available and self._taskbar is not None):
            return False
        try:
            ctypes = self._ctypes
            if value is None:
                return self.set_state("none")
            fn = self._method(9, (ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_ulonglong))
            raw = int(max(0.0, min(1.0, float(value))) * 1000)
            return fn(self._taskbar, self.hwnd, raw, 1000) == 0
        except Exception:
            self.available = False
            return False

    def set_state(self, state: str) -> bool:
        if not (self.available and self._taskbar is not None):
            return False
        flags = {
            "none": 0x0,
            TASKBAR_STATE_NORMAL: 0x2,
            TASKBAR_STATE_ERROR: 0x4,
            TASKBAR_STATE_PAUSED: 0x8,
            "indeterminate": 0x1,
        }
        try:
            ctypes = self._ctypes
            fn = self._method(10, (ctypes.c_void_p, ctypes.c_uint))
            return fn(self._taskbar, self.hwnd, flags.get(state, 0x2)) == 0
        except Exception:
            self.available = False
            return False


def create_backend(hwnd: int | None = None) -> TaskbarBackend:
    """拿得到窗口句柄就用真的，否则安静降级。

    ⚠️ 必须在**要使用它的那个线程**里调用：COM 对象不能跨线程用。
    """
    if not hwnd:
        return NullTaskbar()
    backend = WindowsTaskbar(hwnd)
    return backend if backend.available else NullTaskbar()


def uninitialize_backend(backend: TaskbarBackend) -> None:
    """在线程退出前收尾：真后端释放 COM，空实现什么都不做。"""
    close = getattr(backend, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()


@dataclass
class TaskbarProgress:
    """节流后的进度推送。"""

    backend: TaskbarBackend = field(default_factory=NullTaskbar)
    min_delta: float = MIN_DELTA
    _last: float | None = None

    def update(self, ratio: float | None) -> bool:
        """推一次进度；``None`` 等价于 :meth:`clear`。"""
        if ratio is None:
            return self.clear()
        value = max(0.0, min(0.97, float(ratio)))
        if self._last is not None and abs(value - self._last) < self.min_delta:
            return False
        self._last = value
        return bool(self.backend.set_progress(value))

    def clear(self) -> bool:
        self._last = None
        return bool(self.backend.set_progress(None))

    def fail(self) -> bool:
        return bool(self.backend.set_state(TASKBAR_STATE_ERROR))

    @property
    def last(self) -> float | None:
        return self._last


def compute_ratio(
    run_log: RunLog,
    task: str | None,
    elapsed_seconds: float,
    fallback_seconds: float | None = None,
) -> float | None:
    """没有历史数据时返回 ``None``（不显示进度条）。"""
    estimate = run_log.estimate_seconds(task, fallback_seconds or 0.0)
    if estimate <= 0:
        return None
    return run_log.progress_ratio(task, elapsed_seconds, fallback_seconds=fallback_seconds)


def find_window_handle(title: str) -> int:
    """按标题找窗口句柄（找不到返回 0）。"""
    try:
        import ctypes

        return int(ctypes.windll.user32.FindWindowW(None, str(title)) or 0)
    except Exception:
        return 0


def default_run_log(app_root: str | Path = ".") -> RunLog:
    return RunLog(Path(app_root) / "runs.jsonl")


# ── 设置（configs/ui_extras.json 里的 taskbar_progress 开关） ──
SETTINGS_FILE = Path("configs") / "ui_extras.json"


def load_enabled(path: str | Path | None = None, default: bool = True) -> bool:
    file_path = Path(path) if path is not None else SETTINGS_FILE
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    if not isinstance(data, dict):
        return default
    return bool(data.get("taskbar_progress", default))


def save_enabled(enabled: bool, path: str | Path | None = None) -> Path:
    file_path = Path(path) if path is not None else SETTINGS_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["taskbar_progress"] = bool(enabled)
    tmp = file_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file_path)
    return file_path
