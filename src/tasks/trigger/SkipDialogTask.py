from ok import Logger, TriggerTask

from src.core.BaseGameTask import BaseGameTask
from src.data.FeatureList import FeatureList
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

        deadline = self.active_time() + 3

        while self.active_time() < deadline:
            frame = self.next_frame()

            # 优先处理 Skip
            if skip_dialog := self.find_feature(
                feature_name=FeatureList.skip_dialog,
                frame=frame,
            ):
                self.click(skip_dialog)

                # 成功触发一次操作，重新给连续对话留时间
                deadline = self.active_time() + 3
                continue

            # 处理 Confirm
            if confirm := self.find_confirm():
                self.click(confirm)

                self.wait_until(
                    lambda: not self.find_confirm(),
                    time_out=0.8,
                    raise_if_not_found=False,
                )

                # 成功点击后，继续等待下一段
                deadline = self.active_time() + 3
                continue

            # 当前帧没有找到，继续尝试
            self.sleep(0.05)