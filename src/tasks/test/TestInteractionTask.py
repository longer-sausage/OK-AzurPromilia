from src.core.BaseGameTask import BaseGameTask
from src.data.FeatureList import FeatureList
from src.icons import Icons


class TestInteractionTask(BaseGameTask):
    """测试不同输入方式点击账号切换按钮。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "输入方式测试"
        self.icon = Icons.Test
        self.description = "测试不同输入方式点击 account_switch"
        # 纯调试任务：只在 debug 模式（main_debug.py）下出现在任务列表
        self.visible = self.debug
        self.default_config = {
            "点击方式": "post",
        }
        self.config_type = {
            "点击方式": {
                "type": "drop_down",
                "options": ["post", "foreground_post", "pynput", "pydirect", "alt_click"],
            },
        }

    def run(self):
        method = self.config.get("点击方式", "post")
        self.log_info(f"测试输入方式: {method}", notify=True)
        self.ensure_tool_window_capture()
        if method == "post":
            self._test_post()
        elif method == "foreground_post":
            self._test_foreground_post()
        elif method == "pynput":
            self._test_pynput()
        elif method == "pydirect":
            self._test_pydirect()
        elif method == "alt_click":
            self._test_alt_click()

    def _test_post(self):
        """PostMessage 后台点击（默认方式）"""
        self.log_info("使用 PostMessage 点击")
        result = self.wait_click_feature(
            feature=FeatureList.account_switch,
            time_out=10,
        )
        self.log_info(f"PostMessage 点击结果: {result}", notify=True)

    def _test_foreground_post(self):
        """前台 + PostMessage"""
        self.log_info("使用 ForegroundPostMessage 点击")
        self.ensure_in_front()
        self.sleep(0.5)
        result = self.wait_click_feature(
            feature=FeatureList.account_switch,
            time_out=10,
        )
        self.log_info(f"ForegroundPostMessage 点击结果: {result}", notify=True)

    def _test_pynput(self):
        """pynput 前台点击"""
        self.log_info("使用 pynput 点击")
        self.ensure_in_front()
        self.sleep(0.5)
        result = self.wait_click_ocr(
            match="account_switch",
            time_out=10,
            alt=True,
        )
        self.log_info(f"pynput 点击结果: {result}", notify=True)

    def _test_pydirect(self):
        """pydirectinput 前台点击"""
        self.log_info("使用 pydirectinput 点击")
        self.ensure_in_front()
        self.sleep(0.5)
        result = self.wait_click_ocr(
            match="account_switch",
            time_out=10,
            alt=True,
        )
        self.log_info(f"pydirectinput 点击结果: {result}", notify=True)

    def _test_alt_click(self):
        """Alt + 点击"""
        self.log_info("使用 Alt+Click 点击")
        result = self.wait_click_ocr(
            match="account_switch",
            time_out=10,
            alt=True,
        )
        self.log_info(f"Alt+Click 点击结果: {result}", notify=True)
