"""自动拾取触发式任务：检测到可拾取图标时连按 F 直到图标消失。

性能约束（重要）
---------------
触发任务由执行器**依次轮询**驱动，每个任务在 ``run()`` 里占用的时间都会挤占
其他触发任务（例如辅助星结）的调度机会。因此本任务遵守两条规则：

1. **不在 ``run()`` 里长时间循环**。早期实现用 ``self.loop(10)`` 连按 F 直到
   提示消失，检测到采集物后会独占执行器最多 10 秒，导致其他视觉类触发任务
   明显卡顿。现在改为每次 ``run()` 至多按一次 F 就立刻返回，把执行器让出去；
   是否继续按由下一次调度（或唤醒）决定。
2. **降低轮询与截图频率**。``trigger_interval`` 让本任务不必每轮都参与；
   不需要动作时直接返回，不主动抓帧。

F 键主动唤醒
------------
物理 F 键被按下时，本任务可以立即介入一次判定与拾取，而不必等待下一次轮询。
按键状态用 ``GetAsyncKeyState`` 旁听物理键盘（与自动冲刺读 WASD 同一套方式），
不会吞掉游戏输入。
"""

from __future__ import annotations

import time

import win32api
from ok import Logger, TriggerTask

from src.core.base_game_task import BaseGameTask
from src.data.feature_list import FeatureList
from src.icons import Icons

logger = Logger.get_logger(__name__)

COLLECT_KEY = "f"
COLLECT_AFTER_SLEEP = 0.1

#: 轮询间隔（秒）：由框架 should_trigger 按此间隔调度，降低截图压力。
POLL_INTERVAL_SECONDS = 0.3
#: 连按 F 的最短间隔（秒），避免同一提示被点成连击。
PRESS_COOLDOWN_SECONDS = 0.15

F_VK_CODE = 0x46  # F
#: 是否旁听物理 F 键做主动唤醒
WAKE_ON_F_CONFIG_KEY = "按F键唤醒"


class AutoPickupTask(BaseGameTask, TriggerTask):
    """自动拾取触发式任务：检测到 interact_collect 时按 F，直到提示消失。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动拾取"
        self.description = (
            "检测到可拾取提示时自动按 F。为避免长时间占用执行器，"
            "每次只按一次 F 就立刻让出调度，需要继续时由下一次轮询或按 F 唤醒。"
        )
        self.icon = Icons.Trigger
        # 不必每轮都参与，降低截图与判定的频率。
        self.trigger_interval = POLL_INTERVAL_SECONDS

        self.default_config.update({WAKE_ON_F_CONFIG_KEY: True})
        self.config_description.update({
            WAKE_ON_F_CONFIG_KEY: (
                "按住或轻点 F 键时立即触发一次拾取判定，不必等待下一次轮询；"
                "按键状态为旁听物理键盘，不会影响游戏内的 F 操作。"
            ),
        })

        self._last_press_at: float | None = None
        self._f_was_down = False

    # ── 按键旁听 ────────────────────────────────────────────

    def _wake_on_f(self) -> bool:
        if self.config is None:
            return True
        return bool(self.config.get(WAKE_ON_F_CONFIG_KEY, True))

    def _is_f_pressed(self) -> bool:
        try:
            return bool(win32api.GetAsyncKeyState(F_VK_CODE) & 0x8000)
        except Exception:
            logger.exception("读取 F 键状态失败")
            return False

    def _poll_f_edge(self) -> bool:
        """F 键由抬起变为按下的那一刻返回 True（只认边沿，避免长按刷判定）。"""
        pressed = self._is_f_pressed()
        edge = pressed and not self._f_was_down
        self._f_was_down = pressed
        return edge

    # ── 主逻辑 ──────────────────────────────────────────────

    def run(self):
        now = time.monotonic()
        f_edge = self._wake_on_f() and self._poll_f_edge()

        # 轮询节奏完全交给框架的 trigger_interval（should_trigger）控制，
        # 这里不再自行比较时间戳：框架用 time.time() 记录 last_trigger_time，
        # 与这里的 time.monotonic() 单位/起点不同，混用会让限流永久成立而卡死任务。
        if not self.find_one(FeatureList.interact_collect):
            return

        # 物理 F 刚被按下时不再补按键：玩家自己那一下已经完成拾取，
        # 这里只负责识别与后续跟进，避免把一次拾取变成双击。
        if f_edge:
            logger.info("检测到玩家按下 F，本轮跳过补按按键")
            return

        if self._last_press_at is not None and now - self._last_press_at < PRESS_COOLDOWN_SECONDS:
            return

        self._last_press_at = now
        self.send_key(COLLECT_KEY, after_sleep=COLLECT_AFTER_SLEEP)
        logger.info("检测到可拾取目标，已按 F；等待下一次调度确认提示是否消失")
