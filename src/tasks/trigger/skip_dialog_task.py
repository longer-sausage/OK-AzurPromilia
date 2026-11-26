from ok import Logger, TriggerTask

from src.core.base_game_task import BaseGameTask
from src.data.feature_list import FeatureList
from src.icons import Icons
from src.image.button_detector import ButtonThresholds

logger = Logger.get_logger(__name__)

TUTORIAL_CLOSE_BOX = (0.4120, 0.7741, 0.5854, 0.8231)
TUTORIAL_CLOSE_BUTTON = ButtonThresholds.for_button(
    text_hsv=((0, 0, 200), (180, 25, 255)),
    backdrop_hsv=((84, 40, 34), (96, 68, 52)),
    name="tutorial_close",
    min_backdrop_ratio=0.75,
)


class SkipDialogTask(BaseGameTask, TriggerTask):
    """跳过剧情触发式任务：自动点击跳过对话框和确认按钮。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "跳剧情"
        self.description = "跳过剧情触发式任务：自动点击跳过对话框和确认按钮。"
        self.icon = Icons.Trigger

    def _find_tutorial_close(self, frame=None):
        if frame is None:
            frame = getattr(getattr(self, "executor", None), "frame", None)
        if frame is None:
            return None
        return self.find_button(
            self.box_of_screen(*TUTORIAL_CLOSE_BOX),
            frame=frame,
            name="tutorial_close",
            thresholds=TUTORIAL_CLOSE_BUTTON,
        )

    def handle_dialog(self):
        time = self.active_time()
        checked = False
        for frame in self.loop(5, raise_if_time_out=False):
            # 优先处理 Skip
            if skip_dialog := self.find_one(FeatureList.skip_dialog):
                self.click(skip_dialog)
                time = self.active_time()
                continue

            if check := self.find_one(FeatureList.dialog_remember_uncheck, box=self.box_of_screen(0.4318, 0.5028, 0.4682, 0.5259)):
                self.click(check)
                self.sleep(0.5)
                time = self.active_time()
                continue

            if self.find_one(FeatureList.dialog_remember_check, box=self.box_of_screen(0.4318, 0.5028, 0.4682, 0.5259)):
                checked = True

            if confirm := self.find_skip_story_button(frame=frame):
                self.click(confirm)
                time = self.active_time()
                continue

            if checked and (confirm := self.find_one(
                feature=[FeatureList.confirm_button_2],
                box=self.box_of_screen(0.5755, 0.5935, 0.5979, 0.6352),
            )):
                self.click(confirm)
                time = self.active_time()
                continue

            if self.active_time() - time >= 1:
                break

    def handle_tutorial(self):
        while True:
            clicked = False
            for frame in self.loop(5, raise_if_time_out=False):
                if close_btn := self._find_tutorial_close(frame=frame):
                    self.click(close_btn)
                    clicked = True
                    break

                if feat := self.find_one(FeatureList.tutorial_arrow_right):
                    self.click(feat)
                    clicked = True
                    break

                self.sleep(0.05)

            if not clicked:
                break

    def run(self):
        if self.find_one(FeatureList.skip_dialog):
            logger.info("检测到跳过对话框，开始处理")
            self.handle_dialog()
            return

        if self.find_one(FeatureList.tutorial_arrow_right) or self._find_tutorial_close():
            logger.info("检测到教程，开始处理")
            self.handle_tutorial()
            return
