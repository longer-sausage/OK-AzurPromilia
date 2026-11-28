from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, NoReturn

from ok import BaseScene, TaskDisabledException, WaitFailedException

from src.chara.base_chara import BaseChara, SwitchPriority
from src.combat.team_manager import team_manager
from src.core.base_game_task import BaseGameTask
from src.image.team_portraits import SLOT_COUNT


class NotInCombatException(Exception):
    """已脱离战斗状态时抛出，用于立即中断当前角色行动并退回战斗主循环。"""


class CharaDeadException(NotInCombatException):
    """战斗中检测到角色阵亡时抛出。"""


class CombatScene(BaseScene):
    """单帧战斗状态缓存，随框架 ``reset_scene()`` 自动按帧清空。"""

    def __init__(self) -> None:
        self._in_combat: bool | None = None

    def reset(self) -> None:
        self._in_combat = None

    def in_combat(self) -> bool | None:
        return self._in_combat

    def set_in_combat(self) -> bool:
        self._in_combat = True
        return True

    def set_not_in_combat(self) -> bool:
        self._in_combat = False
        return False


class BaseCombatTask(BaseGameTask):
    """战斗任务底层基类：管理战斗状态机、队伍加载、角色切换调度与冻结计时。

    具体游戏的战斗任务应继承本类（触发式任务继承 ``BaseCombatTask, TriggerTask``），
    实现 ``detect_in_combat``，并按需覆写其余钩子方法。
    """

    FREEZE_HISTORY_WINDOW = 60.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.team_manager = team_manager
        self.chars: list[BaseChara | None] = []
        self._in_combat = False
        self.skip_combat_check = False
        self.sleep_check_interval = 0.4
        self.switch_char_time_out = 5.0
        self.switch_key_interval = 0.1
        self.switch_poll_interval = 0.1
        self.switch_cd = 1.0
        self.wait_combat_interval = 0.02
        self.out_of_combat_reason = ""
        self.combat_end_condition: Callable[[], bool] | None = None
        self.combat_start = 0.0
        self.freeze_durations: list[tuple[float, float, float]] = []

    # ── 子类实现钩子 ────────────────────────────────────

    def in_team(self) -> tuple[bool, int, int]:
        """检测是否处于编队可控状态。

        Returns:
            ``(in_team, current_index, team_size)``：是否在编队界面/世界可控态、
            当前出战角色槽位索引（0 起始）、队伍人数。
        """
        return self.team_manager.team_state(self.frame)

    def detect_in_combat(self) -> bool:
        """检测当前画面是否处于战斗状态。"""
        raise NotImplementedError("Subclasses of BaseCombatTask must implement detect_in_combat()")

    def detect_enter_combat(self) -> bool:
        """检测是否应进入战斗；默认复用 ``detect_in_combat``。"""
        return self.detect_in_combat()

    def on_combat_check(self) -> bool:
        """战斗中每轮额外校验钩子，返回 ``False`` 将立即判定脱战。"""
        return True

    def is_char_dead(self) -> bool:
        """脱战中断时判定是否为角色阵亡，返回 ``True`` 时抛出 ``CharaDeadException``。"""
        return False

    def send_switch_char_key(self, target_char: BaseChara) -> None:
        """发送切换到目标角色的按键输入；默认按 1-based 数字键。"""
        self.send_key(str(target_char.index + 1))

    def on_wait_combat_tick(self) -> None:
        """``wait_combat`` 等待入战期间每帧触发的钩子（可用于索敌/移动等）。"""

    # ── 时间与冻结扣除 ──────────────────────────────────

    def combat_time(self) -> float:
        """返回战斗用单调时间（优先使用暂停感知的 ``active_time``）。"""
        if hasattr(self, "_active_time_paused_total") and getattr(self, "_executor", None) is not None:
            return self.active_time()
        return time.monotonic()

    def add_freeze_duration(self, start: float, duration: float = -1.0, freeze_time: float = 0.1) -> None:
        """记录一次动画/时停冻结区间，后续计算经过时长时自动扣除。"""
        now = self.combat_time()
        if duration < 0:
            duration = now - start
        if start >= 0 and duration > freeze_time:
            cutoff = now - self.FREEZE_HISTORY_WINDOW
            self.freeze_durations = [item for item in self.freeze_durations if item[0] > cutoff]
            self.freeze_durations.append((start, duration, freeze_time))

    def time_elapsed_accounting_for_freeze(self, start: float) -> float:
        """计算自 ``start`` 起扣除冻结时长后的实际经过时间。"""
        if start < 0:
            return 10000.0
        to_minus = sum(
            duration - freeze_time
            for freeze_start, duration, freeze_time in self.freeze_durations
            if start < freeze_start
        )
        return max(0.0, self.combat_time() - start - to_minus)

    # ── 单帧 Scene 缓存辅助 ─────────────────────────────

    def _get_scene_in_combat(self) -> bool | None:
        scene = getattr(self, "scene", None)
        getter = getattr(scene, "in_combat", None)
        if callable(getter):
            return getter()
        return None

    def _set_scene_in_combat(self, value: bool) -> bool:
        scene = getattr(self, "scene", None)
        setter_name = "set_in_combat" if value else "set_not_in_combat"
        setter = getattr(scene, setter_name, None)
        if callable(setter):
            return bool(setter())
        return value

    # ── 战斗状态检查 ────────────────────────────────────

    def reset_to_false(self, reason: str = "") -> bool:
        self.out_of_combat_reason = reason
        return self.do_reset_to_false()

    def do_reset_to_false(self) -> bool:
        self._in_combat = False
        self.skip_combat_check = False
        self._set_scene_in_combat(False)
        return False

    def reset_combat_state(self, reason: str = "combat reset") -> None:
        """彻底清空战斗状态、已缓存队伍与冻结计时记录。"""
        self.reset_to_false(reason)
        self.chars = []
        self.freeze_durations = []

    def do_check_in_combat(self) -> bool:
        if self._in_combat:
            cached = self._get_scene_in_combat()
            if cached is not None:
                return cached
            current_char = self.get_current_char(raise_exception=False)
            if current_char is not None and current_char.skip_combat_check():
                return self._set_scene_in_combat(True)
            if not self.on_combat_check():
                self.log_info("on_combat_check failed")
                return self.reset_to_false(reason="on_combat_check failed")
            if self.combat_end_condition is not None and self.combat_end_condition():
                return self.reset_to_false(reason="end condition reached")
            if self.detect_in_combat():
                return self._set_scene_in_combat(True)
            return self.reset_to_false(reason="combat ended")

        if not self.detect_enter_combat():
            return False
        if not self.load_chars():
            return False
        self._in_combat = True
        return self._set_scene_in_combat(True)

    def in_combat(self) -> bool:
        previous_in_sleep_check = self.in_sleep_check
        self.in_sleep_check = True
        try:
            return bool(self.do_check_in_combat())
        except TaskDisabledException:
            raise
        except Exception as e:
            self.logger.error("do_check_in_combat failed", e)
            return False
        finally:
            self.in_sleep_check = previous_in_sleep_check

    def sleep_check(self) -> None:
        """由 ok-script ``TaskExecutor.sleep`` 周期性回调的战斗状态守卫。"""
        if self.skip_combat_check:
            return
        if self._in_combat:
            self.next_frame()
            if not self.in_combat():
                self.raise_not_in_combat("sleep check not in combat")

    def check_combat(self) -> None:
        """显式校验当前仍在战斗中，否则立即抛出脱战异常。"""
        if self.skip_combat_check:
            return
        if self._in_combat and not self.in_combat():
            self.raise_not_in_combat("combat check not in combat")

    def raise_not_in_combat(self, message: str) -> NoReturn:
        self.logger.info(message)
        dead = self.is_char_dead()
        self.reset_to_false(reason=message)
        if dead:
            raise CharaDeadException(message)
        raise NotInCombatException(message)

    # ── 队伍加载与查询 ──────────────────────────────────

    @staticmethod
    def _char_identity(chars: list[BaseChara | None]) -> tuple[tuple[str, str] | None, ...]:
        return tuple((c.char_name, c.name) if c is not None else None for c in chars)

    def load_chars(self) -> bool:
        """读取当前队伍并刷新角色实例与出战标记。"""
        in_team, current_index, count = self.in_team()
        if not in_team or not (0 <= current_index < count <= SLOT_COUNT):
            return False

        previous_identity = self._char_identity(self.chars)
        now = self.combat_time()
        new_chars = self.team_manager.instantiate(self, self.frame, count, self.chars)
        for char in new_chars:
            char.reset_state()
            char.is_current_char = char.index == current_index
            if char.is_current_char:
                char.last_switch_in_time = now

        self.chars = new_chars
        self.combat_start = now
        if self._char_identity(self.chars) != previous_identity:
            names = [self.tr(self.team_manager.factory.display_msgid(char.char_name)) for char in new_chars]
            self.info_set("Chars", ", ".join(names))
            for char in self.chars:
                if char is not None:
                    self.log_info(f"loaded char[{char.index}]={char.char_name} conf={char.confidence:.2f}")
        return True

    def get_current_char(self, raise_exception: bool = False) -> BaseChara | None:
        for char in self.chars:
            if char is not None and char.is_current_char:
                return char
        if raise_exception:
            self.raise_not_in_combat("cannot find current char")
        return None

    def has_char(self, char_cls: type[BaseChara] | str) -> BaseChara | None:
        """可按行为类或稳定 Chara 名查找当前队员。"""
        for char in self.chars:
            if isinstance(char_cls, str):
                if char is not None and char.char_name == char_cls:
                    return char
            elif isinstance(char, char_cls):
                return char
        return None

    # ── 角色切换调度 ────────────────────────────────────

    def _target_has_switch_cd(self, char: BaseChara) -> bool:
        if self.switch_cd <= 0 or char.last_switch_time < 0:
            return False
        return self.time_elapsed_accounting_for_freeze(char.last_switch_time) <= self.switch_cd

    def _select_switch_candidate(self, current_char: BaseChara, candidates: list[BaseChara]) -> BaseChara:
        """在最高优先级且同档的候选中选出切人目标；默认按最久未上场轮转。"""
        return min(candidates, key=lambda char: (char.last_switch_in_time, char.index))

    def _choose_switch_target(self, current_char: BaseChara) -> BaseChara | None:
        candidates = [char for char in self.chars if char is not None and char != current_char]
        if not candidates:
            return None

        prioritized: list[tuple[int, BaseChara]] = []
        for char in candidates:
            priority = char.get_switch_priority(current_char=current_char)
            if priority > SwitchPriority.NO:
                prioritized.append((priority, char))
        if not prioritized:
            return None

        highest_priority = max(priority for priority, _ in prioritized)
        top_candidates = [char for priority, char in prioritized if priority == highest_priority]
        ready_candidates = [char for char in top_candidates if not self._target_has_switch_cd(char)]
        if ready_candidates:
            top_candidates = ready_candidates
        return self._select_switch_candidate(current_char, top_candidates)

    def switch_next_char(
        self,
        current_char: BaseChara,
        post_action: Callable[[BaseChara], Any] | None = None,
    ) -> BaseChara | None:
        """按优先级选择下一个角色并持续发送切人输入，直到确认切人成功或超时脱战。"""
        switch_to = self._choose_switch_target(current_char)
        if switch_to is None or switch_to == current_char:
            self.logger.warning(f"{current_char} has no available switch target")
            return None

        self.logger.debug(f"switch_next_char {current_char} -> {switch_to}")
        last_send = -1.0
        for _ in self.loop(time_out=self.switch_char_time_out, yield_frame=True, raise_if_time_out=False):
            in_team, current_index, _ = self.in_team()
            already_switched = in_team and current_index == switch_to.index
            if not already_switched:
                self.check_combat()
            if not in_team:
                continue
            if already_switched:
                current_char.switch_out()
                switch_to.switch_in()
                if post_action is not None:
                    post_action(switch_to)
                return switch_to
            if switch_to.wait_switch():
                self.sleep(self.switch_poll_interval)
                continue
            now = self.combat_time()
            if last_send < 0 or now - last_send >= self.switch_key_interval:
                self.send_switch_char_key(switch_to)
                last_send = now

        self.raise_not_in_combat(f"failed switch chars {current_char} -> {switch_to}")

    # ── 战斗主循环 ──────────────────────────────────────

    def wait_combat(self, time_out: float = 10.0, raise_if_not_found: bool = True) -> bool:
        """等待进入战斗状态。"""
        for _ in self.loop(time_out=time_out, yield_frame=True, raise_if_time_out=False):
            if self.in_combat():
                return True
            self.on_wait_combat_tick()
            if self.wait_combat_interval > 0:
                self.sleep(self.wait_combat_interval)
        if raise_if_not_found:
            raise WaitFailedException("wait_combat timed out")
        return False

    def combat_once(self, wait_combat_time: float = 0.0, raise_if_not_found: bool = True) -> bool:
        """执行一整场战斗：可选等待入战 → 循环驱动当前角色 ``perform`` → 结束后清理状态。"""
        entered = False
        try:
            if wait_combat_time > 0:
                if not self.wait_combat(time_out=wait_combat_time, raise_if_not_found=raise_if_not_found):
                    return False
            elif not self.in_combat():
                if raise_if_not_found:
                    raise WaitFailedException("not in combat")
                return False

            while self.in_combat():
                entered = True
                current_char = self.get_current_char(raise_exception=True)
                assert current_char is not None
                current_char.perform()
        except TaskDisabledException:
            self.reset_combat_state(reason="task disabled")
            raise
        except CharaDeadException:
            self._reset_char_states()
            raise
        except NotInCombatException as e:
            self.logger.info(f"combat_once out of combat: {e}")

        if entered:
            self.combat_end()
        return entered

    def _reset_char_states(self) -> None:
        for char in self.chars:
            if char is not None:
                char.reset_state()
        self.freeze_durations = []

    def combat_end(self) -> None:
        """战斗正常结束时回调当前角色的 ``on_combat_end`` 并重置全队状态。"""
        current_char = self.get_current_char(raise_exception=False)
        if current_char is not None:
            current_char.on_combat_end(self.chars)
        self._reset_char_states()
