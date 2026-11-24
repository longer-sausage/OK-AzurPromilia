"""关闭行为：点 × 时问「完全关闭 / 收到托盘」，可勾选记住。

框架本身只有一个 ``Minimize Window to System Tray when Closing`` 开关（要么总是关、
要么总是托盘），没有「问一下」的余地。这里把决策抽成纯函数，
真正的弹窗由 :mod:`src.patches.close_behavior_patch` 挂到 ``MainWindow.closeEvent`` 上。

三种答案：

- ``close``：完全退出（走框架原逻辑，包含退出看门狗与清理）；
- ``tray``：隐藏到托盘（事件忽略，进程继续）；
- ``cancel``：什么都不做。

``remember`` 只有 ``close`` / ``tray`` 有意义（取消不该被记住）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SETTINGS_FILE = Path("configs") / "close_behavior.json"

CHOICE_CLOSE = "close"
CHOICE_TRAY = "tray"
CHOICE_CANCEL = "cancel"
CHOICES = (CHOICE_CLOSE, CHOICE_TRAY, CHOICE_CANCEL)
REMEMBERABLE = (CHOICE_CLOSE, CHOICE_TRAY)


class CloseBehaviorError(ValueError):
    """关闭行为配置不合法。"""


@dataclass(frozen=True)
class CloseBehavior:
    """``ask=False`` 时完全按框架原有行为（直接关闭）。"""

    ask: bool = True
    remember: str = ""

    def __post_init__(self):
        if self.remember and self.remember not in REMEMBERABLE:
            raise CloseBehaviorError(f"remember 只能是 {', '.join(REMEMBERABLE)} 或空")

    def to_dict(self) -> dict:
        return {"ask": self.ask, "remember": self.remember}

    @classmethod
    def from_dict(cls, data: dict | None) -> CloseBehavior:
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise CloseBehaviorError("关闭行为配置必须是对象")
        remember = str(data.get("remember", "") or "")
        return cls(ask=bool(data.get("ask", True)), remember=remember)

    def with_remember(self, choice: str) -> CloseBehavior:
        if choice not in REMEMBERABLE:
            raise CloseBehaviorError(f"只能记住 {', '.join(REMEMBERABLE)}，收到 {choice}")
        return CloseBehavior(ask=self.ask, remember=choice)

    def cleared(self) -> CloseBehavior:
        return CloseBehavior(ask=self.ask, remember="")


def decide(behavior: CloseBehavior) -> str | None:
    """返回要执行的动作；``None`` 表示「需要问用户」。"""
    if not behavior.ask:
        return CHOICE_CLOSE
    if behavior.remember in REMEMBERABLE:
        return behavior.remember
    return None


def resolve(behavior: CloseBehavior, answer: str, remember: bool) -> tuple[str, CloseBehavior]:
    """把用户的回答落成 ``(动作, 新配置)``。"""
    if answer not in CHOICES:
        raise CloseBehaviorError(f"未知的关闭选项 {answer!r}")
    if remember and answer in REMEMBERABLE:
        return answer, behavior.with_remember(answer)
    return answer, behavior


def should_tray(action: str) -> bool:
    return action == CHOICE_TRAY


def should_exit(action: str) -> bool:
    return action == CHOICE_CLOSE


def load_settings(path: str | Path | None = None) -> CloseBehavior:
    """读配置；文件缺失或损坏时退回默认（每次询问）。"""
    file_path = Path(path) if path is not None else SETTINGS_FILE
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CloseBehavior()
    try:
        return CloseBehavior.from_dict(data)
    except CloseBehaviorError:
        return CloseBehavior()


def save_settings(behavior: CloseBehavior, path: str | Path | None = None) -> Path:
    file_path = Path(path) if path is not None else SETTINGS_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(behavior.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file_path)
    return file_path
