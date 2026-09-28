from __future__ import annotations

from functools import cached_property

from src.core.account_override_mixin import AccountOverrideMixin
from src.core.base_game_task import BaseGameTask
from src.data.page import (
    page_main,
    page_mail,
    page_shop,
    page_active_daily,
    page_active_weekly,
    page_big_month_card,
)
from src.data.feature_list import FeatureList
from src.icons import Icons
from src.image.hsv_config import HSVRange
from src.image.frame_processes import isolate_by_hsv_ranges, make_hsv_isolator


# 红色感叹号 HSV 范围（提取感叹号红色底色部分，跨 0° 两端）
EXCLAMATION_RED_HSV = (
    ((0, 60, 100), (10, 255, 255)),
    ((170, 60, 100), (180, 255, 255)),
)


def active_exclamation_mark_mask(frame):
    return isolate_by_hsv_ranges(frame, EXCLAMATION_RED_HSV, invert=False, kernel_size=2)


class ClaimDailyTask(BaseGameTask):
    """每日收菜任务：邮件 → 惊喜盒子 → 日常/周常活跃 → 大月卡 → 回主界面。

    独立运行；也可由 ``DailyFeature`` 包装后接入一键日常（日常不继承本类）。
    """

    # ── 子任务参数（供 DailyFeature 接入日常时复用） ──
    # 账号覆盖存储挂的任务名；参数配置文件即 configs/<sub_task_config_name>.json，
    # 由框架加载进本实例的 self.config。
    sub_task_config_name = "ClaimDailyTask"
    # 参数声明：{配置键: 默认值}。声明后：
    #   1. 本任务面板可编辑（_init_claim_daily_config 注册）
    #   2. 被日常执行时经 _sub_task_cfg 读取（多账号覆盖 → 自身配置 → 默认值）
    #   3. 「账号配置」页可按账号覆盖（account_config_tab 收集声明了参数的子任务）
    sub_task_default_config: dict = {
        "删除已读邮件": True,
    }
    # 与 sub_task_default_config 同键的说明文案
    sub_task_config_description: dict = {}
    # 运行时由 DailyFeature 注入：宿主的账号覆盖查询 fn(config_name) -> dict。
    # 独立运行时为 None，参数直接读自身配置。
    _account_override_provider = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "每日收菜"
        self.icon = Icons.Task
        self.description = "按顺序领取邮件、每日惊喜盒子、日常活跃、周常活跃、大月卡任务、大月卡奖励。"
        # 注册子任务参数声明
        self._init_claim_daily_config()

    def _init_claim_daily_config(self):
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

    @cached_property
    def safe_box(self):
        return self.box_of_screen(0.4786, 0.8722, 0.5245, 0.9120)

    def claim_mail(self):
        self.ui_ensure(page_mail)

        end_flag = False
        for _ in self.loop():
            if box := self.find_one(FeatureList.mail_button_claim_all, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                if end_flag:
                    break
                self.click(box)
                self.sleep(0.1)
                continue
            if box := self.find_one(FeatureList.confirm_button_3):
                end_flag = True
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                end_flag = True
                self.click(self.safe_box)
                self.sleep(0.1)
                continue

        if self._sub_task_cfg("删除已读邮件"):
            end_flag = False
            for _ in self.loop():
                if self.find_one(FeatureList.mail_button_claim_all, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                    if end_flag:
                        break
                    self.click(self.box_of_screen(0.2401, 0.7546, 0.2484, 0.7750))
                    self.sleep(0.1)
                    continue
                if box := self.find_one(FeatureList.confirm_button_3):
                    end_flag = True
                    self.click(box)
                    self.sleep(0.1)
                    continue

    def claim_shop(self):
        self.ui_ensure(page_shop)

        for _ in self.loop():
            if not self.find_one(FeatureList.shop_click_1_check):
                self.click(self.box_of_screen(0.3625, 0.9435, 0.3802, 0.9685))
                self.sleep(0.1)
                continue
            if not self.find_one(FeatureList.shop_click_2_check):
                self.click(self.box_of_screen(0.8099, 0.1315, 0.8271, 0.1509))
                self.sleep(0.1)
                continue
            break

        self.sleep(0.5)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(FeatureList.shop_button_claim_daily):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if box := self.find_one(FeatureList.shop_button_claim_daily_confirm):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                frame_count = 0
                self.click(self.safe_box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.shop_check):
                frame_count += 1
                if frame_count >= 5:
                    break
            else:
                frame_count = 0

    def claim_active(self):
        self.ui_ensure(page_active_daily)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(FeatureList.active_daily_claim_task, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if box := self.find_one(
                FeatureList.active_daily_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
                box=self.box_of_screen(0.4224, 0.8843, 0.8802, 0.9148)
            ):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                frame_count = 0
                self.click(self.safe_box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.active_daily_check) and not self.find_one(
                FeatureList.active_daily_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
            ):
                frame_count += 1
                if frame_count >= 5:
                    break
            else:
                frame_count = 0

        self.ui_ensure(page_active_weekly)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(FeatureList.active_weekly_claim_task, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if box := self.find_one(
                FeatureList.active_weekly_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
                box=self.box_of_screen(0.4224, 0.8843, 0.8802, 0.9148)
            ):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                frame_count = 0
                self.click(self.safe_box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.active_weekly_check) and not self.find_one(
                FeatureList.active_weekly_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
            ):
                frame_count += 1
                if frame_count >= 5:
                    break
            else:
                frame_count = 0

    def claim_big_month_card(self):
        self.ui_ensure(page_big_month_card)

        for _ in self.loop():
            if self.find_one(FeatureList.big_month_card_task_check):
                break
            self.click(self.box_of_screen(0.6089, 0.9611, 0.6219, 0.9815))
            self.sleep(0.1)
        stable_time = self.active_time()
        for _ in self.loop():
            if box := self.find_one(FeatureList.big_month_card_button_claim_all):
                self.click(box)
                self.sleep(0.1)
                stable_time = self.active_time()
                continue
            if self.find_one(FeatureList.big_month_card_levelup_popup):
                self.click(self.safe_box)
                self.sleep(0.1)
                stable_time = self.active_time()
                continue
            if self.active_time() - stable_time >= 1 and self.find_one(FeatureList.big_month_card_task_check):
                break

        for _ in self.loop():
            if self.find_one(FeatureList.big_month_card_reward_check):
                break
            self.click(self.box_of_screen(0.4375, 0.9611, 0.4500, 0.9815))
            self.sleep(0.1)
        stable_time = self.active_time()
        for _ in self.loop():
            if box := self.find_one(FeatureList.big_month_card_button_claim_all):
                self.click(box)
                self.sleep(0.1)
                stable_time = self.active_time()
                continue
            if self.find_one(FeatureList.claim_popup):
                self.click(self.safe_box)
                self.sleep(0.1)
                stable_time = self.active_time()
                continue
            if self.active_time() - stable_time >= 1 and self.find_one(FeatureList.big_month_card_reward_check):
                break

    def run(self):
        self.claim_mail()
        self.claim_shop()
        self.claim_active()
        self.claim_big_month_card()
        self.ui_ensure(page_main)
