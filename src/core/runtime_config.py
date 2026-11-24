"""运行期策略配置：超时、重试、卡死检测与失败自恢复。

这些参数需要让用户自己调（低配机器要放宽超时、慢机器要加大卡死阈值），
所以统一挂在 ``global_config_store`` 的全局配置里，而不是散落在各任务的调用点。

约定：

- 默认值必须与改动前的行为**完全一致**（单步超时 10 秒、重试间隔 0 秒、
  动作后等待 0 秒），这样存量用户升级后不会出现时序变化。
- ``低配模式`` 只放宽超时倍数（1.5 倍），不改变任何判定逻辑，
  避免「低配机器因为慢而过不了同一个门槛」。
- 所有取值都做了类型兜底：用户在 JSON 里手改成字符串也不会让任务崩。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from ok import ConfigOption
from qfluentwidgets import FluentIcon

RUNTIME_CONFIG_NAME = "Automation Runtime Config"

STEP_TIMEOUT_KEY = "单步超时(秒)"
RETRY_INTERVAL_KEY = "重试间隔(秒)"
AFTER_ACTION_WAIT_KEY = "动作后等待(秒)"
STUCK_THRESHOLD_KEY = "卡死检测阈值(秒)"
LOW_SPEC_KEY = "低配模式"
AUTO_RECOVER_KEY = "失败后自动返回主菜单"
MAX_RECOVER_KEY = "最大返回次数"
FAILURE_FRAME_LIMIT_KEY = "失败帧保留数量"

DEFAULT_STEP_TIMEOUT = 10.0
DEFAULT_RETRY_INTERVAL = 0.0
DEFAULT_AFTER_ACTION_WAIT = 0.0
DEFAULT_STUCK_THRESHOLD = 30.0
DEFAULT_MAX_RECOVER_ATTEMPTS = 3
DEFAULT_FAILURE_FRAME_LIMIT = 20

LOW_SPEC_TIMEOUT_SCALE = 1.5

# 卡死检测的最小轮询间隔：sleep_check 会被框架在每次 sleep 里调用，
# 图像指纹本身不贵，但没必要比这更频繁。
STUCK_CHECK_INTERVAL = 1.0


def _as_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result:  # NaN
        return default
    return result


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on", "是", "开"):
            return True
        if text in ("0", "false", "no", "off", "否", "关"):
            return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RuntimeSettings:
    """一次读取得到的运行期策略快照。"""

    step_timeout: float = DEFAULT_STEP_TIMEOUT
    retry_interval: float = DEFAULT_RETRY_INTERVAL
    after_action_wait: float = DEFAULT_AFTER_ACTION_WAIT
    stuck_threshold: float = DEFAULT_STUCK_THRESHOLD
    low_spec: bool = False
    auto_recover: bool = True
    max_recover_attempts: int = DEFAULT_MAX_RECOVER_ATTEMPTS
    failure_frame_limit: int = DEFAULT_FAILURE_FRAME_LIMIT

    @property
    def timeout_scale(self) -> float:
        """低配模式放宽超时的倍数。"""
        return LOW_SPEC_TIMEOUT_SCALE if self.low_spec else 1.0

    def scaled_timeout(self, time_out: float) -> float:
        """按低配模式缩放一个超时值（0 与负数表示「不限时」，原样返回）。"""
        if time_out <= 0:
            return time_out
        return time_out * self.timeout_scale

    def resolve_step_timeout(self, time_out: float | None = None) -> float:
        """把 ``loop(time_out=None)`` 解析成实际秒数。"""
        base = self.step_timeout if time_out is None else time_out
        return self.scaled_timeout(base)

    def resolve_retry_gap(self, check_delay: float = 0.0) -> float:
        """条件没满足时，两次重试之间等多久。

        ``wait_until_check_delay``（框架死键，由本项目实现）显式配了就优先用它；
        否则用 ``重试间隔(秒)``；两者都为 0 就是「不额外等待」，与旧行为一致。
        """
        try:
            explicit = max(0.0, float(check_delay or 0.0))
        except (TypeError, ValueError):
            explicit = 0.0
        return max(explicit, max(0.0, float(self.retry_interval or 0.0)))

    def resolve_stuck_threshold(self) -> float:
        """卡死阈值同样按低配模式放宽，避免慢机器误报。"""
        return self.scaled_timeout(self.stuck_threshold)


def build_default_config() -> dict:
    """全局配置的默认值（也是 UI 上显示的那份）。"""
    return {
        STEP_TIMEOUT_KEY: DEFAULT_STEP_TIMEOUT,
        RETRY_INTERVAL_KEY: DEFAULT_RETRY_INTERVAL,
        AFTER_ACTION_WAIT_KEY: DEFAULT_AFTER_ACTION_WAIT,
        STUCK_THRESHOLD_KEY: DEFAULT_STUCK_THRESHOLD,
        LOW_SPEC_KEY: False,
        AUTO_RECOVER_KEY: True,
        MAX_RECOVER_KEY: DEFAULT_MAX_RECOVER_ATTEMPTS,
        FAILURE_FRAME_LIMIT_KEY: DEFAULT_FAILURE_FRAME_LIMIT,
    }


CONFIG_DESCRIPTION = {
    STEP_TIMEOUT_KEY: "单个等待/轮询步骤的默认超时；任务里显式传超时的位置不受影响",
    RETRY_INTERVAL_KEY: "条件没满足时两次重试之间的间隔（与 wait_until_check_delay 取较大值），0 表示不额外等待",
    AFTER_ACTION_WAIT_KEY: "失败自恢复时每次按返回键后的等待；0 表示用框架的默认间隔",
    STUCK_THRESHOLD_KEY: "画面长时间不变（判定为可能卡死）的阈值，只记录警告不中止任务",
    LOW_SPEC_KEY: "低配机器：所有超时统一放宽 1.5 倍，卡死阈值同步放宽",
    AUTO_RECOVER_KEY: "任务失败后自动按返回键退回主菜单，只退菜单不重跑任务",
    MAX_RECOVER_KEY: "失败后最多按几次返回键",
    FAILURE_FRAME_LIMIT_KEY: "失败帧库最多保留多少帧，超出后丢弃最旧的",
}

runtime_config_option = ConfigOption(
    RUNTIME_CONFIG_NAME,
    build_default_config(),
    description="超时、重试与失败自恢复策略",
    config_description=CONFIG_DESCRIPTION,
    icon=FluentIcon.SPEED_HIGH,
)


def load_runtime_settings(config: Mapping[str, Any] | None) -> RuntimeSettings:
    """从配置字典读出策略快照；缺键或类型不对都退回默认值。"""
    if config is None:
        return RuntimeSettings()

    step_timeout = _as_float(config.get(STEP_TIMEOUT_KEY), DEFAULT_STEP_TIMEOUT)
    if step_timeout <= 0:
        step_timeout = DEFAULT_STEP_TIMEOUT

    retry_interval = _as_float(config.get(RETRY_INTERVAL_KEY), DEFAULT_RETRY_INTERVAL)
    after_action_wait = _as_float(config.get(AFTER_ACTION_WAIT_KEY), DEFAULT_AFTER_ACTION_WAIT)
    stuck_threshold = _as_float(config.get(STUCK_THRESHOLD_KEY), DEFAULT_STUCK_THRESHOLD)
    if stuck_threshold <= 0:
        stuck_threshold = DEFAULT_STUCK_THRESHOLD

    max_recover = _as_int(config.get(MAX_RECOVER_KEY), DEFAULT_MAX_RECOVER_ATTEMPTS)
    failure_limit = _as_int(config.get(FAILURE_FRAME_LIMIT_KEY), DEFAULT_FAILURE_FRAME_LIMIT)

    return RuntimeSettings(
        step_timeout=step_timeout,
        retry_interval=max(0.0, retry_interval),
        after_action_wait=max(0.0, after_action_wait),
        stuck_threshold=stuck_threshold,
        low_spec=_as_bool(config.get(LOW_SPEC_KEY), False),
        auto_recover=_as_bool(config.get(AUTO_RECOVER_KEY), True),
        max_recover_attempts=max(0, max_recover),
        failure_frame_limit=max(1, failure_limit),
    )


def with_override(settings: RuntimeSettings, **overrides) -> RuntimeSettings:
    """测试与调用点临时覆盖若干字段，不影响全局配置。"""
    return replace(settings, **overrides)
