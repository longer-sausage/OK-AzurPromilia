"""任务队列：把「一条龙 / 实时辅助 / 流程 / 轮椅方案」串成一条顺序队列。

队列是纯数据（JSON，存 ``configs/queues/<名字>.json``），执行由
:class:`QueueRunner` 负责；具体某一项怎么跑交给调用方注入的 ``resolver``，
这样队列本身不依赖任务注册表，也就能离线测试。

预算语义：``budget_minutes`` 是「这一项最多花多久」。流程类可以真的被
``FlowExecutor(budget_seconds=…)`` 打断；任务类无法中途打断，
超预算只记录 ``budget_exceeded``，不谎称已经停止。
"""

from src.core.queue.model import (
    QUEUE_FOLDER,
    QUEUE_KINDS,
    QueueFormatError,
    QueueItem,
    TaskQueue,
    list_queues,
    load_queue,
    queue_path,
    save_queue,
)
from src.core.queue.runner import (
    QueueItemResult,
    QueueRunner,
    QueueRunResult,
)

__all__ = [
    "QUEUE_FOLDER",
    "QUEUE_KINDS",
    "QueueFormatError",
    "QueueItem",
    "QueueItemResult",
    "QueueRunResult",
    "QueueRunner",
    "TaskQueue",
    "list_queues",
    "load_queue",
    "queue_path",
    "save_queue",
]
