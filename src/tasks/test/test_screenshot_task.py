from src.core.base_game_task import BaseGameTask
from src.core.game_window import ManualCaptureError, capture_manual_frame
from src.icons import Icons


class TestScreenshotTask(BaseGameTask):
    """测试任务示例：截图并输出识别信息，用于调试识别类功能。"""

    needs_frame = False  # 由手动截图入口在窗口置前后取帧，跳过执行器的预取。

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "截图测试"
        self.icon = Icons.Test
        self.description = "截取当前画面并记录截图路径，用于调试"
        # 纯调试任务：只在 debug 模式（main_debug.py）下出现在任务列表
        self.visible = self.debug

        self.default_config = {
            "截图延迟(秒)": 0.5,
        }

    def run(self):
        delay = self.config.get("截图延迟(秒)", 0.5)
        self.sleep(delay)
        try:
            frame = capture_manual_frame(self.executor.device_manager)
        except ManualCaptureError as exc:
            self.log_warning(self.tr(str(exc)), notify=True)
            return
        self.screenshot("test_shot", frame=frame)
        self.log_info(self.tr("已截图"), notify=True)
