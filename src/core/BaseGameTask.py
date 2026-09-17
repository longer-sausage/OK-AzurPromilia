import time
from datetime import datetime

# 覆写框架截图时间戳格式：日期_时分秒（无毫秒）
import ok.gui.debug.Screenshot as _ok_screenshot
from ok import BaseTask, TaskDisabledException, TriggerTask, WaitFailedException

from src.core.base_mixin.framework_override_mixin import FrameworkOverrideMixin
from src.core.base_mixin.runtime_mixin import RuntimeMixin
from src.core.config_migration import migrate_config_file_keys, migrate_config_values
from src.config import config as app_config
from src.core.game_window import find_game_hwnd
from src.core.global_config_store import get_global_config
from src.data.FeatureList import FeatureList
from src.data.lang import get_lang_accessor
from src.interaction.KeyConfig import KeyConfigManager
from src.interaction.ScreenPosition import ScreenPosition

_ok_screenshot.get_current_time_formatted = lambda: datetime.now().strftime("%Y%m%d_%H%M%S")


def _round_ratio(value):
    try:
        return round(float(value), 3)
    except Exception:
        return value


class BaseGameTask(RuntimeMixin, FrameworkOverrideMixin, BaseTask):
    """游戏自动化任务基类，提供通用的交互和识别功能。

    新项目从本类派生一次性任务；触发式任务继承
    ``BaseGameTask, TriggerTask``。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_user = ""  # 记录当前用户
        self.support_multi_account = False  # 是否支持多账号执行逻辑
        self.default_config_group = {}  # 配置项分组信息，格式为 { "分组名称": ["配置项1", "配置项2"] }
        self.box = ScreenPosition(self)  # 屏幕位置辅助对象，提供top/bottom/left/right等边界
        self.key_manager = KeyConfigManager(get_global_config("Game Hotkey Config"))
        self.once_sleep_time = get_global_config("Ensure Main Once Action Sleep").get(
            "SingleActionWithDelay", 1.5
        )  # 获取全局配置的单次动作睡眠时间

        # 语言访问器（按模块化 JSON 加载）
        try:
            self.lang = get_lang_accessor(self)
        except Exception:
            self.lang = get_lang_accessor(None)

        self._task_pause_started_at = None
        self._active_time_paused_total = 0.0
        self._seen_executor_pause_start = getattr(self.executor, "pause_start", None)

    # ── 暂停感知的活动计时 ─────────────────────────────
    def active_time(self) -> float:
        """Return monotonic task time with framework pauses excluded."""
        now = time.monotonic()
        executor = self.executor
        executor_pause_start = getattr(executor, "pause_start", None)
        current_executor_pause = 0.0

        if executor_pause_start != self._seen_executor_pause_start:
            pause_duration = max(0.0, time.time() - executor_pause_start)
            if executor.paused:
                current_executor_pause = pause_duration
            else:
                self._active_time_paused_total += pause_duration
                self._seen_executor_pause_start = executor_pause_start

        current_task_pause = 0.0
        if self._task_pause_started_at is not None:
            current_task_pause = max(0.0, now - self._task_pause_started_at)

        return now - self._active_time_paused_total - current_executor_pause - current_task_pause

    def sleep(self, timeout):
        """Sleep for active task time, keeping the deadline across pauses."""
        if timeout <= 0:
            return True

        deadline = self.active_time() + timeout
        while True:
            remaining = deadline - self.active_time()
            if remaining <= 0:
                return True
            super().sleep(min(remaining, 0.1))

    def pause(self):
        if not isinstance(self, TriggerTask) and self._task_pause_started_at is None:
            self._task_pause_started_at = time.monotonic()
        return super().pause()

    def unpause(self):
        if self._task_pause_started_at is not None:
            self._active_time_paused_total += max(0.0, time.monotonic() - self._task_pause_started_at)
            self._task_pause_started_at = None
        return super().unpause()

    def wait_until(self, condition, time_out=0, pre_action=None, post_action=None, settle_time=-1,
                   raise_if_not_found=False):
        """Framework wait_until variant whose timeout freezes while paused."""
        self.executor.reset_scene()
        start = self.active_time()
        if time_out == 0:
            time_out = self.executor.wait_scene_timeout
        settled = None

        while not self.executor.exit_event.is_set():
            if pre_action is not None:
                pre_action()
            self.next_frame()
            result = condition()
            if result:
                if settle_time == -1:
                    settle_time = self.executor.wait_until_settle_time
                if settle_time <= 0:
                    return result
                now = self.active_time()
                if settled is None:
                    settled = now
                elif now - settled > settle_time:
                    return result
                continue

            settled = None
            if post_action is not None:
                post_action()
            if self.active_time() - start > time_out:
                break

        if raise_if_not_found:
            raise WaitFailedException()
        return None

    # ── 坐标 / 点击辅助（把比例坐标四舍五入到 3 位小数） ──
    def box_of_screen(self, x=0, y=0, to_x=1.0, to_y=1.0, width=0.0, height=0.0,
                      name=None, hcenter=False, vcenter=False, confidence=1.0):
        return super().box_of_screen(
            _round_ratio(x), _round_ratio(y), _round_ratio(to_x), _round_ratio(to_y),
            width=width, height=height, name=name, hcenter=hcenter, vcenter=vcenter,
            confidence=confidence,
        )

    def box_of_screen_scaled(
        self,
        original_screen_width,
        original_screen_height,
        x_original,
        y_original,
        to_x=0,
        to_y=0,
        width_original=0,
        height_original=0,
        name=None,
        hcenter=False,
        vcenter=False,
        confidence=1.0,
    ):
        """Create a screen box scaled from original resolution coordinates with rounded ratios."""
        return super().box_of_screen_scaled(
            original_screen_width,
            original_screen_height,
            _round_ratio(x_original),
            _round_ratio(y_original),
            _round_ratio(to_x),
            _round_ratio(to_y),
            width_original=width_original,
            height_original=height_original,
            name=name,
            hcenter=hcenter,
            vcenter=vcenter,
            confidence=confidence,
        )

    def click_relative(self, x, y, *args, **kwargs):
        return super().click_relative(_round_ratio(x), _round_ratio(y), *args, **kwargs)

    def middle_click_relative(self, x, y, *args, **kwargs):
        """Middle-click at relative screen coordinates with rounded ratios."""
        return super().middle_click_relative(_round_ratio(x), _round_ratio(y), *args, **kwargs)

    @property
    def runtime_locale(self) -> str | None:
        """统一获取运行时 UI 语言。"""
        executor = getattr(self, "executor", None)
        locale_obj = getattr(executor, "locale", None)
        if locale_obj is None:
            return None
        if hasattr(locale_obj, "name"):
            try:
                name_attr = getattr(locale_obj, "name")
                value = name_attr() if callable(name_attr) else name_attr
                if value:
                    return str(value)
            except Exception:
                pass
        return str(locale_obj)

    # ── 配置键迁移 ──────────────────────────────────────
    def load_config(self):
        """走 MRO 收集各 mixin/任务的迁移表，执行键名复制与值转换迁移，再加载配置。

        先做纯键名复制（config_key_migrations），再做值转换（config_value_migrations），
        确保旧格式值（如布尔开关）在复制后仍能被正确转换为新格式（如列表）。
        """
        key_migrations = {}
        value_migrations = {}
        for klass in type(self).__mro__:
            table = getattr(klass, 'config_key_migrations', None)
            if table:
                key_migrations.update(table)
            vtable = getattr(klass, 'config_value_migrations', None)
            if vtable:
                value_migrations.update(vtable)
        migrate_config_file_keys(self.__class__.__name__, key_migrations)
        migrate_config_values(self.__class__.__name__, value_migrations)
        super().load_config()

    def handle_task_exception(self, e: Exception, prefix: str):
        """统一处理任务 run() 中的异常逻辑。

        - 截图（前缀基于日期 + prefix）
        - 根据配置 `发生异常时终止游戏` 决定是继续（记录日志）还是终止（记录并不抛出）
        - 对于 `TaskDisabledException` 总是重新抛出以便上层处理
        """
        try:
            self.screenshot(prefix)
        except Exception:
            pass

        if not self.config.get("发生异常时终止游戏", False):
            self.log_info("发生异常，继续游戏", notify=True)
            raise e
        else:
            if isinstance(e, TaskDisabledException):
                self.log_info("发生异常，继续游戏", notify=True)
                raise e
            else:
                self.log_info("发生异常，终止游戏", notify=True)

    def mark_task_failure(self, message: str, task_name: str | None = None):
        """统一标记任务失败消息，并截图（包含时间和任务名称）。"""
        name = task_name or getattr(self, "current_task", None) or "UnknownTask"
        try:
            self.screenshot(f"fail_{name}")
        except Exception:
            pass
        self.log_info(str(message))

    def ensure_main(self, esc=True, time_out=90, after_sleep=0):
        """
        确保回到主界面（游戏世界）。

        采用两段稳定检查确认主界面：
        1. 第一段（疑似确认）：检测到一帧疑似主界面（Esc 图标）后，再确认一帧，
           过滤加载/返回动画中的单帧闪屏误检；
        2. 第二段（最终确认）：仅在第一段通过后执行，主界面状态须持续 2 秒。

        两段检查共享同一时间预算（time_out），确保总耗时不超过指定超时时间。
        恢复阶段（按 ESC 返回）在总预算内循环执行两段检查：第二段未通过时
        回到第一段继续恢复等待，直到总超时，避免主界面刚出现时图标未稳定
        匹配导致提前失败。

        Args:
            esc: 是否在失败时执行返回键处理。
            time_out: 等待主界面的总超时时间。
            after_sleep: 成功后额外等待时间。

        Returns:
            None

        Raises:
            Exception: 当无法回到主界面时抛出。
        """
        self.check_resolution()
        self.info_set("current task", self.tr("wait main esc={esc}").format(esc=esc))
        start = self.active_time()
        observe_time = min(2.0, time_out)

        def main_suspected(use_esc):
            # 第一段稳定检查：疑似主界面（Esc）连续两帧出现才算通过
            if not self.is_main(esc=use_esc):
                return False
            return self.is_main(esc=use_esc)

        def main_stable(use_esc):
            # 第二段稳定检查（最终确认）：仅在第一段通过后执行，状态须持续 2 秒
            # 使用剩余时间预算，避免超出总 time_out
            remaining = time_out - (self.active_time() - start)
            if remaining <= 0:
                return False
            # 理想稳定时间为 2 秒，但不超过剩余预算
            stable_time_out = min(3.0, remaining)
            stable_settle = min(2.0, remaining)
            return self.wait_until(
                lambda: self.is_main(esc=use_esc),
                time_out=stable_time_out,
                settle_time=stable_settle,
                raise_if_not_found=False,
            )

        # Give loading and return animations a chance to finish before recovery input.
        result = self.wait_until(
            lambda: main_suspected(False),
            time_out=observe_time,
            settle_time=0,
            raise_if_not_found=False,
        )
        if result:
            result = main_stable(False)

        if not result and self.active_time() - start < time_out:
            self._next_main_recovery_time = self.active_time()
            # 恢复阶段：循环两段检查直到总预算耗尽，第二段失败不中断恢复
            while self.active_time() - start < time_out:
                remaining = time_out - (self.active_time() - start)
                if remaining <= 0:
                    break
                result = self.wait_until(
                    lambda: main_suspected(esc),
                    time_out=remaining,
                    settle_time=0,
                    raise_if_not_found=False,
                )
                if not result:
                    break
                # 最终确认只观察不按键，避免返回键干扰刚出现的稳定状态
                result = main_stable(False)
                if result:
                    break
                self.log_info("主界面第二段稳定检查未通过，继续恢复等待")

        if not result:
            raise Exception("Please start in game world and in team!")
        if after_sleep > 0:
            self.sleep(after_sleep)
        self.info_set("current task", self.tr("in main esc={esc}").format(esc=esc))

    def in_world(self):
        """
        判断是否在游戏世界中（非菜单/对话状态）。

        Returns:
            bool: 当前处于游戏世界返回 True。
        """
        main_world_features = [FeatureList.login_out]

        in_world = all(self.find_one(f, vertical_variance=0.01, horizontal_variance=0.02) for f in main_world_features)

        if in_world:
            self._logged_in = True

        return in_world

    def is_main(self, esc=False):
        """
        判断是否处于可执行任务的主界面状态。

        Args:
            esc: 是否在处理失败时按返回键。

        Returns:
            bool: 处于主界面返回 True，否则返回 False。
        """

        self.next_frame()

        # Stability is handled by ensure_main's outer wait.
        if self.in_world():
            self._logged_in = True
            return True

        if result := (
            self.find_one(feature=[FeatureList.confirm_button, FeatureList.confirm_button_2], vertical_variance=0.01, horizontal_variance=0.02)
        ):
            self.log_info("检测到特定弹窗，尝试点击确认")
            self.click(result)
            self._next_main_recovery_time = self.active_time() + self.once_sleep_time
            return False

        if esc and self.active_time() >= getattr(self, "_next_main_recovery_time", 0):
            self.log_info("主界面自然恢复等待结束，发送返回键")
            self.back()
            self._next_main_recovery_time = self.active_time() + self.once_sleep_time

        return False

    def get_game_hwnd(self) -> int:
        """Return the game hwnd resolved from the configured window features."""
        hwnd = find_game_hwnd(app_config.get("windows", {}))
        if hwnd:
            return hwnd
        device_manager = getattr(getattr(self, "executor", None), "device_manager", None)
        hwnd_window = getattr(device_manager, "hwnd_window", None)
        return getattr(hwnd_window, "hwnd", 0)

    def register_config_groups(self, groups: dict, dropdown_name: str = "配置选择"):
        """注册配置分组，支持下拉切换 + 子配置折叠显示"""
        if not hasattr(self, "default_config") or self.default_config is None:
            self.default_config = {}

        if not hasattr(self, "config_type") or self.config_type is None:
            self.config_type = {}

        # 1. 创建下拉选择框
        dropdown_key = dropdown_name
        group_names = list(groups.keys())

        if not group_names:
            print("警告: groups 为空")
            return

        # 注册下拉框配置类型
        self.config_type[dropdown_key] = {
            "type": "drop_down",
            "options": group_names,
            "sub_configs": groups,  # 关键：用于框架实现折叠逻辑
        }

        # 2. 设置默认选中第一个分组
        self.default_config[dropdown_key] = group_names[0]

        # 3. 为所有配置项补充默认值（安全处理）
        for group_items in groups.values():
            for item in group_items:
                if isinstance(item, str):
                    key = item
                else:
                    key = str(item)

                if key not in self.default_config:
                    if hasattr(self, "config") and self.config is not None and key in self.config:
                        self.default_config[key] = self.config[key]
                    else:
                        self.default_config[key] = None

        self.config_description.update({
            dropdown_key: "配置默认隐藏，选择后展开对应配置项。"
        })
    def click_confirm(self, after_sleep=0, time_out=5, recheck_time=0, disappear_time_out=0.8):
        """
        点击对话框中的确认按钮。

        Args:
            after_sleep: 点击后的延迟时间。
            time_out: 总超时时间。
            recheck_time: 点击后重新检测的等待时间。
            disappear_time_out: 等待确认按钮消失的最大时间。

        Returns:
            bool: 找到并点击确认按钮返回 True，超时返回 False。
        """
        start_time = self.active_time()
        while True:
            self.next_frame()
            confirm = self.find_confirm()
            if confirm:
                self.click(confirm)

                if disappear_time_out > 0:
                    self.wait_until(
                        lambda: not self.find_confirm(),
                        time_out=disappear_time_out,
                        raise_if_not_found=False,
                    )
                if after_sleep > 0:
                    self.sleep(after_sleep)

                if recheck_time > 0:
                    self.sleep(recheck_time)

                    if confirm := self.find_confirm():
                        self.click(confirm)
                        if disappear_time_out > 0:
                            self.wait_until(
                                lambda: not self.find_confirm(),
                                time_out=disappear_time_out,
                                raise_if_not_found=False,
                            )
                        if after_sleep > 0:
                            self.sleep(after_sleep)

                return True
            # 超时检测
            if self.active_time() - start_time > time_out:
                self.log_info("点击确认超时")
                return False

            self.sleep(0.01)
    def find_confirm(self):
        """查找对话框中的确认按钮，返回匹配的特征或 None。"""
        return self.find_one(
            feature=[FeatureList.skip_confirm, FeatureList.confirm_button, FeatureList.confirm_button_2],
            vertical_variance=0.01,
            horizontal_variance=0.02
        ) or self.find_one(
            feature=[FeatureList.confirm_button_2],
            box=self.box_of_screen(0.5753,0.6116,0.5957,0.6420)
        )
    