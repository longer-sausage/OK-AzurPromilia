"""自动拾取触发式任务：检测到可拾取图标时连按 F 直到图标消失。"""

from ok import Logger, TriggerTask

from src.core.base_game_task import BaseGameTask
from src.data.feature_list import FeatureList
from src.icons import Icons

logger = Logger.get_logger(__name__)

COLLECT_KEY = "f"
COLLECT_AFTER_SLEEP = 0.1
COLLECT_TIMEOUT = 10.0


class AutoPickupTask(BaseGameTask, TriggerTask):
    """自动拾取触发式任务：检测到 interact_collect 时连按 F 直到消失。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动拾取"
        self.description = "检测到可拾取提示时自动连按 F，直到提示消失。"
        self.icon = Icons.Trigger

    def handle_collect(self):
        """持续按 F 拾取直到 interact_collect 消失。"""
        self.send_key(COLLECT_KEY, after_sleep=COLLECT_AFTER_SLEEP)
        for frame in self.loop(COLLECT_TIMEOUT, raise_if_time_out=False):
            if frame is None or not self.find_one(FeatureList.interact_collect, frame=frame):
                break
            self.send_key(COLLECT_KEY, after_sleep=COLLECT_AFTER_SLEEP)

    def run(self):
        if self.find_one(FeatureList.interact_collect):
            logger.info("检测到可拾取目标，开始自动拾取")
            self.handle_collect()
            return True
