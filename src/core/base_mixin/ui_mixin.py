from __future__ import annotations

import time
from typing import Any

from ok import Logger, WaitFailedException

from src.data.page import Page, PageNotFoundError

logger = Logger.get_logger(__name__)


class UIMixin:
    """UI 路由导航 Mixin，提供基于 Page 拓扑的状态检测与跳转能力。"""

    def ui_page_appear(self, page: Page | str) -> bool:
        """检查当前帧是否匹配指定页面的特征。"""
        target_page = Page.get(page)
        if target_page.check_feature is None:
            return False
        return bool(self.find_one(target_page.check_feature, **target_page.check_kwargs) is not None)

    def ui_goto(
        self,
        destination: Page | str,
        time_out: float = 30.0,
        interval: float | None = None,
        page_not_found_tolerance: float = 3.0,
    ) -> bool:
        """通过 BFS 路由拓扑导航前往目标页面。

        Args:
            destination: 目标页面实例或页面名称。
            time_out: 最大导航超时时间（秒）。
            interval: 页面切换动作后的防抖等待时间（秒）。默认读取 self.once_sleep_time 或 1.0。
            page_not_found_tolerance: 连续无法识别当前所在页面的最大容忍时间（秒）。
                超过此时间仍未能匹配任何已注册页面时抛出 PageNotFoundError。

        Returns:
            bool: 是否成功到达目标页面。

        Raises:
            WaitFailedException: 导航超时或目标页面未到达。
            PageNotFoundError: 连续未识别当前页面超过 page_not_found_tolerance 秒。
            KeyError: 目标页面未在 Page 注册表中。
        """
        dest_page = Page.get(destination)

        Page.init_connection(dest_page)
        if interval is None:
            interval = getattr(self, "once_sleep_time", 1.0)

        logger.info(f"UI goto destination: {dest_page}")
        page_not_found_time = None

        try:
            for _ in self.loop(time_out, yield_frame=True, raise_if_time_out=False):
                # 检查任务退出信号与禁用状态
                if (
                    hasattr(self, "executor")
                    and getattr(self.executor, "exit_event", None)
                    and self.executor.exit_event.is_set()
                ):
                    logger.info("UI goto aborted: task exit event is set")
                    return False
                if hasattr(self, "is_task_disabled") and self.is_task_disabled():
                    logger.info("UI goto aborted: task is disabled")
                    return False

                # 已到达目标页面
                if self.ui_page_appear(page=dest_page):
                    logger.info(f"Page arrived: {dest_page}")
                    return True

                # 遍历所有已连通的页面并寻找当前所在页面
                current_page: Page | None = None
                for page in Page.iter_pages():
                    if page.parent is None or page.check_feature is None:
                        continue
                    if self.ui_page_appear(page):
                        current_page = page
                        break

                if current_page is not None:
                    page_not_found_time = None
                    next_page = current_page.parent
                    if next_page is not None:
                        button = current_page.links.get(next_page)
                        logger.info(f"Page switch: {current_page} -> {next_page} via {button}")
                        self._ui_execute_action(button)
                        if interval > 0:
                            self.sleep(interval)
                else:
                    if page_not_found_time is None:
                        page_not_found_time = time.monotonic()
                    elif time.monotonic() - page_not_found_time > page_not_found_tolerance:
                        raise PageNotFoundError('Unknown page.')
                    time.sleep(0.1)

            # 超时检测
            logger.error(f"UI goto {dest_page} timed out after {time_out}s")
            raise WaitFailedException(f"UI goto {dest_page} timed out after {time_out}s")
        finally:
            Page.clear_connection()

    def _ui_execute_action(self, button: Any) -> None:
        """执行页面切换动作（以 task 为参数的回调或点击目标）。"""
        if callable(button):
            button(self)
        elif button is not None:
            self.click(button)

    def ui_ensure(self, page: Page | str, time_out: float = 30.0) -> bool:
        """确保当前处于指定页面；若不在则触发导航。

        Args:
            page: 目标页面实例或页面名称。
            time_out: 导航超时时间（秒）。

        Returns:
            bool: 是否成功处于目标页面。
        """
        self.next_frame()
        if self.ui_page_appear(page):
            return True
        return self.ui_goto(page, time_out=time_out)
