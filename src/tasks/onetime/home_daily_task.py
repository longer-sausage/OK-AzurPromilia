from __future__ import annotations

from ok import WaitFailedException

from src.core.account_override_mixin import AccountOverrideMixin
from src.core.base_game_task import BaseGameTask
from src.data.page import page_main, page_home, page_home_building, page_home_building_pot, page_home_restaurant
from src.data.feature_list import FeatureList
from src.core.detector.template_detector import TemplateDetector
from src.core.detector.ocr_detector import OcrDetector
from src.icons import Icons

class HomeDailyTask(BaseGameTask):
    """家园每日任务：收菜 → 做饭 → 喂饭 → 回主界面。

    独立运行；也可由 ``DailyFeature`` 包装后接入一键日常（日常不继承本类）。
    """

    # ── 子任务参数（供 DailyFeature 接入日常时复用） ──
    # 账号覆盖存储挂的任务名；参数配置文件即 configs/<sub_task_config_name>.json，
    # 由框架加载进本实例的 self.config。
    sub_task_config_name = "HomeDailyTask"
    # 参数声明：{配置键: 默认值}。声明后：
    #   1. 本任务面板可编辑（_init_home_daily_config 注册）
    #   2. 被日常执行时经 _sub_task_cfg 读取（多账号覆盖 → 自身配置 → 默认值）
    #   3. 「账号配置」页可按账号覆盖（account_config_tab 收集声明了参数的子任务）
    # 目前暂无参数；未来加参数时在这里填，例如 {"喂饭阈值": 100}。
    sub_task_default_config: dict = {}
    # 与 sub_task_default_config 同键的说明文案
    sub_task_config_description: dict = {}
    # 运行时由 DailyFeature 注入：宿主的账号覆盖查询 fn(config_name) -> dict。
    # 独立运行时为 None，参数直接读自身配置。
    _account_override_provider = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "家园每日"
        self.icon = Icons.Task
        self.description = "完成家园每日任务"
        # 注册子任务参数声明
        self._init_home_daily_config()

    def _init_home_daily_config(self):
        """把参数声明注册进本任务的 default_config / config_description。"""
        self.default_config.update(self.sub_task_default_config)
        self.config_description.update(self.sub_task_config_description)

    def _sub_task_cfg(self, key, default=None):
        """子任务参数取值。业务代码读参数一律走本方法，不要直接 self.config。

        解析顺序：
        1. 多账号覆盖：由 DailyFeature 注入的宿主查询（内部已判断
           任务运行中、多账户独立配置开启、已设置当前账号），命中时
           以自身配置值为基准做类型校正；
        2. 自身配置（框架已加载 configs/<任务名>.json），缺键回落默认值。
        """
        declared_default = self.sub_task_default_config.get(key, default)
        provider = self._account_override_provider
        if provider is not None:
            try:
                overrides = provider(self.sub_task_config_name) or {}
            except Exception:
                overrides = {}
            if key in overrides:
                base = self.config.get(key, declared_default)
                return AccountOverrideMixin._coerce_override_value(base, overrides.get(key))
        return self.config.get(key, declared_default)

    def _handle_claim_popup(self, checker):
        stable_frame = 0
        for _ in self.loop(10, raise_if_time_out=False):
            if self.find_one(checker):
                stable_frame += 1
                if stable_frame >= 5:
                    return
                self.sleep(0.1)
                continue
            stable_frame = 0

            if box := self.find_one(FeatureList.home_claim_cross):
                self.click(box)
                self.sleep(0.1)
                continue

            if self.find_one(FeatureList.home_levelup_popup):
                self.click(self.box_of_screen(0.4818, 0.6907, 0.5198, 0.7269))
                self.sleep(0.1)
                continue
        raise WaitFailedException('Claim task of HomeDaily timeout.')

    def claim(self):
        self.ui_ensure(page_home)
        if self.wait_action_result(
            condition=TemplateDetector(FeatureList.home_ranch_claim),
            action=lambda box: self.click(box),
            expect=TemplateDetector(FeatureList.home_claim_cross),
            max_attempts=3,
            time_out=2,
        ):
            self._handle_claim_popup(FeatureList.home_kibo_manage)

        self.ui_ensure(page_home_building)
        self.wait_action_result(
            action=lambda: self.click(self.box_of_screen(0.0333, 0.8093, 0.0656, 0.8676)),
            expect=TemplateDetector(FeatureList.home_claim_cross),
            max_attempts=3
        )
        self._handle_claim_popup(FeatureList.home_building_check)

    def make_food(self):
        self.ui_ensure(page_home_building_pot)
        self.wait_action_result(
            action=lambda: self.click(self.box_of_screen(0.3797, 0.1565, 0.3906, 0.1731)),
            expect=TemplateDetector(FeatureList.pot_category_all_activated),
            max_attempts=3
        )
        food_hit = self.detect_with_scroll(
            TemplateDetector(
                FeatureList.food_little_bobo,
                box=self.box_of_screen(0.2865, 0.2176, 0.6188, 0.8667),
            ),
            self.box_of_screen(0.2865, 0.2176, 0.6188, 0.8667),
        )
        if not self.wait_action_result(
            action=lambda: self.click(food_hit),
            expect=OcrDetector(
                match=self.lang.home.little_bobo,
                box=self.box_of_screen(0.6797, 0.1852, 0.7786, 0.2241)
            ),
            max_attempts=3
        ):
            self.log_warning('Failed to make food.')
            return

        self.click(self.box_of_screen(0.8792, 0.7639, 0.8896, 0.7806))
        self.sleep(0.1)
        self.click(self.box_of_screen(0.7849, 0.8167, 0.8380, 0.8519))

    def feed(self):
        self.ui_ensure(page_home_restaurant)
        for frame in self.loop(10):
            boxes = self.ocr(box=self.box_of_screen(0.4557, 0.2389, 0.5427, 0.2611), frame=frame)
            if not boxes or not (result := boxes[0].name):
                continue
            if result.count('/') != 1:
                continue

            result = result.split('/')
            current = int(result[0].strip())
            total = int(result[1].strip())

            if total - current < 100:
                return

            self.click(self.box_of_screen(0.8375, 0.8667, 0.8630, 0.9157))
            self.sleep(0.2)

    def run_home_daily(self):
        """家园每日完整流程，独立运行与被日常执行共用。"""
        # 收菜
        self.claim()

        # 做饭
        self.make_food()

        # 喂饭
        self.feed()

        # 回主页面
        self.ui_ensure(page_main)

    def run(self):
        self.run_home_daily()
