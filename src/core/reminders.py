"""提醒：每日 04:00 重置提醒 + 按分钟循环的体力提醒。

两者都不打断正在跑的任务 —— 只发一条右下角通知，内容说明清楚就够了。
调度逻辑纯函数化，时间由调用方传入，所以可以离线测试（不用真的等到 04:00）。

规则：

- **每日重置**：每天 ``daily_reset_hour``（默认 4）点后的第一次轮询提醒一次；
  同一天不会重复提醒，跨天自动重置；
- **体力提醒**：``stamina_interval_minutes``（默认 0 = 关闭）分钟提醒一次，
  由调度器自己记上次时间，进程重启会重新计时（提醒不是关键业务，不做持久化）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

DAILY_RESET_HOUR = 4
REMINDER_DAILY = "daily_reset"
REMINDER_STAMINA = "stamina"
SETTINGS_FILE = Path("configs") / "reminders.json"


@dataclass(frozen=True)
class ReminderSettings:
    enabled: bool = True
    daily_reset_enabled: bool = True
    daily_reset_hour: int = DAILY_RESET_HOUR
    stamina_enabled: bool = False
    stamina_interval_minutes: int = 0

    def __post_init__(self):
        if not 0 <= int(self.daily_reset_hour) <= 23:
            raise ValueError("daily_reset_hour 必须在 0..23")
        if int(self.stamina_interval_minutes) < 0:
            raise ValueError("stamina_interval_minutes 不能是负数")

    @property
    def stamina_enabled_effective(self) -> bool:
        return self.enabled and self.stamina_enabled and self.stamina_interval_minutes > 0

    @property
    def daily_enabled_effective(self) -> bool:
        return self.enabled and self.daily_reset_enabled

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "daily_reset_enabled": self.daily_reset_enabled,
            "daily_reset_hour": self.daily_reset_hour,
            "stamina_enabled": self.stamina_enabled,
            "stamina_interval_minutes": self.stamina_interval_minutes,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> ReminderSettings:
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise ValueError("提醒配置必须是对象")
        try:
            return cls(
                enabled=bool(data.get("enabled", True)),
                daily_reset_enabled=bool(data.get("daily_reset_enabled", True)),
                daily_reset_hour=int(data.get("daily_reset_hour", DAILY_RESET_HOUR) or DAILY_RESET_HOUR),
                stamina_enabled=bool(data.get("stamina_enabled", False)),
                stamina_interval_minutes=int(data.get("stamina_interval_minutes", 0) or 0),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"提醒配置不合法: {exc}") from exc


@dataclass(frozen=True)
class Reminder:
    kind: str
    title: str
    message: str
    due_at: float

    def to_notification(self) -> tuple[str, str]:
        """框架通知需要 ``(message, title)`` 这个顺序。"""
        return (self.message, self.title)


def next_daily_reset(now: float, hour: int = DAILY_RESET_HOUR) -> float:
    """``now`` 之后（含当天）最近一次重置时刻。"""
    moment = datetime.fromtimestamp(now)
    candidate = moment.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def current_reset_date(now: float, hour: int = DAILY_RESET_HOUR) -> date:
    """``now`` 属于哪一天的「重置周期」：04:00 之前算前一天。"""
    moment = datetime.fromtimestamp(now)
    return (moment - timedelta(hours=int(hour))).date()


@dataclass
class ReminderScheduler:
    """按设置产出「该提醒了」的事件；调用方负责发通知。"""

    settings: ReminderSettings = field(default_factory=ReminderSettings)
    clock: object = field(default=time.time)

    _last_daily: date | None = None
    _last_stamina: float | None = None
    _started: float | None = None

    def __post_init__(self):
        now = float(self.clock())
        self._started = now
        self._last_stamina = now
        # 启动时不补发「昨天」的重置提醒：只在跨过重置点后提醒
        self._last_daily = current_reset_date(now, self.settings.daily_reset_hour)

    # ── 轮询 ─────────────────────────────────────────────
    def poll(self, now: float | None = None) -> list[Reminder]:
        moment = float(self.clock() if now is None else now)
        reminders: list[Reminder] = []

        if self.settings.daily_enabled_effective:
            today = current_reset_date(moment, self.settings.daily_reset_hour)
            if self._last_daily is None or today != self._last_daily:
                self._last_daily = today
                reminders.append(
                    Reminder(
                        kind=REMINDER_DAILY,
                        title="每日重置",
                        message=f"每日 {self.settings.daily_reset_hour:02d}:00 重置已到，日常/体力已刷新",
                        due_at=moment,
                    )
                )

        if self.settings.stamina_enabled_effective:
            interval = max(1, int(self.settings.stamina_interval_minutes)) * 60.0
            last = self._last_stamina if self._last_stamina is not None else (self._started or moment)
            if moment - last >= interval:
                self._last_stamina = moment
                reminders.append(
                    Reminder(
                        kind=REMINDER_STAMINA,
                        title="体力提醒",
                        message=f"已经过去 {int(interval // 60)} 分钟，看看体力有没有溢出",
                        due_at=moment,
                    )
                )
        return reminders

    # ── 状态 ─────────────────────────────────────────────
    def seconds_to_daily_reset(self, now: float | None = None) -> float:
        moment = float(self.clock() if now is None else now)
        return max(0.0, next_daily_reset(moment, self.settings.daily_reset_hour) - moment)

    def reset(self) -> None:
        """清空内部计时（设置被改过之后调用，避免用旧间隔继续算）。"""
        now = float(self.clock())
        self._started = now
        self._last_stamina = now
        self._last_daily = current_reset_date(now, self.settings.daily_reset_hour)


# ── 持久化 ───────────────────────────────────────────────
def load_settings(path: str | Path | None = None) -> ReminderSettings:
    """读提醒设置；文件缺失或损坏时退回默认。"""
    file_path = Path(path) if path is not None else SETTINGS_FILE
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ReminderSettings()
    try:
        return ReminderSettings.from_dict(data)
    except ValueError:
        return ReminderSettings()


def save_settings(settings: ReminderSettings, path: str | Path | None = None) -> Path:
    file_path = Path(path) if path is not None else SETTINGS_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file_path)
    return file_path
