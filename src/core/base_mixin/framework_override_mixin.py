"""覆写框架方法的 Mixin：在基类行为上叠加游戏级逻辑。"""

from __future__ import annotations

import cv2

from src.data.feature_list import FeatureList as fL
from src.image.hsv_config import HSVRange as hR
from src.interaction.mouse import run_at_window_pos


class FrameworkOverrideMixin:
    """覆写 BaseTask 方法，叠加分辨率映射、账号后缀等逻辑。"""

    def find_feature(
        self,
        feature_name=None,
        horizontal_variance=0,
        vertical_variance=0,
        threshold=0,
        use_gray_scale=False,
        x=-1, y=-1, to_x=-1, to_y=-1,
        width=-1, height=-1,
        box=None,
        canny_lower=0, canny_higher=0,
        frame_processor=None,
        template=None,
        match_method=cv2.TM_CCOEFF_NORMED,
        screenshot=False,
        mask_function=None,
        frame=None,
        limit=0,
        target_height=0,
        feature=None,
    ):
        """按当前分辨率映射后执行特征识别。"""
        if feature is not None and feature_name is None:
            feature_name = feature
        if not feature_name:
            raise ValueError("必须提供 feature_name 或 feature 参数")
        if isinstance(feature_name, (list, tuple)):
            feature_name = [self.get_feature_by_resolution(name) for name in feature_name]
        else:
            feature_name = self.get_feature_by_resolution(feature_name)
        return super().find_feature(
            feature_name, horizontal_variance, vertical_variance,
            threshold, use_gray_scale, x, y, to_x, to_y,
            width, height, box, canny_lower, canny_higher,
            frame_processor, template, match_method, screenshot,
            mask_function, frame, limit, target_height,
        )

    def find_one(
        self,
        feature_name=None,
        horizontal_variance=0,
        vertical_variance=0,
        threshold=0,
        use_gray_scale=False,
        box=None,
        canny_lower=0, canny_higher=0,
        frame_processor=None,
        template=None,
        mask_function=None,
        frame=None,
        match_method=cv2.TM_CCOEFF_NORMED,
        screenshot=False,
        limit=1,
        target_height=0,
        feature=None,
    ):
        """按当前分辨率映射后执行单个特征识别。"""
        if feature is not None and feature_name is not None:
            raise ValueError("只能提供 feature 或 feature_name 中的一个参数，不能同时提供两者")
        if feature is None and feature_name is None:
            raise ValueError("必须提供 feature 或 feature_name 中的一个参数")
        if feature is not None and feature_name is None:
            feature_name = feature
        return super().find_one(
            feature_name, horizontal_variance, vertical_variance,
            threshold, use_gray_scale, box, canny_lower, canny_higher,
            frame_processor, template, mask_function, frame,
            match_method, screenshot, limit, target_height,
        )

    def click(self, x=-1, *args, **kwargs):
        """覆写 click，支持直接传入 Hit 对象与 None 空保护。"""
        if "box" in kwargs and hasattr(kwargs["box"], "box"):
            kwargs["box"] = kwargs["box"].box
        if x is None:
            if hasattr(self, "logger") and self.logger:
                self.logger.warning("click: target is None, skip click")
            return False
        if hasattr(x, "box"):
            x = x.box
        elif isinstance(x, list):
            x = [b.box if hasattr(b, "box") else b for b in x]
        return super().click(x, *args, **kwargs)

    def click_box(self, box=None, *args, **kwargs):
        """覆写 click_box，支持直接传入 Hit 对象与 None 空保护。"""
        if box is None:
            if hasattr(self, "logger") and self.logger:
                self.logger.warning("click_box: box is None, skip click")
            return False
        if hasattr(box, "box"):
            box = box.box
        elif isinstance(box, list):
            box = [b.box if hasattr(b, "box") else b for b in box]
        return super().click_box(box, *args, **kwargs)

    def scroll(self, x: int, y: int, count: int) -> None:
        """按屏幕绝对像素坐标滚轮。"""
        run_at_window_pos(self.get_game_hwnd(), super().scroll, x, y, 0.5, x, y, count)

    def scroll_relative(self, x: float, y: float, count: int) -> None:
        """按屏幕相对坐标比例滚轮。"""
        run_at_window_pos(
            self.get_game_hwnd(), super().scroll_relative,
            int(x * self.width), int(y * self.height), 0.5, x, y, count,
        )

    def info_set(self, key, value):
        """写入运行时信息，自动追加当前账号后缀。"""
        if self.current_user:
            suffix = self.current_user[-4:] if len(self.current_user) >= 4 else self.current_user
            key = f"{key}({suffix})"
        if value is not None:
            value = str(value).replace("⭐", "")
        return super().info_set(key, value)
