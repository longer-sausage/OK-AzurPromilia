from __future__ import annotations

from functools import cached_property

from src.core.account_override_mixin import AccountOverrideMixin
from src.core.base_game_task import BaseGameTask
from src.data.page import page_main, page_commission_daily_material, page_commission_daily_boss, page_commission_daily_equipment
from src.data.feature_list import FeatureList
from src.core.detector.template_detector import TemplateDetector
from src.core.detector.ocr_detector import OcrDetector
from src.icons import Icons
from src.image.hsv_config import HSVRange
from src.image.frame_processes import make_hsv_isolator

ALL_COMISSIONS = {
    'daily_material': [
        '银光闪闪',
        '结晶萃取',
        '千锤百炼',
    ],
    'daily_boss': [
        '无惧之战砧',
        '凋零的挽歌',
        '温和的雷鸣',
        '深巢梦魇',
        '林间幻梦',
        '吞噬之渊',
        '焚灼之域',
    ],
    'daily_equipment': [
        '苍雷之卫',
        '烈炎之佑',
        '常青之庇',
        '湍流之守',
        '丘薮之陲',
        '长风之护',
        '厚岩之盾',
        '严寒之屏',
        '日月之捍',
        '急炽之御',
    ]
}

COMMISSION_PAGES = {
    'daily_material': page_commission_daily_material,
    'daily_boss': page_commission_daily_boss,
    'daily_equipment': page_commission_daily_equipment,
}

COMMISSION_COSTS = {
    'daily_material': 30,
    'daily_boss': 30,
    'daily_equipment': 40,
}

class CommissionDailyTask(BaseGameTask):
    """委托每日任务：消耗体力反复刷选定的每日委托。

    独立运行；也可由 ``DailyFeature`` 包装后接入一键日常（日常不继承本类）。
    """

    # ── 子任务参数（供 DailyFeature 接入日常时复用） ──
    # 账号覆盖存储挂的任务名；参数配置文件即 configs/<sub_task_config_name>.json，
    # 由框架加载进本实例的 self.config。
    sub_task_config_name = "CommissionDailyTask"
    # 参数声明：{配置键: 默认值}。声明后：
    #   1. 本任务面板可编辑（_init_commission_daily_config 注册）
    #   2. 被日常执行时经 _sub_task_cfg 读取（多账号覆盖 → 自身配置 → 默认值）
    #   3. 「账号配置」页可按账号覆盖（account_config_tab 收集声明了参数的子任务）
    sub_task_default_config: dict = {
        "选择委托": "银光闪闪",
    }
    # 与 sub_task_default_config 同键的说明文案
    sub_task_config_description: dict = {}
    # 与 sub_task_default_config 同键的控件类型
    sub_task_config_type: dict = {
        "选择委托": {
            "type": "cascade_drop_down",
            "options": ALL_COMISSIONS,
            "labels": {
                'daily_material': '每日委托-基础材料',
                'daily_boss': '每日委托-首领挑战',
                'daily_equipment': '每日委托-武备获取',
            }
        },
    }
    # 运行时由 DailyFeature 注入：宿主的账号覆盖查询 fn(config_name) -> dict。
    # 独立运行时为 None，参数直接读自身配置。
    _account_override_provider = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "委托每日"
        self.icon = Icons.Task
        self.description = "完成委托家园每日任务，需要在游戏设置中打开“自动托管领取原初之流宝箱”并且至少通关过副本一遍解锁自动战斗。"
        # 注册子任务参数声明（含控件类型）
        self._init_commission_daily_config()

        self.commission_types = {}
        for type in ALL_COMISSIONS:
            for name in ALL_COMISSIONS[type]:
                self.commission_types[name] = type

    def _init_commission_daily_config(self):
        """把参数声明注册进本任务的 default_config / config_description / config_type。"""
        self.default_config.update(self.sub_task_default_config)
        self.config_description.update(self.sub_task_config_description)
        if not hasattr(self, "config_type") or self.config_type is None:
            self.config_type = {}
        self.config_type.update(self.sub_task_config_type)

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

    @property
    def commission_name(self):
        return self._sub_task_cfg("选择委托")

    @property
    def commission_type(self):
        return self.commission_types[self.commission_name]

    @cached_property
    def scroll_box(self):
        return self.box_of_screen(0.0906, 0.5611, 0.6156, 0.6917)

    def _get_power(self):
        for _ in self.loop(2):
            boxes = self.ocr(box=self.box_of_screen(0.8635, 0.0417, 0.9187, 0.0593))
            if not boxes or not (result := boxes[0].name):
                continue
            if result.count('/') != 1:
                continue
            return int(result.split('/')[0])

    def run_once(self):
        self.ui_ensure(COMMISSION_PAGES[self.commission_type])
        result = self.detect_with_scroll(
            detector=OcrDetector(
                match=self.commission_name,
                box=self.scroll_box,
            ),
            box=self.scroll_box,
            scroll_count=10,
        )
        self.wait_action_result(
            action=lambda: self.click(result),
            expect=OcrDetector(
                match=self.commission_name,
                box=self.box_of_screen(0.7562, 0.2741, 0.9240, 0.3167),
            ),
            max_attempts=5,
        )
        self.wait_action_result(
            action=lambda: self.click(self.box_of_screen(0.8870, 0.7204, 0.9307, 0.7417)),
            expect=TemplateDetector(FeatureList.commission_button_start_commission),
            max_attempts=5,
        )
        self.wait_action_result(
            condition=TemplateDetector(FeatureList.commission_button_start_commission),
            action=lambda hit: self.click(hit),
            expect=TemplateDetector(FeatureList.loading_check),
            max_attempts=5,
        )
        for _ in self.loop(120):
            if self.find_one(FeatureList.auto_combat_setting):
                break
            if self.find_one(FeatureList.auto_combat_check):
                self.send_key('f1')
                self.sleep(self.once_sleep_time)
        for _ in self.loop(480):
            if self.find_one(FeatureList.auto_combat_setting):
                self.sleep(self.once_sleep_time)
                continue
            if self.find_one(FeatureList.bond_levelup_popup):
                self.click(self.box_of_screen(0.4568, 0.8324, 0.5448, 0.8824))
                self.sleep(0.1)
                continue
            if box := self.find_one(FeatureList.combat_button_return):
                self.click(box)
                self.sleep(0.1)
                continue
            if any(self.ui_page_appear(page) for page in COMMISSION_PAGES.values()):
                break

    def run(self):
        self.ui_ensure(COMMISSION_PAGES[self.commission_type])
        while self._get_power() >= COMMISSION_COSTS[self.commission_type]:
            self.run_once()
        self.ui_ensure(page_main)
