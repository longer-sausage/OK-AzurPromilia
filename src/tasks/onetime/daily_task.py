"""一键日常任务。

使用迁移的 DailyTaskRunner、daily_summary 和 AccountMixin
来构建一个支持多账户的日常任务。

职责：
1. 注册配置（包括多账户配置）
2. 声明任务清单（build_task_plan）
3. 驱动编排器（run）
4. 生成执行汇总报告
"""

import tempfile
from pathlib import Path

from src.core.base_game_task import BaseGameTask
from src.icons import Icons
from src.tasks.daily.account_mixin import AccountMixin
from src.tasks.daily.daily_feature import DailyFeature
from src.tasks.daily.daily_summary import create_task_summary_report, open_local_path_with_default_app
from src.tasks.daily.daily_task_runner import DailyTaskRunner
from src.tasks.onetime.claim_daily_task import ClaimDailyTask
from src.tasks.onetime.commission_daily_task import CommissionDailyTask
from src.tasks.onetime.home_daily_task import HomeDailyTask


class DailyTask(AccountMixin, BaseGameTask):
    """一键日常任务：多账户 + 任务清单 + 汇总报告。

    继承顺序：AccountMixin 在前，确保多账户能力注入。
    子任务（如家园每日）经 DailyFeature 组合接入，不继承业务逻辑。
    """

    # 允许「多账户独立配置」按账号覆盖本任务的参数
    support_multi_account = True

    # 这些是全账号共用的开关，按账号覆盖没有意义
    account_config_blacklist = {
        "生成汇总文件",
        "自动打开汇总文件",
        "Exit After Task",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "一键日常"
        self.icon = Icons.Task
        self.description = "日常任务编排：多账户 + 任务清单 + 汇总报告"
        self.support_schedule_task = True
        self.daily_runner = None  # DailyTaskRunner 实例

        # 初始化多账户配置（必须在 super().__init__ 之后）
        self._init_account_config()
        # 初始化本任务的默认配置
        self._init_default_config()
        # 家园每日子任务：复用独立任务的执行逻辑，经包装器接入
        self.home_daily = DailyFeature(self, HomeDailyTask, switch_key="家园每日")
        # 每日收菜子任务：邮件、惊喜盒子、日常/周常活跃、大月卡
        self.claim_daily = DailyFeature(self, ClaimDailyTask, switch_key="每日收菜")
        # 委托每日子任务：消耗体力刷选定的每日委托
        self.commission_daily = DailyFeature(self, CommissionDailyTask, switch_key="委托每日")

    def _init_default_config(self):
        """注册日常任务的配置项。"""
        self.default_config.update({
            "家园每日": True,
            "委托每日": False,
            "每日收菜": False,
            "生成汇总文件": True,
            "自动打开汇总文件": False,
        })
        self.config_description.update({
            "家园每日": "执行家园每日：收菜、做饭、喂饭",
            "委托每日": "执行委托每日：消耗体力刷选定的每日委托（需先解锁自动战斗）",
            "每日收菜": "执行每日收菜：邮件、惊喜盒子、日常/周常活跃、大月卡",
            "生成汇总文件": (
                "任务结束后把执行情况写成 txt 汇总\n"
                "目录：系统临时目录/ok-ap/一键日常/"
            ),
            "自动打开汇总文件": "生成汇总后自动用系统默认程序打开它",
        })

    # ── 任务清单 ──────────────────────────────────────────

    def build_task_plan(self):
        """声明日常任务执行清单。

        元素为 (任务名, 执行函数)，任务名同时是配置开关的键名。
        需要自定义开关判定时追加第三个元素「开关谓词」。
        """
        return [
            self.home_daily.plan_item(),
            # 委托每日在前：战斗会推进日常/周常活跃与大月卡任务进度
            self.commission_daily.plan_item(),
            # 每日收菜殿后：把委托战斗产生的活跃度、任务进度一并领走
            self.claim_daily.plan_item(),
        ]

    # ── 主执行入口 ────────────────────────────────────────

    def run(self):
        """任务主入口：驱动 DailyTaskRunner 执行任务清单。"""
        try:
            self.daily_runner = DailyTaskRunner(self, self.build_task_plan())
            self.daily_runner.run()
        finally:
            # 无论正常结束还是异常中断，都尝试落地汇总
            self.run_daily_finally()

    def run_daily_finally(self):
        """生成执行情况汇总 txt。整个过程失败只记日志，不影响任务本身的结果。"""
        try:
            if not self.config.get("生成汇总文件", True):
                return True
            if not (self.daily_runner and self.daily_runner.has_summary_data()):
                self.log_info("无可用汇总信息，跳过生成汇总文件")
                return True

            summary_path = create_task_summary_report(
                self, Path(tempfile.gettempdir()), self.daily_runner.final_summary
            )
            if self.config.get("自动打开汇总文件", False):
                open_local_path_with_default_app(summary_path)
                self.log_info(f"日常执行情况汇总已创建并打开: {summary_path}")
            else:
                self.log_info(f"日常执行情况汇总已创建（未打开）: {summary_path}")
            return True
        except Exception as e:
            self.log_info(f"创建日常任务汇总文件失败: {e}", notify=True)
            return False
