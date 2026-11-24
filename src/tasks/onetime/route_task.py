"""导航路线任务：跑一条保存在 ``configs/routes/`` 下的路线。

「跑通一次就存下来」比每次手点更省事，路线本身是纯 JSON（见 :mod:`src.core.route`），
可以在「流程与队列」页里导入/校验，也可以直接编辑。

配置项：

- `路线文件`：`configs/routes` 下的名字（不带 `.json`）
- `从断点继续`：跳过上次已经跑完的航点
"""

from __future__ import annotations

from src.core.base_game_task import BaseGameTask
from src.core.flow import CheckpointStore
from src.core.route import (
    RouteContext,
    RouteFormatError,
    RouteRunner,
    list_routes,
    load_route,
    route_fingerprint,
)
from src.icons import Icons


class RouteTask(BaseGameTask):
    """按航点跑一条导航路线。"""

    support_schedule_task = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "导航路线"
        self.icon = Icons.Task
        self.description = (
            "按保存好的路线逐个航点导航/等待/点击，跑通一次就能存下来复用。"
            "路线文件在 configs/routes 下。"
        )
        self.default_config = {
            "路线文件": "",
            "从断点继续": True,
        }
        self.config_description = {
            "路线文件": "configs/routes 下的文件名（不带 .json）",
            "从断点继续": "跳过上次已经跑完的航点",
        }

    def run(self):
        name = str(self.config.get("路线文件", "") or "").strip()
        if not name:
            available = list_routes()
            hint = f"可选：{'、'.join(available[:8])}" if available else "还没有任何路线文件"
            self.log_info(f"没有填写路线文件；{hint}", notify=True)
            return False
        try:
            route = load_route(name)
        except RouteFormatError as exc:
            self.log_info(f"路线读取失败：{exc}", notify=True)
            return False

        context = RouteContext(self)
        runner = RouteRunner(
            route,
            context,
            checkpoint=CheckpointStore().for_key(
                f"route_{route.name}", fingerprint=route_fingerprint(route)
            ),
            log=self.log_info,
            default_timeout=self.runtime.step_timeout,
        )
        try:
            result = runner.run(resume=bool(self.config.get("从断点继续", True)))
        except Exception as exc:
            self.mark_task_failure(f"路线执行失败：{exc}", task_name="Route")
            return False
        self.log_info(result.summary(), notify=True)
        return result.ok
