"""Allow TriggerTask/BaseTask to declare needs_frame=False to skip screenshot pipeline."""

from __future__ import annotations

import logging

_PATCH_INSTALLED = False

logger = logging.getLogger(__name__)


class _FrameReadySentinel:
    """Non-None sentinel for frameless tasks to bypass upstream `is None` check."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<needs_frame=False>"

    def __bool__(self) -> bool:
        return True

    def __getattr__(self, item):
        raise AttributeError(
            f"Task declared needs_frame=False, should not access frame attribute {item!r}"
        )


_FRAME_READY = _FrameReadySentinel()


def _current_task(executor):
    return getattr(executor, "current_task", None)


def _task_wants_frame(executor) -> bool:
    return getattr(_current_task(executor), "needs_frame", True)


def install_no_frame_task_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.task.task import BaseTask
    from ok.task.TaskExecutor import TaskExecutor

    _require_upstream_contract(TaskExecutor)

    BaseTask.needs_frame = True

    _original_next_frame = TaskExecutor.next_frame

    def _patched_next_frame(self, time_out=6):
        if not _task_wants_frame(self):
            return _FRAME_READY
        return _original_next_frame(self, time_out)

    _patched_next_frame.__wrapped__ = _original_next_frame
    TaskExecutor.next_frame = _patched_next_frame

    _original_reset_scene = TaskExecutor.reset_scene

    def _patched_reset_scene(self, check_enabled=True):
        if not _task_wants_frame(self):
            if check_enabled:
                self.check_enabled()
            return None
        return _original_reset_scene(self, check_enabled)

    _patched_reset_scene.__wrapped__ = _original_reset_scene
    TaskExecutor.reset_scene = _patched_reset_scene

    _PATCH_INSTALLED = True
    logger.info("no_frame_task_patch installed")


def _require_upstream_contract(TaskExecutor) -> None:
    required_executor_attrs = ("next_frame", "reset_scene", "execute", "check_enabled")
    missing = [name for name in required_executor_attrs if not hasattr(TaskExecutor, name)]
    if missing:
        raise RuntimeError(
            f"no_frame_task_patch missing TaskExecutor attrs: {missing}"
        )
