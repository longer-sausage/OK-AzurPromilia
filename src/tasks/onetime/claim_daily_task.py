from __future__ import annotations

import logging

from ok import Box

from src.core.account_override_mixin import AccountOverrideMixin
from src.core.base_game_task import BaseGameTask
from src.data.feature_list import FeatureList
from src.data.page import (
    page_active_daily,
    page_active_weekly,
    page_big_month_card,
    page_mail,
    page_main,
    page_shop,
)
from src.icons import Icons
from src.image.button_detector import ButtonThresholds
from src.image.frame_processes import isolate_by_hsv_ranges, make_hsv_isolator
from src.image.hsv_config import HSVRange
from src.image.stability import hamming_distance, perceptual_hash

# 红色感叹号 HSV 范围（提取感叹号红色底色部分，跨 0° 两端）
EXCLAMATION_RED_HSV = (
    ((0, 60, 100), (10, 255, 255)),
    ((170, 60, 100), (180, 255, 255)),
)

SHOP_TAB_BAR = (0.332, 0.113, 0.995, 0.162)
# 限时珍藏首格和商品详情中的领取按钮。取按钮内侧，避开付费按钮的金边和卡片底边。
SHOP_DAILY_CARD_BOX = (0.515, 0.343, 0.608, 0.375)
SHOP_DAILY_CONFIRM_BOX = (0.524, 0.606, 0.718, 0.647)
SHOP_FREE_CLAIM_BUTTON = ButtonThresholds.for_button(
    text_hsv=((8, 65, 35), (36, 255, 155)),
    backdrop_hsv=((18, 60, 195), (35, 255, 255)),
    name="shop_free_claim",
    center_x_range=(0.12, 0.88),  # 日文「無料受け取り」比中文更宽
    min_band_aspect=3.0,
    min_backdrop_ratio=0.80,
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

    def claim_mail(self):
        self.ui_ensure(page_mail)

        end_flag = False
        for _ in self.loop(time_out=5, raise_if_time_out=False):
            if box := self.find_one(FeatureList.mail_button_claim_all, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                if end_flag:
                    break
                self.click(box)
                self.sleep(1)
                continue
            if box := self.find_one(FeatureList.confirm_button_3):
                end_flag = True
                self.click(box)
                self.sleep(0.1)
                continue
            if self.handle_claim_popup():
                end_flag = True
                self.sleep(0.1)
                continue
        if not end_flag:
            logging.getLogger(__name__).warning("邮件领取未确认完成，继续执行后续任务")

        if self._sub_task_cfg("删除已读邮件"):
            end_flag = False
            for _ in self.loop():
                if self.find_one(FeatureList.mail_button_claim_all, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                    if end_flag:
                        break
                    self.click(self.box_of_screen(0.2401, 0.7546, 0.2484, 0.7750))
                    self.sleep(0.1)
                    continue
                if box := self.find_one(
                    FeatureList.confirm_button_3,
                    box=self.box_of_screen(0.55, 0.58, 0.65, 0.66),
                ):
                    end_flag = True
                    self.click(box)
                    self.sleep(0.1)
                    continue

    def _find_shop_limited_tab(self, frame, bar: Box) -> tuple[Box | None, bool]:
        tab = self.find_one(
            FeatureList.shop_limited_tab_icon, box=bar, frame=frame, use_gray_scale=True, threshold=0.73,
        )
        if not tab:
            return None, False
        # 国服和日服的标签文字宽度不同，选中菱形都在宝箱图标的右下方。
        below_label = Box(tab.x, tab.y + tab.height, 3 * tab.width, tab.height)
        selected = self.find_one(FeatureList.shop_click_2_check, box=below_label, frame=frame, threshold=0.80)
        return tab, bool(selected)

    def _select_shop_limited_tab(self, bar: Box) -> bool:
        for scroll_count in (-5, 5):
            last_scroll_hash = None
            last_clicked_x = None
            for frame in self.loop(time_out=10, raise_if_time_out=False):
                if not self.find_one(FeatureList.shop_check, frame=frame):
                    continue
                if not self.find_one(FeatureList.shop_click_1_check, frame=frame, horizontal_variance=0.01):
                    self.click(self.box_of_screen(0.3625, 0.9435, 0.3802, 0.9685))
                    self.sleep(0.3)
                    continue
                tab, selected = self._find_shop_limited_tab(frame, bar)
                if selected:
                    return True
                if tab and (last_clicked_x is None or abs(tab.x - last_clicked_x) > tab.width // 2):
                    if self.click(tab):
                        last_clicked_x = tab.x
                    self.sleep(0.4)
                    continue

                # 在栏内滚动，抵达一端后换向；图标或选中标记被边缘裁切时也能继续寻找。
                strip_hash = perceptual_hash(bar.crop_frame(frame))
                if last_scroll_hash is not None and hamming_distance(strip_hash, last_scroll_hash) <= 1:
                    break
                last_scroll_hash = strip_hash
                self.scroll(*bar.center(), scroll_count)
                self.sleep(0.3)
        return False

    def claim_shop(self):
        if not self.ui_ensure(page_shop):
            return
        bar = self.box_of_screen(*SHOP_TAB_BAR)
        if not self._select_shop_limited_tab(bar):
            return

        self.sleep(0.5)
        card_box = self.box_of_screen(*SHOP_DAILY_CARD_BOX)
        for frame in self.loop(time_out=2, raise_if_time_out=False):
            _, selected = self._find_shop_limited_tab(frame, bar)
            if not selected:
                continue
            button = self.find_button(card_box, frame=frame, thresholds=SHOP_FREE_CLAIM_BUTTON)
            if button and self.click(button):
                break
        else:
            return  # 已领或只有付费商品

        confirm_box = self.box_of_screen(*SHOP_DAILY_CONFIRM_BOX)
        for frame in self.loop(time_out=5, raise_if_time_out=False):
            if self.handle_claim_popup():
                break
            if self.find_one(FeatureList.shop_check, frame=frame):
                continue
            button = self.find_button(confirm_box, frame=frame, thresholds=SHOP_FREE_CLAIM_BUTTON)
            if button and self.click(button):
                break
        else:
            if self.ui_page_appear(page_shop):
                return
            # 商品详情未出现可领按钮时关闭弹窗。
            self.send_key("esc")

        shop_frames = 0
        for frame in self.loop():
            if self.handle_claim_popup():
                shop_frames = 0
                self.sleep(0.1)
            elif self.find_one(FeatureList.shop_check, frame=frame):
                shop_frames += 1
                if shop_frames >= 5:
                    return
            else:
                shop_frames = 0

    def claim_active(self):
        self.ui_ensure(page_active_daily)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(
                FeatureList.active_daily_claim_task,
                box=self.box_of_screen(0.89, 0.14, 0.92, 0.83),
            ):
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
            if self.handle_claim_popup():
                frame_count = 0
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
            if box := self.find_one(
                FeatureList.active_weekly_claim_task,
                box=self.box_of_screen(0.89, 0.14, 0.92, 0.83),
            ):
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
            if self.handle_claim_popup():
                frame_count = 0
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
            if self.find_one(
                FeatureList.big_month_card_task_check,
                box=self.box_of_screen(0.5437, 0.9519, 0.5792, 0.9898),
            ):
                break
            self.click(self.box_of_screen(0.6089, 0.9611, 0.6219, 0.9815))
            self.sleep(0.1)
        stable_time = self.active_time()
        for _ in self.loop():
            # 领取按钮与解锁入口的装饰相似，需同时确认领取红点。
            box = self.find_one(
                FeatureList.big_month_card_button_claim_all,
                box=self.box_of_screen(0.85, 0.75, 0.99, 0.83),
            )
            if box and self.find_one(
                FeatureList.active_daily_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
                box=self.box_of_screen(0.94, 0.75, 0.99, 0.80),
            ):
                self.click(box)
                self.sleep(0.1)
                stable_time = self.active_time()
                continue
            if self.find_one(FeatureList.big_month_card_levelup_popup):
                self.click(self.safe_box)
                self.sleep(0.1)
                stable_time = self.active_time()
                continue
            if self.active_time() - stable_time >= 1 and self.find_one(
                FeatureList.big_month_card_task_check,
                box=self.box_of_screen(0.5437, 0.9519, 0.5792, 0.9898),
            ):
                break

        for _ in self.loop():
            if self.find_one(
                FeatureList.big_month_card_reward_check,
                box=self.box_of_screen(0.3776, 0.9528, 0.4078, 0.9907),
            ):
                break
            self.click(self.box_of_screen(0.4375, 0.9611, 0.4500, 0.9815))
            self.sleep(0.1)
        stable_time = self.active_time()
        for _ in self.loop():
            # 奖励页同样用红点区分领取按钮和解锁入口。
            box = self.find_one(
                FeatureList.big_month_card_button_claim_all,
                box=self.box_of_screen(0.85, 0.75, 0.99, 0.83),
            )
            if box and self.find_one(
                FeatureList.active_daily_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
                box=self.box_of_screen(0.94, 0.75, 0.99, 0.80),
            ):
                self.click(box)
                self.sleep(0.1)
                stable_time = self.active_time()
                continue
            if self.handle_claim_popup():
                self.sleep(0.1)
                stable_time = self.active_time()
                continue
            if self.active_time() - stable_time >= 1 and self.find_one(
                FeatureList.big_month_card_reward_check,
                box=self.box_of_screen(0.3776, 0.9528, 0.4078, 0.9907),
            ):
                break

    def run(self):
        self.claim_mail()
        self.claim_shop()
        self.claim_active()
        self.claim_big_month_card()
        self.ui_ensure(page_main)
