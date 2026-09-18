from src.core.BaseGameTask import BaseGameTask
from src.icons import Icons


class TestScreenshotTask(BaseGameTask):
    """测试任务示例：截图并输出识别信息，用于调试识别类功能。"""

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
        path = self.screenshot("test_shot")
        self.log_info(f"已截图：{path}", notify=True)