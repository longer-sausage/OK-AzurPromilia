"""崩溃诊断：未捕获异常写堆栈 + 自动打诊断包。

三个入口都挂上：

- ``sys.excepthook``：主线程未捕获异常；
- ``threading.excepthook``：后台线程未捕获异常（框架的后台线程不少）；
- ``faulthandler``：解释器级别的崩溃（段错误等）写到 ``logs/crash_faulthandler.log``。

写出来的东西：

```text
crashes/
├── crash_20261124_013000.txt     # 时间、线程、完整堆栈
└── ok-ap-diagnostics-….zip        # 同目录的诊断包（可选）
```

**UI 线程的异常只提示不退出**：Qt 的槽函数里抛异常默认会被吞掉，
所以「提示」这一步交给 ``notify`` 回调（挂到框架的通知/日志上），
真正决定退不退出的仍然是框架。这里只保证**现场一定落盘**。
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

CRASH_FOLDER = Path("crashes")
FAULT_LOG = Path("logs") / "crash_faulthandler.log"
MAX_TEXT_BYTES = 200 * 1024


@dataclass
class CrashRecord:
    path: Path | None = None
    bundle: Path | None = None
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None

    def summary(self) -> str:
        if not self.ok:
            return f"崩溃记录写入失败：{self.error}"
        extra = f"，诊断包 {self.bundle.name}" if self.bundle else ""
        return f"崩溃记录：{self.path}{extra}"


@dataclass
class CrashGuard:
    app_root: Path = field(default_factory=lambda: Path("."))
    notify: Callable[[str], None] | None = None
    make_bundle: bool = True
    installed: bool = False
    _fault_handle: object | None = None
    _previous_hooks: tuple | None = None

    # ── 安装 ─────────────────────────────────────────────
    def install(self) -> bool:
        """挂上三个钩子；重复调用是幂等的。"""
        if self.installed:
            return True
        try:
            self._ensure_dirs()
            self._previous_hooks = (sys.excepthook, threading.excepthook)
            sys.excepthook = self._sys_hook
            threading.excepthook = self._thread_hook
            try:
                # 句柄要活到进程结束（faulthandler 会一直往里写），所以不用 with
                self._fault_handle = open(  # noqa: SIM115
                    self.app_root / FAULT_LOG, "a", encoding="utf-8"
                )
                faulthandler.enable(file=self._fault_handle)
            except Exception:
                self._fault_handle = None
            self.installed = True
            return True
        except Exception as exc:
            self._notify(f"崩溃诊断安装失败：{exc}")
            return False

    def close(self) -> None:
        """卸掉钩子并关掉 faulthandler 的句柄（测试与退出清理用）。"""
        with contextlib.suppress(Exception):
            if self._fault_handle is not None:
                faulthandler.disable()
                self._fault_handle.close()
        self._fault_handle = None
        if self._previous_hooks is not None:
            with contextlib.suppress(Exception):
                sys.excepthook, threading.excepthook = self._previous_hooks
            self._previous_hooks = None
        self.installed = False

    def _ensure_dirs(self) -> None:
        (self.app_root / CRASH_FOLDER).mkdir(parents=True, exist_ok=True)
        (self.app_root / FAULT_LOG.parent).mkdir(parents=True, exist_ok=True)

    # ── 钩子 ─────────────────────────────────────────────
    def _sys_hook(self, exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        self.record(exc_type, exc, tb, thread_name="MainThread")
        if not issubclass(exc_type, KeyboardInterrupt):
            self._notify(f"程序异常：{exc_type.__name__}: {exc}")

    def _thread_hook(self, args: threading.ExceptHookArgs) -> None:
        thread_name = getattr(args.thread, "name", "thread") if args.thread else "thread"
        self.record(args.exc_type, args.exc_value, args.exc_traceback, thread_name=thread_name)
        self._notify(f"后台线程 {thread_name} 异常：{args.exc_type.__name__}: {args.exc_value}")

    # ── 记录 ─────────────────────────────────────────────
    def record(
        self,
        exc_type: type[BaseException] | None = None,
        exc: BaseException | None = None,
        tb: TracebackType | None = None,
        thread_name: str = "MainThread",
        text: str | None = None,
    ) -> CrashRecord:
        """写一份崩溃记录（+ 诊断包）。任何异常都不外抛。"""
        try:
            self._ensure_dirs()
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            path = self.app_root / CRASH_FOLDER / f"crash_{stamp}_{os.getpid()}.txt"
            index = 1
            while path.exists():
                path = self.app_root / CRASH_FOLDER / f"crash_{stamp}_{os.getpid()}_{index}.txt"
                index += 1

            if text is None:
                text = self._format(exc_type, exc, tb, thread_name)
            path.write_text(text[:MAX_TEXT_BYTES], encoding="utf-8")

            record = CrashRecord(path=path, text=text[:MAX_TEXT_BYTES])
            if self.make_bundle:
                record.bundle = self._write_bundle(exc_type, exc)
            return record
        except Exception as error:
            return CrashRecord(error=str(error))

    def _format(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        thread_name: str,
    ) -> str:
        lines = [
            f"time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}",
            f"thread: {thread_name}",
            f"pid: {os.getpid()}",
            f"python: {sys.version.split()[0]}",
            f"cwd: {os.getcwd()}",
            "",
        ]
        if exc is not None and tb is not None:
            lines.extend(traceback.format_exception(exc_type or type(exc), exc, tb))
        elif exc is not None:
            lines.append(f"{type(exc).__name__}: {exc}")
        else:
            lines.append("(no exception info)")
        return "".join(lines)

    def _write_bundle(self, exc_type, exc) -> Path | None:
        try:
            from src.core.diagnostics_bundle import build_bundle

            extra = {
                "crash": {
                    "type": getattr(exc_type, "__name__", str(exc_type)),
                    "message": str(exc) if exc is not None else "",
                }
            }
            result = build_bundle(
                dest_dir=self.app_root / CRASH_FOLDER,
                app_root=self.app_root,
                extra_system=extra,
            )
            return result.path if result.ok else None
        except Exception:
            return None

    def _notify(self, message: str) -> None:
        if self.notify:
            try:
                self.notify(message)
                return
            except Exception:
                pass
        with contextlib.suppress(Exception):
            print(message, file=sys.stderr)


def install(app_root: str | Path = ".", notify: Callable[[str], None] | None = None,
            make_bundle: bool = True) -> CrashGuard:
    """便捷入口：建一个已安装的守卫。"""
    guard = CrashGuard(app_root=Path(app_root), notify=notify, make_bundle=make_bundle)
    guard.install()
    return guard
