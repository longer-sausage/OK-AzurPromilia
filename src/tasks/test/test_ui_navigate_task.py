from __future__ import annotations

from ok import WaitFailedException

from src.core.base_game_task import BaseGameTask
from src.data.page import Page
from src.icons import Icons


class TestUINavigateTask(BaseGameTask):
    """测试任务：通过 UI 路由拓扑导航前往指定的 UI 界面。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "UI导航测试"
        self.icon = Icons.Test
        self.description = "测试通过 UI 路由拓扑导航前往指定的 UI 界面"
        # 纯调试任务：只在 debug 模式（main_debug.py）下出现在任务列表
        self.visible = self.debug

        registered_pages = list(Page.all_pages.keys())
        default_target = (
            "page_home"
            if "page_home" in registered_pages
            else (registered_pages[0] if registered_pages else "page_home")
        )

        self.default_config = {
            "目标界面": default_target,
            "超时时间(秒)": 30.0,
            "防抖等待(秒)": 1.0,
        }
        self.config_description = {
            "目标界面": "选择需要导航前往的目标 UI 界面",
            "超时时间(秒)": "UI 导航的最大超时时间（秒）",
            "防抖等待(秒)": "每次页面切换动作后的防抖等待时间（秒）",
        }
        self.config_type = {
            "目标界面": {
                "type": "drop_down",
                "options": registered_pages or [default_target],
            },
        }

    def run(self):
        destination = self.config.get("目标界面", "page_home")
        time_out = float(self.config.get("超时时间(秒)", 30.0))
        interval = float(self.config.get("防抖等待(秒)", 1.0))

        self.log_info(f"开始导航前往目标界面: {destination}", notify=True)
        try:
            success = self.ui_goto(destination, time_out=time_out, interval=interval)
            if success:
                self.log_info(f"成功到达目标界面: {destination}", notify=True)
            else:
                self.log_info(f"导航已终止或未完成: {destination}", notify=True)
        except WaitFailedException as e:
            self.log_error(f"导航超时: {e}", notify=True)
            raise
        except Exception as e:
            self.log_error(f"导航发生异常: {e}", notify=True)
            raise
