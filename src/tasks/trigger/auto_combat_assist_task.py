"""实时辅助：面向「需要一直盯着」的战斗场景，按设置持续操作。

与其它触发任务的差别：**不针对某个玩法**，只做「按你设置的键、按你设置的节奏」，
所以不依赖任何游戏内界面模板就能用。可选的「前置模板」用来把它限定在特定场合
（例如只在战斗 HUD 出现时按键），没有就不用。

配置项：

- `按键序列`：逗号分隔，例如 ``q,e,r``；按顺序轮转，不是一次全按；
- `动作间隔(秒)`：两次动作之间的最小间隔（冷却），默认 0.8；
- `前置模板`：可选，填模板名（如 ``auto_combat_check``）；不填表示不禁用；
- `最大连续次数`：单次 run 里最多按几次，默认 3，避免长时间占住触发轮转；
- `记录日志`：每次动作写一行日志。

设计约束：``trigger_interval = 0`` 表示每轮都参与调度，靠自己的冷却限流；
任何异常都让本次 run 直接返回（触发任务不该把框架拖崩）。
"""

from __future__ import annotations

from ok import TriggerTask

from src.core.base_game_task import BaseGameTask
from src.icons import Icons

DEFAULT_SEQUENCE = "q,e,r"
DEFAULT_INTERVAL = 0.8
DEFAULT_MAX_ACTIONS = 3
DEFAULT_PRESS = 0.05


def parse_sequence(raw: str) -> list[str]:
    """把 ``q,e,r`` / ``q e r`` 解析成按键列表（去空、保序、去重相邻重复）。"""
    text = str(raw or "")
    for separator in ("，", ";", "；", "|", " "):
        text = text.replace(separator, ",")
    keys: list[str] = []
    for part in text.split(","):
        key = part.strip()
        if not key:
            continue
        if keys and keys[-1] == key:
            continue
        keys.append(key)
    return keys


class AutoCombatAssistTask(BaseGameTask, TriggerTask):
    """按序列 + 冷却持续按键的通用实时辅助。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "实时辅助"
        self.icon = Icons.Trigger
        self.description = (
            "按你设置的按键序列与节奏持续操作，适合需要一直盯着的战斗场景。"
            "可选用前置模板限定生效场合。"
        )
        self.trigger_interval = 0
        self.default_config = {
            "按键序列": DEFAULT_SEQUENCE,
            "动作间隔(秒)": DEFAULT_INTERVAL,
            "前置模板": "",
            "最大连续次数": DEFAULT_MAX_ACTIONS,
            "记录日志": False,
            "_按下时长(秒)": DEFAULT_PRESS,
        }
        self.config_description = {
            "按键序列": "逗号分隔，按顺序轮转，例如 q,e,r",
            "动作间隔(秒)": "两次动作之间的最小间隔，避免连点",
            "前置模板": "可选：只有该模板出现时才动作（填模板名，如 auto_combat_check）",
            "最大连续次数": "单次 run 最多按几次，避免长时间占住触发轮转",
            "记录日志": "每次动作写一行日志，方便确认节奏",
            "_按下时长(秒)": "按键按住时长",
        }
        self._next_index = 0
        self._last_action_at: float | None = None

    # ── 依赖（测试可替换） ───────────────────────────────
    def _now(self) -> float:
        try:
            return float(self.active_time())
        except Exception:
            return 0.0

    def _gate_open(self) -> bool:
        feature = str(self.config.get("前置模板", "") or "").strip()
        if not feature:
            return True
        try:
            return bool(self.find_one(feature=feature))
        except Exception:
            return False

    def _press(self, key: str) -> None:
        duration = float(self.config.get("_按下时长(秒)", DEFAULT_PRESS) or DEFAULT_PRESS)
        self.press_key(key, down_time=duration)

    # ── 主流程 ───────────────────────────────────────────
    def run(self):
        keys = parse_sequence(self.config.get("按键序列", DEFAULT_SEQUENCE))
        if not keys:
            return False
        if not self._gate_open():
            return False

        try:
            interval = max(0.0, float(self.config.get("动作间隔(秒)", DEFAULT_INTERVAL) or 0.0))
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL

        now = self._now()
        if self._last_action_at is not None and now - self._last_action_at < interval:
            return False

        try:
            limit = max(1, int(self.config.get("最大连续次数", DEFAULT_MAX_ACTIONS) or 1))
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_ACTIONS

        pressed = 0
        for _ in range(limit):
            key = keys[self._next_index % len(keys)]
            self._next_index = (self._next_index + 1) % len(keys)
            self._press(key)
            pressed += 1
            if self.config.get("记录日志", False):
                self.log_info(f"实时辅助：按下 {key}")

        self._last_action_at = self._now()
        return pressed > 0

    # ── 状态 ─────────────────────────────────────────────
    def reset_state(self) -> None:
        """停用再启用时从头开始（避免接着上次的轮转位置按）。"""
        self._next_index = 0
        self._last_action_at = None
