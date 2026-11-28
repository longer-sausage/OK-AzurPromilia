"""副本门禁：判断是否该把战斗让给游戏自带自动战斗。

规则（用户设定）：
- 第一次打（关卡还没三星、游戏自带自动战斗未解锁）→ 我们的全自动接管；
- 已解锁自动战斗 → 我们停止任务，直接用游戏自带的打。

判据：游戏自带自动战斗的 UI 特征（``profile.GAME_AUTO_FEATURES``）连续出现。
"""

from __future__ import annotations

from src.combat import profile
from src.combat.recognition.report import feature_visible


class DungeonGate:
    """检测「游戏自带自动战斗可用」，决定是否让权。"""

    def __init__(self, find, enabled: bool = True, confirm_frames: int = 2) -> None:
        self._find = find
        self.enabled = enabled
        self.confirm_frames = max(1, int(confirm_frames))
        self._hits = 0

    def reset(self) -> None:
        self._hits = 0

    def should_yield(self, frame) -> bool:
        """连续确认后返回 True：应当让给游戏自带自动战斗。"""
        if not self.enabled or frame is None:
            return False
        if feature_visible(self._find, profile.GAME_AUTO_FEATURES, frame):
            self._hits = min(self._hits + 1, self.confirm_frames)
        else:
            self._hits = 0
        return self._hits >= self.confirm_frames

    @property
    def hits(self) -> int:
        return self._hits
