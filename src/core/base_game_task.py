import time
from datetime import datetime

import cv2

# 覆写框架截图时间戳格式：日期_时分秒（无毫秒）
import ok.gui.debug.Screenshot as _ok_screenshot
from ok import BaseTask, TaskDisabledException, TriggerTask, WaitFailedException, CannotFindException

from src.config import config as app_config
from src.core.base_mixin.framework_override_mixin import FrameworkOverrideMixin
from src.core.base_mixin.runtime_mixin import RuntimeMixin
from src.core.base_mixin.ui_mixin import UIMixin
from src.core.config_migration import migrate_config_file_keys, migrate_config_values
from src.core.game_window import find_game_hwnd
from src.core.global_config_store import get_global_config
from src.data.feature_list import FeatureList
from src.data.page import PageNotFoundError, page_main
from src.data.lang import get_lang_accessor
from src.image.hsv_config import HSVRange
from src.image.frame_processes import make_hsv_isolator
from src.image.stability import perceptual_hash, hamming_distance
from src.interaction.key_config import KeyConfigManager
from src.interaction.screen_position import ScreenPosition

_ok_screenshot.get_current_time_formatted = lambda: datetime.now().strftime("%Y%m%d_%H%M%S")


def _round_ratio(value):
    try:
        return round(float(value), 3)
    except Exception:
        return value


class BaseGameTask(RuntimeMixin, UIMixin, FrameworkOverrideMixin, BaseTask):
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
        self._logged_in = False

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

    def _ensure_main(self, esc):
        """单次尝试确保回到主界面。

        若已处于主界面或成功通过拓扑导航到达主界面，置 `_logged_in = True` 并返回 True。
        若处于未识别页面（PageNotFoundError），则依次尝试登录弹窗、确认弹窗或按返回键恢复，并返回 False。
        若在已知页面间拓扑导航超时（WaitFailedException），按预期不予捕获，直接向外抛出。
        """
        try:
            self.ui_ensure(page_main)
            self._logged_in = True
            return True
        except PageNotFoundError:
            if (
                self.handle_login() or
                self.handle_confirm()
            ):
                self.sleep(self.once_sleep_time)
            elif esc:
                self.back()
                self.sleep(self.once_sleep_time)
            return False

    def ensure_main(self, esc=True, time_out=90, after_sleep=0):
        """
        确保回到主界面（游戏世界）。

        采用基于 UI 拓扑图导航与兜底恢复机制回到主界面：
        1. 尝试通过 `ui_ensure(page_main)` 导航至主界面；
        2. 若处于未识别页面（捕获 `PageNotFoundError`），依次尝试处理登录弹窗（`handle_login`）、
           确认弹窗（`handle_confirm`）或发送返回键（`back`）；
        3. 在总超时时间（`time_out`）内循环重试。若在已知页面间拓扑导航超时，
           按设计直接向外抛出 `WaitFailedException`，由调用方处理或中断任务。

        Args:
            esc: 是否在未识别页面且无弹窗时执行返回键处理。
            time_out: 等待主界面的总超时时间（秒）。
            after_sleep: 成功回到主界面后额外等待时间（秒）。

        Returns:
            None

        Raises:
            RuntimeError: 当总超时仍未回到主界面时抛出。
            WaitFailedException: 当已知页面间的拓扑导航超时未到达目标时直接向外抛出。
        """
        self.check_resolution()
        self.info_set("current task", self.tr("wait main esc={esc}").format(esc=esc))
        for _ in self.loop(time_out, yield_frame=False, raise_if_time_out=False):
            if self._ensure_main(esc):
                if after_sleep > 0:
                    self.sleep(after_sleep)
                self.info_set("current task", self.tr("in main esc={esc}").format(esc=esc))
                return
        raise RuntimeError('Failed to ensure main.')

    def handle_login(self):
        """
        处理登录界面的各种弹窗（月卡、签到、奖励等）。
        """
        if not self._logged_in and self.find_one(
            feature=[FeatureList.login_out],
            mask_function=make_hsv_isolator(HSVRange.WHITE),
        ):
            self.click(0.5, 0.5)
            return True
        return False

    def handle_confirm(self):
        """处理游戏内的特定确认弹窗（如断线重连、系统提示等）。"""
        if result := (
            self.find_one(feature=[
                FeatureList.confirm_button,
                FeatureList.confirm_button_2,
                FeatureList.confirm_button_3,
            ], vertical_variance=0.01, horizontal_variance=0.02)
        ):
            self.log_info("检测到特定弹窗，尝试点击确认")
            self.click(result)
            return True
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

    def find_confirm(self):
        """查找对话框中的确认按钮，返回匹配的特征或 None。"""
        frame=self.next_frame()
        return self.find_button(
            frame=frame,
            box=self.box_of_screen(0.6323,0.7046,0.7193,0.7750),
        ) or self.find_one(
            feature=[FeatureList.confirm_button, FeatureList.confirm_button_2],
            vertical_variance=0.01,
            horizontal_variance=0.02,
            frame=frame
        ) or self.find_one(
            feature=[FeatureList.confirm_button_2],
            box=self.box_of_screen(0.5753,0.6116,0.5957,0.6420),
            frame=frame
        )

    def _detect_with_scroll(
        self,
        detector,
        box,
        scroll_count=-10,
        max_scrolls=5,
        delay=0.2,
    ):
        detector = self._resolve_detector(detector)
        frame = self.next_frame()

        if result := detector.detect(frame):
            return result

        scroll_x, scroll_y = box.center()

        last_hash = None
        for _ in range(max_scrolls):
            self.scroll(scroll_x, scroll_y, scroll_count)
            self.sleep(delay)
            frame = self.next_frame()

            if result := detector.detect(frame):
                return result

            current_hash = perceptual_hash(box.crop_frame(frame))
            if last_hash is not None and hamming_distance(last_hash, current_hash) <= 1:
                raise CannotFindException('Cannot find with scroll.')
            last_hash = current_hash

        raise CannotFindException('Cannot find with scroll.')

    def detect_with_scroll(
        self,
        detector,
        box,
        scroll_count=-10,
        max_scrolls=5,
        delay=0.2,
        max_attempts=3,
    ):
        detector = self._resolve_detector(detector)
        for _ in range(max_attempts):
            try:
                return self._detect_with_scroll(detector, box, scroll_count, max_scrolls, delay)
            except CannotFindException:
                scroll_x, scroll_y = box.center()
                self.scroll(scroll_x, scroll_y, -scroll_count * max_scrolls)
                self.sleep(delay)

        raise CannotFindException('Cannot find with scroll.')

    def loop(
        self,
        time_out: float = 10.0,
        yield_frame: bool = True,
        raise_if_time_out: bool | Exception | type[Exception] = True,
    ):
        """在超时时间内循环，每次迭代获取新帧并检查超时。

        基于 self.active_time() 计算活跃时长（自动排除任务暂停时间）。

        Args:
            time_out: 最大循环时间（秒），默认 10 秒。
            yield_frame: 是否在每次迭代时获取并产出下一帧（self.next_frame()）。
                为 False 时产出 None。
            raise_if_time_out: 超时未退出时的行为：
                - True（默认）：抛出 TimeoutError('Loop time out.')；
                - False / None：正常退出循环（不抛出异常）；
                - Exception 实例：直接 raise 该异常实例；
                - Exception 子类：raise 该异常类('Loop time out.')。

        Yields:
            Frame | None: yield_frame 为 True 时返回图像帧，为 False 时返回 None。

        Raises:
            TimeoutError: 当 raise_if_time_out 为 True 且循环超时未中断时抛出。
            Exception: 当 raise_if_time_out 传入自定义异常且循环超时未中断时抛出。
        """
        start_time = self.active_time()
        while self.active_time() - start_time < time_out:
            if yield_frame:
                yield self.next_frame()
            else:
                yield None
        if raise_if_time_out:
            if isinstance(raise_if_time_out, Exception):
                raise raise_if_time_out
            if isinstance(raise_if_time_out, type) and issubclass(raise_if_time_out, Exception):
                raise raise_if_time_out("Loop time out.")
            raise TimeoutError("Loop time out.")