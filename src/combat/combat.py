from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from inspect import isabstract
from math import inf
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NoReturn, overload

from ok import BaseScene, TaskDisabledException, WaitFailedException

from src.char import BASE_CHAR_NAME, normalize_binding_name
from src.char.base_char import BaseChar
from src.combat.base_scheduler import BaseScheduler
from src.combat.combat_state import CombatState, SlotState
from src.combat.team_manager import TeamManager, TeamSlot
from src.combat.team_manager import team_manager as default_team_manager
from src.image.team_portraits import SLOT_COUNT

if TYPE_CHECKING:
    import numpy as np

    from src.core.base_game_task import BaseGameTask


class CombatTeamError(ValueError):
    """识别到的队伍无法为调度器唯一确定所需角色。"""


class NotInCombatException(Exception):
    """战斗结束，中断正在执行的动作。"""


class CharDeadException(NotInCombatException):
    """战斗因角色阵亡而中断。"""


class CombatScene(BaseScene):
    """可选的单帧检测缓存。共享执行器上的不同 Combat 各自缓存检测结果。"""

    def __init__(self) -> None:
        self.checked: set[Combat] = set()

    def reset(self) -> None:
        self.checked.clear()


class Combat:
    """使用宿主任务的取帧、计时和输入能力，管理一支队伍的战斗生命周期。"""

    FREEZE_HISTORY_WINDOW = 60.0

    def __init__(
        self,
        task: BaseGameTask,
        meta: dict[str, Any],
        *,
        team_manager: TeamManager | None = None,
        detect_in_combat: Callable[[], bool] | None = None,
    ) -> None:
        self.task = task
        self.team_manager = team_manager if team_manager is not None else default_team_manager
        if not isinstance(meta, dict):
            raise TypeError("Combat metadata must be a dictionary")
        self.meta = meta.copy()
        if missing := {"scheduler", "chars"} - self.meta.keys():
            raise ValueError(f"Missing combat metadata keys: {', '.join(sorted(missing))}")
        try:
            self.meta["chars"] = tuple(self.meta["chars"])
        except TypeError as exc:
            raise TypeError("Combat metadata 'chars' must be an iterable of character entries") from exc
        self.char_overrides = self._resolve_chars(self.meta["chars"])
        self.scheduler = self._create_scheduler(self.meta["scheduler"])
        self.state = CombatState()
        self.chars: list[BaseChar] = []
        self._detector = detect_in_combat if detect_in_combat is not None else getattr(task, "detect_in_combat", None)
        self.combat_end_condition: Callable[[], bool] | None = None
        self.sleep_check_interval = 0.4
        self.switch_char_time_out = 5.0
        self.switch_key_interval = 0.1
        self.switch_poll_interval = 0.1
        self.switch_cd = 1.0
        self.wait_combat_interval = 0.02

    def _resolve_chars(self, chars: tuple[type[BaseChar] | None, ...]) -> Mapping[str, type[BaseChar]]:
        if len(chars) != SLOT_COUNT:
            raise ValueError(f"Combat metadata requires exactly {SLOT_COUNT} character entries")
        overrides = {}
        for char in chars:
            if char is None:
                continue
            if not isinstance(char, type) or not issubclass(char, BaseChar) or isabstract(char):
                raise TypeError("A combat character must be a concrete BaseChar subclass or None")
            if normalize_binding_name(char.__name__) == BASE_CHAR_NAME:
                raise ValueError("Use None for an unrestricted character")
            name = self.team_manager.factory.name_for_behavior(char)
            if name in overrides:
                raise ValueError(f"Duplicate character in combat metadata: {name}")
            overrides[name] = char
        return MappingProxyType(overrides)

    @staticmethod
    def _create_scheduler(factory: Callable[[], BaseScheduler]) -> BaseScheduler:
        if not callable(factory):
            raise TypeError("scheduler must be a BaseScheduler class or factory")
        if isinstance(factory, type) and (not issubclass(factory, BaseScheduler) or isabstract(factory)):
            raise TypeError("scheduler must be a concrete BaseScheduler class")
        scheduler = factory()
        if not isinstance(scheduler, BaseScheduler):
            raise TypeError("scheduler factory must return a BaseScheduler")
        return scheduler

    def validate_team(self, slots: Sequence[TeamSlot]) -> None:
        """在实例化队员前检查实机队伍是否与元数据匹配。"""
        recognized = set()
        for slot in slots:
            identity = slot.char_name if slot.char_name != BASE_CHAR_NAME else slot.template_id
            if identity is None:
                continue
            if identity in recognized:
                raise CombatTeamError(f"Duplicate character in detected team: {identity}")
            recognized.add(identity)
        missing = self.char_overrides.keys() - recognized
        if missing:
            raise CombatTeamError(f"Required character missing from detected team: {', '.join(sorted(missing))}")

    def detect_in_combat(self) -> bool:
        """默认使用宿主的 detect_in_combat，也可在构造时传入场景专属判据。"""
        if self._detector is None:
            raise NotImplementedError("Provide a combat detector on the task, in the constructor or on Combat")
        return self._detector()

    def detect_enter_combat(self) -> bool:
        return self.detect_in_combat()

    def on_combat_check(self) -> bool:
        return True

    def is_char_dead(self) -> bool:
        return False

    def on_wait_combat_tick(self) -> None:
        """等待入战期间每帧触发，可用于索敌或移动。"""

    def send_switch_char_key(self, target: BaseChar) -> None:
        self.task.send_key(str(target.index + 1))

    def add_freeze_duration(self, start: float, duration: float = -1.0, freeze_time: float = 0.1) -> None:
        now = self.task.active_time()
        if duration < 0:
            duration = now - start
        if start >= 0 and duration > freeze_time:
            history = self.state.freeze_durations
            cutoff = now - self.FREEZE_HISTORY_WINDOW
            history[:] = [item for item in history if item[0] > cutoff]
            history.append((start, duration, freeze_time))

    def time_elapsed_accounting_for_freeze(self, start: float) -> float:
        if start < 0:
            return 10000.0
        frozen = sum(duration - freeze_time for since, duration, freeze_time in self.state.freeze_durations
                     if start < since)
        return max(0.0, self.task.active_time() - start - frozen)

    def on_switch_cooldown(self, char: BaseChar) -> bool:
        last = self.slot_state(char).last_switch_time
        return self.switch_cd > 0 and last >= 0 and self.time_elapsed_accounting_for_freeze(last) <= self.switch_cd

    def slot_state(self, char: BaseChar) -> SlotState:
        if not 0 <= char.index < len(self.chars) or self.chars[char.index] is not char:
            raise ValueError("Character is not in this combat's team")
        return self.state.slots[char.index]

    def record_perform(self, char: BaseChar) -> None:
        self.slot_state(char).last_perform = self.task.active_time()

    def load_chars(self) -> bool:
        """从同一帧识别队伍并初始化所有槽位，只在配队变化时报告角色名。"""
        frame = self.task.frame
        in_team, index, count = self.team_manager.team_state(frame)
        if not in_team or not 0 <= index < count <= SLOT_COUNT:
            return False
        previous = self.chars
        chars = self.team_manager.instantiate(self, frame, count, previous)
        now = self.task.active_time()
        self.chars = chars
        self.state.reset_team(count)
        self.state.end_reason = ""
        self.state.started_at = now
        self.state.set_current(index, now)
        if chars != previous:
            names = [self.task.tr(self.team_manager.factory.display_msgid(char.char_name)) for char in chars]
            self.task.info_set("Chars", ", ".join(names))
            for char in chars:
                self.task.log_info(f"loaded char[{char.index}]={char.char_name} conf={char.confidence:.2f}")
        return True

    def get_current_char(self) -> BaseChar | None:
        index = self.state.current_index
        return self.chars[index] if index is not None else None

    def has_char(self, char_cls: type[BaseChar] | str) -> BaseChar | None:
        for char in self.chars:
            if isinstance(char_cls, str):
                if char.char_name == char_cls:
                    return char
            elif isinstance(char, char_cls):
                return char
        return None

    @overload
    def require_char[T: BaseChar](self, char_cls: type[T]) -> T: ...

    @overload
    def require_char(self, char_cls: str) -> BaseChar: ...

    def require_char(self, char_cls: type[BaseChar] | str) -> BaseChar:
        """供调度器按类或稳定名取得实际槽位的队员。"""
        name = char_cls if isinstance(char_cls, str) else self.team_manager.factory.name_for_behavior(char_cls)
        matches = [char for char in self.chars if char.char_name == name]
        if not matches:
            raise CombatTeamError(f"Character missing from combat team: {name}")
        if len(matches) > 1:
            raise CombatTeamError(f"Character is ambiguous in combat team: {name}")
        char = matches[0]
        if isinstance(char_cls, type) and not isinstance(char, char_cls):
            raise CombatTeamError(f"{name} uses {char.name}, expected {char_cls.__name__}")
        return char

    def update_state(self, frame: np.ndarray | None = None) -> BaseChar | None:
        """将 HUD 确认的出战槽位同步到全队。HUD 暂时不可读时保留上一次确认值。"""
        in_team, index, count = self.team_manager.team_state(self.task.frame if frame is None else frame)
        if not in_team or count != len(self.chars) or not 0 <= index < count:
            return None
        self.state.set_current(index, self.task.active_time())
        return self.chars[index]

    def _leave(self, reason: str) -> bool:
        self.state.active = False
        self.state.skip_combat_check = False
        self.state.end_reason = reason
        if isinstance(scene := self.task.scene, CombatScene):
            scene.checked.discard(self)
        return False

    def reset_combat_state(self, reason: str = "combat reset") -> None:
        self._leave(reason)
        self.chars = []
        self.state.reset_team(0)

    def _check_in_combat(self) -> bool:
        scene = self.task.scene
        if self.state.active:
            if isinstance(scene, CombatScene) and self in scene.checked:
                return True
            current = self.get_current_char()
            if current is None or not current.skip_combat_check():
                if not self.on_combat_check():
                    self.task.log_info("on_combat_check failed")
                    return self._leave("on_combat_check failed")
                if self.combat_end_condition is not None and self.combat_end_condition():
                    return self._leave("end condition reached")
                if not self.detect_in_combat():
                    return self._leave("combat ended")
        elif self.detect_enter_combat() and self.load_chars():
            self.state.active = True
        else:
            return False
        if isinstance(scene, CombatScene):
            scene.checked.add(self)
        return True

    def in_combat(self) -> bool:
        """检测入战或脱战，保护检测期间的框架 sleep 回调免于重入。"""
        previous = self.task.in_sleep_check
        self.task.in_sleep_check = True
        try:
            return self._check_in_combat()
        except (TaskDisabledException, NotInCombatException, NotImplementedError, CombatTeamError):
            raise
        except Exception as exc:
            self.task.logger.error("combat detection failed", exc)
            return self._leave("combat detection failed")
        finally:
            self.task.in_sleep_check = previous

    def check_combat(self) -> None:
        if self.state.active and not self.state.skip_combat_check and not self.in_combat():
            self.raise_not_in_combat(self.state.end_reason)

    def raise_not_in_combat(self, message: str) -> NoReturn:
        self.task.logger.info(message)
        dead = self.is_char_dead()
        self._leave(message)
        if dead:
            raise CharDeadException(message)
        raise NotInCombatException(message)

    def sleep_check(self) -> None:
        """ok-script 在调用此守卫前已获取新帧。"""
        if self.state.active and not self.state.skip_combat_check:
            self.update_state()
            self.check_combat()

    def sleep(self, seconds: float, check_combat: bool = True) -> None:
        previous = self.state.skip_combat_check
        if not check_combat:
            self.state.skip_combat_check = True
        try:
            with self.task.use_sleep_check(self.sleep_check, self.sleep_check_interval):
                self.task.sleep(seconds)
        finally:
            self.state.skip_combat_check = previous

    def switch_to(self, target: BaseChar, post_action: Callable[[BaseChar], object] | None = None) -> BaseChar:
        """切到指定队员，等待 HUD 确认后再返回。调度器决定目标与后续动作。"""
        self.slot_state(target)
        current = self.get_current_char()
        last_send = -1.0
        for frame in self.task.loop(time_out=self.switch_char_time_out, raise_if_time_out=False):
            observed = self.update_state(frame)
            if observed is target:
                if post_action is not None:
                    post_action(target)
                return target
            self.check_combat()
            if observed is None:
                continue
            if target.wait_switch():
                self.sleep(self.switch_poll_interval)
                continue
            now = self.task.active_time()
            if last_send < 0 or now - last_send >= self.switch_key_interval:
                self.send_switch_char_key(target)
                last_send = now
        self.raise_not_in_combat(f"failed switch chars {current} -> {target}")

    def wait_combat(self, time_out: float = 10.0, raise_if_not_found: bool = True) -> bool:
        for _ in self.task.loop(time_out=time_out, raise_if_time_out=False):
            if self.in_combat():
                return True
            self.on_wait_combat_tick()
            if self.wait_combat_interval > 0:
                self.task.sleep(self.wait_combat_interval)
        if raise_if_not_found:
            raise WaitFailedException("wait_combat timed out")
        return False

    def frames(self) -> Iterator[np.ndarray]:
        """每次调度前提供新帧及更新后的队伍状态，直到本场战斗结束。"""
        if not self.state.active:
            return
        for frame in self.task.loop(time_out=inf):
            self.update_state(frame)
            if not self.in_combat():
                if self.is_char_dead():
                    raise CharDeadException(self.state.end_reason)
                return
            yield frame
            if not self.state.active:
                return

    def combat_once(self, wait_combat_time: float = 0.0, raise_if_not_found: bool = True) -> bool:
        entered = False
        try:
            with self.task.use_sleep_check(self.sleep_check, self.sleep_check_interval):
                if wait_combat_time > 0:
                    entered = self.wait_combat(wait_combat_time, raise_if_not_found)
                else:
                    entered = self.in_combat()
                    if not entered and raise_if_not_found:
                        raise WaitFailedException("not in combat")
                if entered:
                    self.scheduler.run(self)
        except TaskDisabledException:
            self.reset_combat_state("task disabled")
            raise
        except CharDeadException as exc:
            self._leave(str(exc))
            self.state.reset_team(len(self.chars))
            raise
        except NotInCombatException as exc:
            self._leave(str(exc))
            self.task.logger.info(f"combat_once out of combat: {exc}")
        except Exception:
            if entered or self.state.active:
                self.reset_combat_state("combat interrupted")
            raise

        if entered:
            self.combat_end()
        return entered

    def combat_end(self) -> None:
        reason = self.state.end_reason or "combat ended"
        current = self.get_current_char()
        try:
            if current is not None:
                current.on_combat_end(self.chars)
        except TaskDisabledException:
            self.chars = []
            reason = "task disabled"
            raise
        finally:
            self._leave(reason)
            self.state.reset_team(len(self.chars))
