"""自动战斗触发任务：全自动 / 捉宠两种模式。

- 全自动：持续战斗直到判定脱战；
- 捉宠：把敌人打到血量阈值以下就停手（可自动星结），按脱战处理；
- 副本门禁：检测到游戏自带自动战斗可用时，停止本任务，交给游戏打。

键位、区域、颜色阈值集中在 ``src/combat/profile.py``，待游戏内实测校准。
"""

from ok import Logger, TriggerTask

from src.combat.base_combat_task import BaseCombatTask
from src.icons import Icons

logger = Logger.get_logger(__name__)


class AutoCombatTask(BaseCombatTask, TriggerTask):
    """战斗调度触发任务：自动放技能/大招/切人。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动战斗"
        self.description = "战斗中按角色计划释放技能、大招并自动切人。"
        self.icon = Icons.Trigger
        self.trigger_interval = 0

        self.default_config.update(
            {
                "_战斗模式": "全自动",
                "释放大招": True,
                "释放技能": True,
                "释放E技能": True,
                "自动普攻": True,
                "已解锁自动战斗时让给游戏": True,
                "_捉宠后按星结": True,
                "_角色脚本": "generic",
                "_捉宠血量阈值(%)": 20,
                "_进入战斗确认帧数": 2,
                "_退出战斗确认帧数": 3,
                "_不确定期(秒)": 0.25,
                "_索敌时长(秒)": 1.0,
                "_索敌间隔(秒)": 0.3,
                "_续战确认帧数": 2,
                "_索敌次数上限": 3,
                "_索敌扫描间隔(秒)": 1.0,
                "_索敌续战时长(秒)": 3.0,
                "_启用槽位识别": True,
                "_技能冷却(秒)": 8.0,
                "_副技能冷却(秒)": 8.0,
                "_大招冷却(秒)": 20.0,
                "_切人冷却(秒)": 3.0,
                "_就绪重试间隔(秒)": 2.0,
                "_普攻间隔(秒)": 0.35,
                "_自动交互": True,
                "_交互冷却(秒)": 1.5,
                "_判据日志间隔(秒)": 10.0,
            }
        )
        self.config_description.update(
            {
                "释放大招": "战斗中对大招键按优先级释放。",
                "释放技能": "战斗中对技能键（Q）按优先级释放。",
                "释放E技能": "战斗中对副技能键（E）按优先级释放。",
                "自动普攻": "没有技能可用时自动点击普攻。",
                "已解锁自动战斗时让给游戏": "检测到游戏自带自动战斗可用时，停止本任务，交给游戏自己打。",
            }
        )

    def create_objective(self):
        """模式切换后重建目标对象。"""
        return super().create_objective()

    def run(self):
        return self.combat_tick()
