"""日常子任务包装器：把独立任务接进日常任务清单，日常任务不继承业务 mixin。

用法（DailyTask）::

    self.home_daily = DailyFeature(self, HomeDailyTask, switch_key="家园每日")
    # build_task_plan 里：
    return [self.home_daily.plan_item()]

机制：
- 执行时从 executor 已注册的一次性任务实例里找到子任务实例——框架已替它把
  ``configs/<任务名>.json`` 加载成内存配置，参数读取直接用那份（真正的
  「沿用子任务的配置」），无需再读文件；
- 执行前把宿主的账号上下文与账号覆盖查询能力注入子任务实例，使
  「多账户独立配置」对子任务参数生效（子任务实现里经 ``_sub_task_cfg``
  取参数即可享受）；执行后恢复原状；
- 子任务未注册时记日志并把该项记为失败（返回 False），不中断日常其余任务。
"""

from __future__ import annotations


class DailyFeature:
    """把一个独立任务包装成 ``DailyTaskRunner`` 任务清单的子任务项。"""

    def __init__(self, host, task_class, switch_key: str, run_method: str = "run"):
        """
        Args:
            host: 日常宿主任务实例（DailyTask），提供 executor 与账号上下文。
            task_class: 子任务类（如 HomeDailyTask），须已在 ``onetime_tasks`` 注册。
            switch_key: 日常配置里控制本子任务的开关键名。
            run_method: 执行时调用的子任务方法名，默认 ``run``。
        """
        self.host = host
        self.task_class = task_class
        self.switch_key = switch_key
        self.run_method = run_method

    def plan_item(self):
        """返回任务清单元素 ``(任务名, 执行函数)``，任务名即配置开关键名。"""
        return (self.switch_key, self.run)

    def _resolve_impl(self):
        """从 executor 已注册的一次性任务实例里找子任务实例，找不到返回 None。"""
        for task in getattr(self.host.executor, "onetime_tasks", []) or []:
            if isinstance(task, self.task_class):
                return task
        return None

    def run(self):
        """在子任务实例上执行业务流程，前后注入/恢复宿主的账号上下文。"""
        impl = self._resolve_impl()
        if impl is None:
            self.host.log_info(
                f"未找到 {self.task_class.__name__} 的已注册实例，{self.switch_key} 记为失败",
                notify=True,
            )
            return False

        # 注入宿主上下文，使子任务参数的多账号覆盖生效；结束后恢复原状
        backup = (
            getattr(impl, "current_account_id", ""),
            getattr(impl, "current_user", ""),
            getattr(impl, "_account_override_provider", None),
        )
        impl.current_account_id = getattr(self.host, "current_account_id", "")
        impl.current_user = getattr(self.host, "current_user", "")
        impl._account_override_provider = (
            self.host._account_override_for
            if hasattr(self.host, "_account_override_for")
            else None
        )
        try:
            return getattr(impl, self.run_method)()
        finally:
            (
                impl.current_account_id,
                impl.current_user,
                impl._account_override_provider,
            ) = backup
