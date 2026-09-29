from ok import Logger, TriggerTask

from src.core.base_game_task import BaseGameTask
from src.data.feature_list import FeatureList
from src.icons import Icons

logger = Logger.get_logger(__name__)


class SkipDialogTask(BaseGameTask, TriggerTask):
    """跳过剧情触发式任务：自动点击跳过对话框和确认按钮。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "跳剧情"
        self.description = "跳过剧情触发式任务：自动点击跳过对话框和确认按钮。"
        self.icon = Icons.Trigger

    def run(self):
        if not self.find_one(feature=FeatureList.skip_dialog):
            return

        logger.info("检测到跳过对话框，开始处理")

        while True:
            clicked = False
            for frame in self.loop(3, raise_if_time_out=False):
                # 优先处理 Skip
                if skip_dialog := self.find_feature(
                    feature_name=FeatureList.skip_dialog,
                    frame=frame,
                ):
                    self.click(skip_dialog)
                    clicked = True
                    break

                # 处理 Confirm
                if confirm := self.find_confirm():
                    self.click(confirm)
                    clicked = True
                    break

                # 当前帧没有找到，继续尝试
                self.sleep(0.05)

            if not clicked:
                break