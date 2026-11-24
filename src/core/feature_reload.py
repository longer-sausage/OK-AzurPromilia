"""模板热更新的「安全刷新」：把 FeatureSet 的重载放到它自己的锁里。

背景（为什么不能直接调 ``process_data()``）：

``FeatureSet.process_data()`` 的实现是**先清空** ``feature_dict`` / ``box_dict``，
再从盘上的 ``coco_annotations.json`` 重新读一遍合并。所以：

- 它**不是线程安全的** —— 而 ``ensure_feature()``（每次识别都会走）是加锁的；
- 如果 GUI 线程在任务正在跑的时候直接调它，就会有一个「字典先被清空、还没填回来」的窗口，
  这期间任何模板查找都会 miss，任务会把「素材刚更新」当成「识别失败」。

这里提供 :func:`reload_features`：拿 ``feature_set.lock`` 再重载，把这个窗口缩到最小，
并在**任务正在运行时**明确告警（让用户知道那一次识别可能算不准），而不是假装无事发生。

> 仍然做不到「绝对无感」：框架没有提供原子替换 FeatureSet 的接口。真要彻底解决，
> 得把素材安装推迟到任务间隙。这里选择「马上生效 + 明确告警」，因为用户装完素材
> 最自然的期待就是立刻能看到效果。
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

#: 任务正在跑时的告警文案（调用方可以自定义，比如塞进自己的日志）
BUSY_WARNING = "素材已更新，但当前有任务在运行：这一次识别可能因模板缓存重建而漏检，请留意日志"


def _feature_set_of(executor) -> object | None:
    if executor is None:
        return None
    return getattr(executor, "feature_set", None)


def _lock_of(feature_set) -> object:
    """拿 ``feature_set.lock``；没有锁就退化成空上下文（老版本框架）。"""
    lock = getattr(feature_set, "lock", None)
    if lock is not None and hasattr(lock, "__enter__"):
        return lock
    return contextlib.nullcontext()


def is_busy(executor) -> bool:
    """是否真的有任务在跑（``current_task`` 可能是 None 或已停用）。"""
    task = getattr(executor, "current_task", None)
    if task is None:
        return False
    return not bool(getattr(task, "disabled", False))


def reload_features(
    executor,
    log: Callable[[str], None] | None = None,
    warn_when_busy: bool = True,
) -> bool:
    """在锁保护下重载模板缓存；返回 ``feature_set.load_success``（拿不到就 False）。"""
    feature_set = _feature_set_of(executor)
    if feature_set is None:
        return False

    if warn_when_busy and is_busy(executor) and log is not None:
        with contextlib.suppress(Exception):
            log(BUSY_WARNING)

    ok = False
    try:
        with _lock_of(feature_set):
            ok = bool(feature_set.process_data())
    except Exception as exc:  # 刷新失败不该把安装流程一起带崩
        if log is not None:
            with contextlib.suppress(Exception):
                log(f"模板缓存刷新失败：{exc}")
        return False
    if not ok and log is not None:
        with contextlib.suppress(Exception):
            log("模板缓存刷新完成，但框架报告加载失败（素材可能不完整）")
    return ok
