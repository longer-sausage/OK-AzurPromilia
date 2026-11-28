from __future__ import annotations

import unittest
from enum import Enum
from types import SimpleNamespace
from unittest.mock import Mock

from ok import TaskDisabledException, TriggerTask, WaitFailedException

from src.combat import (
    BaseChar,
    BaseCombatTask,
    CharDeadException,
    CharFactory,
    CombatScene,
    NotInCombatException,
    SwitchPriority,
)


class FakeClock:
    def __init__(self, initial: float = 100.0) -> None:
        self.now = initial

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class FakeMatch:
    def __init__(self, name: str, confidence: float = 0.95) -> None:
        self.name = name
        self.confidence = confidence


class DummyTemplate(Enum):
    CHAR_A = "char_a"
    CHAR_A_ALT = "char_a_alt"
    CHAR_B = "char_b"


class CharA(BaseChar):
    def __init__(self, task, index, char_name=None, confidence=1.0, tag="default"):
        super().__init__(task, index, char_name=char_name, confidence=confidence)
        self.tag = tag


class CharB(BaseChar):
    pass


class CharC(BaseChar):
    pass


class DummyCombatTask(BaseCombatTask):
    def __init__(self, executor, app, clock: FakeClock | None = None) -> None:
        super().__init__(executor, app)
        self._clock = clock or FakeClock()
        self.team_state: tuple[bool, int, int] = (True, 0, 3)
        self.combat_detected = True
        self.enter_combat_detected: bool | None = None
        self.dead = False
        self.slot_boxes: dict[int, object] = {}
        self.sent_keys: list[str] = []
        self.frame_advances = 0
        self.wait_ticks = 0

    def active_time(self) -> float:
        return self._clock()

    def next_frame(self):
        self.frame_advances += 1
        self._clock.advance(0.05)
        if self.scene is not None:
            self.scene.reset()
        return None

    def sleep(self, timeout):
        if timeout > 0:
            self._clock.advance(timeout)
        return True

    def send_key(self, key, *args, **kwargs):
        self.sent_keys.append(str(key))
        if self.scene is not None:
            self.scene.reset()
        return True

    def in_team(self) -> tuple[bool, int, int]:
        return self.team_state

    def detect_in_combat(self) -> bool:
        return self.combat_detected

    def detect_enter_combat(self) -> bool:
        if self.enter_combat_detected is not None:
            return self.enter_combat_detected
        return super().detect_enter_combat()

    def is_char_dead(self) -> bool:
        return self.dead

    def get_char_slot_box(self, index: int):
        return self.slot_boxes.get(index)

    def on_wait_combat_tick(self) -> None:
        self.wait_ticks += 1


def make_task(
    *,
    with_scene: bool = True,
    clock: FakeClock | None = None,
) -> DummyCombatTask:
    scene = CombatScene() if with_scene else None
    executor = SimpleNamespace(
        scene=scene,
        debug=False,
        pause_start=None,
        paused=False,
        exit_event=SimpleNamespace(is_set=lambda: False),
        wait_scene_timeout=10,
        wait_until_settle_time=0,
        reset_scene=scene.reset if scene is not None else (lambda: None),
        locale=None,
    )
    app = SimpleNamespace(tr=lambda text: text)
    return DummyCombatTask(executor, app, clock=clock)


class TestBaseChar(unittest.TestCase):
    def test_perform_updates_last_perform_and_calls_do_perform(self):
        clock = FakeClock(10.0)
        task = make_task(clock=clock)
        called = []

        class RecordingChar(BaseChar):
            def do_perform(self):
                called.append(self.last_perform)

        char = RecordingChar(task, 0)
        char.perform()

        self.assertEqual(called, [10.0])
        self.assertEqual(char.last_perform, 10.0)

    def test_switch_in_out_and_reset_state(self):
        clock = FakeClock(20.0)
        task = make_task(clock=clock)
        char = BaseChar(task, 1)

        char.switch_in()
        self.assertTrue(char.is_current_char)
        self.assertEqual(char.last_switch_in_time, 20.0)

        clock.advance(2.5)
        char.switch_out()
        self.assertFalse(char.is_current_char)
        self.assertEqual(char.last_switch_time, 22.5)

        char.reset_state()
        self.assertEqual(char.last_switch_time, -1.0)
        self.assertEqual(char.last_switch_in_time, -1.0)
        self.assertEqual(char.last_perform, -1.0)

    def test_sleep_restores_skip_combat_check_on_exception_and_nesting(self):
        task = make_task()
        char = BaseChar(task, 0)

        for previous in (False, True):
            for check_combat in (False, True):
                with self.subTest(previous=previous, check_combat=check_combat):
                    task.skip_combat_check = previous

                    def interrupt_sleep(seconds, prev=previous, chk=check_combat):
                        self.assertEqual(task.skip_combat_check, prev or not chk)
                        raise TaskDisabledException()

                    task.sleep = interrupt_sleep
                    with self.assertRaises(TaskDisabledException):
                        char.sleep(0.1, check_combat=check_combat)
                    self.assertEqual(task.skip_combat_check, previous)

        # Nested sleep must not clear outer check_combat=False suppression.
        flags = []

        def nested_sleep(seconds):
            flags.append(task.skip_combat_check)
            if seconds == 1.0:
                char.sleep(0.1, check_combat=True)
                flags.append(task.skip_combat_check)

        task.sleep = nested_sleep
        task.skip_combat_check = False
        char.sleep(1.0, check_combat=False)
        self.assertEqual(flags, [True, True, True])
        self.assertFalse(task.skip_combat_check)

    def test_char_equality_and_priority_ordering(self):
        self.assertEqual(CharA(None, 0), CharA(None, 0))
        self.assertNotEqual(CharA(None, 0), CharA(None, 1))
        self.assertNotEqual(CharA(None, 0), CharB(None, 0))
        self.assertLess(SwitchPriority.NO, SwitchPriority.LOW)
        self.assertLess(SwitchPriority.LOW, SwitchPriority.NORMAL)
        self.assertLess(SwitchPriority.NORMAL, SwitchPriority.HIGH)
        self.assertLess(SwitchPriority.HIGH, SwitchPriority.MUST)


class TestCharFactory(unittest.TestCase):
    def test_register_alias_templates_normalizes_canonical_name_and_fast_recheck(self):
        factory = CharFactory()
        factory.register((DummyTemplate.CHAR_A, DummyTemplate.CHAR_A_ALT), CharA, tag="hero")
        factory.register(DummyTemplate.CHAR_B, CharB)

        searches: list[tuple[str, ...]] = []
        task = SimpleNamespace(
            find_one=Mock(return_value=None),
            find_best_match_in_box=Mock(
                side_effect=lambda box, names, threshold=0.6: (
                    searches.append(tuple(names)) or FakeMatch("char_a_alt", 0.99)
                )
            ),
        )

        char = factory.get_char_by_pos(task, "slot_0", 0, None)
        self.assertIsInstance(char, CharA)
        self.assertEqual(char.char_name, "char_a")
        self.assertEqual(char.tag, "hero")
        self.assertEqual(searches[-1], ("char_a", "char_a_alt", "char_b"))

        # Second lookup rechecks only the cached character's own templates and reuses instance.
        rechecked = factory.get_char_by_pos(task, "slot_0", 0, char)
        self.assertIs(rechecked, char)
        self.assertEqual(searches[-1], ("char_a", "char_a_alt"))

    def test_resolution_suffix_lookup_and_fallbacks(self):
        factory = CharFactory()
        factory.register("char_a", CharA)

        task = SimpleNamespace(
            find_one=Mock(return_value=None),
            find_best_match_in_box=Mock(return_value=FakeMatch("char_a_2k", 0.88)),
        )
        char = factory.get_char_by_pos(task, "slot_0", 0, None)
        self.assertIsInstance(char, CharA)
        self.assertEqual(char.char_name, "char_a")

        # Obscured portrait preserves old_char; without old_char falls back to BaseChar("unknown").
        task.find_best_match_in_box.return_value = None
        kept = factory.get_char_by_pos(task, "slot_0", 1, char)
        self.assertIs(kept, char)
        self.assertEqual(kept.index, 1)

        unknown = factory.get_char_by_pos(task, "slot_0", 2, None)
        self.assertIs(type(unknown), BaseChar)
        self.assertEqual(unknown.char_name, "unknown")
        self.assertEqual(unknown.confidence, 0.0)

    def test_empty_registry_and_invalid_register(self):
        factory = CharFactory()
        with self.assertRaises(ValueError):
            factory.register((), CharA)
        with self.assertRaises(ValueError):
            factory.register("", CharA)

        task = SimpleNamespace(
            find_one=Mock(side_effect=AssertionError("should not be called")),
            find_best_match_in_box=Mock(side_effect=AssertionError("should not be called")),
        )
        unknown = factory.get_char_by_pos(task, "slot_0", 0, None)
        self.assertEqual(unknown.char_name, "unknown")


class TestCombatStateAndFreeze(unittest.TestCase):
    def test_scene_cache_and_without_scene_fallback(self):
        task = make_task(with_scene=True)
        detect_calls = 0
        original = task.detect_in_combat

        def counting_detect():
            nonlocal detect_calls
            detect_calls += 1
            return original()

        task.detect_in_combat = counting_detect
        self.assertTrue(task.in_combat())
        self.assertEqual(detect_calls, 1)

        # Same frame returns cached True from CombatScene without re-detecting.
        self.assertTrue(task.in_combat())
        self.assertEqual(detect_calls, 1)

        # Resetting scene forces a fresh detection on the next check.
        task.scene.reset()
        self.assertTrue(task.in_combat())
        self.assertEqual(detect_calls, 2)

        # Without a CombatScene attached, task still functions cleanly.
        no_scene_task = make_task(with_scene=False)
        self.assertTrue(no_scene_task.in_combat())
        self.assertTrue(no_scene_task.in_combat())

    def test_in_combat_hooks_and_end_conditions(self):
        task = make_task()
        self.assertTrue(task.in_combat())

        # Active char skip_combat_check suppresses combat-end detection.
        current = task.get_current_char()
        assert current is not None
        current.skip_combat_check = lambda: True
        task.combat_detected = False
        task.scene.reset()
        self.assertTrue(task.in_combat())

        # Once skip_combat_check is False, combat_end_condition terminates combat.
        current.skip_combat_check = lambda: False
        task.combat_detected = True
        task.combat_end_condition = lambda: True
        task.scene.reset()
        self.assertFalse(task.in_combat())
        self.assertEqual(task.out_of_combat_reason, "end condition reached")

        # on_combat_check returning False also terminates combat.
        task.combat_end_condition = None
        self.assertTrue(task.in_combat())
        task.on_combat_check = lambda: False
        task.scene.reset()
        self.assertFalse(task.in_combat())
        self.assertEqual(task.out_of_combat_reason, "on_combat_check failed")

    def test_sleep_check_and_check_combat_raise_on_exit_and_death(self):
        task = make_task()
        self.assertTrue(task.in_combat())

        task.combat_detected = False
        task.skip_combat_check = True
        task.check_combat()
        task.sleep_check()

        task.skip_combat_check = False
        with self.assertRaises(NotInCombatException):
            task.sleep_check()
        self.assertFalse(task._in_combat)

        # Re-enter and simulate character death.
        task.combat_detected = True
        self.assertTrue(task.in_combat())
        task.combat_detected = False
        task.dead = True
        task.scene.reset()
        with self.assertRaises(CharDeadException):
            task.check_combat()
        self.assertFalse(task._in_combat)

    def test_in_combat_reentrancy_flag_and_disabled_propagation(self):
        task = make_task()
        task.in_sleep_check = False
        task.detect_in_combat = Mock(side_effect=TaskDisabledException())
        with self.assertRaises(TaskDisabledException):
            task.in_combat()
        self.assertFalse(task.in_sleep_check)

        # Preserves outer in_sleep_check=True when invoked inside sleep_check.
        task.in_sleep_check = True
        task.detect_in_combat = Mock(return_value=False)
        self.assertFalse(task.in_combat())
        self.assertTrue(task.in_sleep_check)

    def test_freeze_durations_are_per_instance_and_subtracted_properly(self):
        clock_a = FakeClock(100.0)
        clock_b = FakeClock(100.0)
        task_a = make_task(clock=clock_a)
        task_b = make_task(clock=clock_b)

        start = clock_a()
        clock_a.advance(1.0)
        freeze_start = clock_a()
        clock_a.advance(2.0)
        task_a.add_freeze_duration(freeze_start, duration=2.0, freeze_time=0.1)

        # Total wall time = 3.0s, deducted freeze = 2.0 - 0.1 = 1.9s -> 1.1s elapsed.
        self.assertAlmostEqual(task_a.time_elapsed_accounting_for_freeze(start), 1.1, places=5)
        self.assertEqual(task_b.freeze_durations, [])
        self.assertEqual(task_a.time_elapsed_accounting_for_freeze(-1.0), 10000.0)


class TestSwitchingAndCombatLoop(unittest.TestCase):
    def test_round_robin_priority_and_switch_cd_selection(self):
        clock = FakeClock(50.0)
        task = make_task(clock=clock)
        c0 = CharA(task, 0)
        c1 = CharB(task, 1)
        c2 = CharC(task, 2)
        task.chars = [c0, c1, c2]
        c0.is_current_char = True
        c0.last_switch_in_time = 50.0

        # Round-robin: 0 -> 1 -> 2 -> 0
        self.assertIs(task._choose_switch_target(c0), c1)
        c0.switch_out()
        clock.advance(2.0)
        c1.switch_in()

        self.assertIs(task._choose_switch_target(c1), c2)
        c1.switch_out()
        clock.advance(2.0)
        c2.switch_in()

        self.assertIs(task._choose_switch_target(c2), c0)

        # Switch CD avoidance prefers teammate whose switch CD expired.
        c0.last_switch_time = clock() - 0.2  # on CD (<= 1.0s)
        c1.last_switch_time = clock() - 5.0  # ready
        self.assertIs(task._choose_switch_target(c2), c1)

        # Priority overrides round-robin; NO excludes candidate.
        c0.get_switch_priority = lambda current_char=None: SwitchPriority.MUST
        c1.get_switch_priority = lambda current_char=None: SwitchPriority.HIGH
        self.assertIs(task._choose_switch_target(c2), c0)

        c0.get_switch_priority = lambda current_char=None: SwitchPriority.NO
        c1.get_switch_priority = lambda current_char=None: SwitchPriority.NO
        self.assertIsNone(task._choose_switch_target(c2))

    def test_switch_next_char_handles_hud_gap_wait_switch_and_post_action(self):
        clock = FakeClock(10.0)
        task = make_task(clock=clock)
        c0 = CharA(task, 0)
        c1 = CharB(task, 1)
        task.chars = [c0, c1]
        c0.is_current_char = True
        task._in_combat = True

        wait_calls = [True, False]
        c1.wait_switch = lambda: wait_calls.pop(0) if wait_calls else False

        # Frame 1: in_team (0), c1 defers via wait_switch()
        # Frame 2: in_team (0), sends key "2"
        # Frame 3: not in_team (transient animation gap)
        # Frame 4: in_team (1), confirmed switched
        team_frames = iter([
            (True, 0, 2),
            (True, 0, 2),
            (False, -1, 2),
            (True, 1, 2),
        ])
        task.in_team = lambda: next(team_frames)

        post_targets = []
        switched = task.switch_next_char(c0, post_action=post_targets.append)

        self.assertIs(switched, c1)
        self.assertFalse(c0.is_current_char)
        self.assertTrue(c1.is_current_char)
        self.assertEqual(task.sent_keys, ["2"])
        self.assertEqual(post_targets, [c1])

    def test_switch_next_char_timeout_raises_not_in_combat(self):
        clock = FakeClock(10.0)
        task = make_task(clock=clock)
        task.switch_char_time_out = 0.2
        c0 = CharA(task, 0)
        c1 = CharB(task, 1)
        task.chars = [c0, c1]
        c0.is_current_char = True
        task._in_combat = True
        task.team_state = (True, 0, 2)

        with self.assertRaises(NotInCombatException):
            task.switch_next_char(c0)
        self.assertFalse(task._in_combat)
        self.assertGreaterEqual(len(task.sent_keys), 1)

    def test_load_chars_dynamic_team_size_and_identity_reporting(self):
        task = make_task()
        info_sets = []
        task.info_set = lambda key, val: info_sets.append((key, val))

        task.team_state = (True, 0, 2)
        self.assertTrue(task.load_chars())
        self.assertEqual(len(task.chars), 2)
        self.assertTrue(task.chars[0].is_current_char)
        self.assertFalse(task.chars[1].is_current_char)
        chars_updates = [item for item in info_sets if item[0] == "Chars"]
        self.assertEqual(len(chars_updates), 1)

        # Reloading same team does not re-emit info_set("Chars", ...).
        self.assertTrue(task.load_chars())
        chars_updates = [item for item in info_sets if item[0] == "Chars"]
        self.assertEqual(len(chars_updates), 1)

        # Invalid current_index or not in_team returns False.
        task.team_state = (False, -1, 2)
        self.assertFalse(task.load_chars())
        task.team_state = (True, 5, 2)
        self.assertFalse(task.load_chars())

    def test_wait_combat_and_combat_once_lifecycle(self):
        clock = FakeClock(0.0)
        task = make_task(clock=clock)
        task.combat_detected = False

        # Timeout without raising returns False; with raising throws WaitFailedException.
        self.assertFalse(task.wait_combat(time_out=0.15, raise_if_not_found=False))
        self.assertGreater(task.wait_ticks, 0)
        with self.assertRaises(WaitFailedException):
            task.wait_combat(time_out=0.15, raise_if_not_found=True)

        # Full combat_once flow: enter -> perform -> exit -> combat_end.
        events = []

        class FightingChar(BaseChar):
            def do_perform(self):
                events.append(f"perform_{self.index}")
                self.task.combat_detected = False
                self.task.next_frame()

            def on_combat_end(self, chars):
                events.append(f"end_{self.index}_{len(chars)}")

        task.char_factory.default_char_cls = FightingChar
        task.team_state = (True, 0, 1)
        task.combat_detected = True

        self.assertTrue(task.combat_once(wait_combat_time=1.0))
        self.assertEqual(events, ["perform_0", "end_0_1"])
        self.assertFalse(task._in_combat)
        self.assertEqual(task.freeze_durations, [])

    def test_combat_once_handles_disabled_and_char_dead(self):
        task = make_task()
        end_called = []

        class DisabledChar(BaseChar):
            def do_perform(self):
                raise TaskDisabledException()

            def on_combat_end(self, chars):
                end_called.append(True)

        task.char_factory.default_char_cls = DisabledChar
        task.team_state = (True, 0, 1)
        task.combat_detected = True

        with self.assertRaises(TaskDisabledException):
            task.combat_once(wait_combat_time=0.0)
        self.assertEqual(end_called, [])
        self.assertEqual(task.chars, [])
        self.assertFalse(task._in_combat)

        # CharDeadException propagates without calling on_combat_end.
        class DyingChar(BaseChar):
            def do_perform(self):
                self.task.dead = True
                self.task.raise_not_in_combat("char died")

            def on_combat_end(self, chars):
                end_called.append(True)

        task.char_factory.default_char_cls = DyingChar
        with self.assertRaises(CharDeadException):
            task.combat_once(wait_combat_time=0.0)
        self.assertEqual(end_called, [])
        self.assertFalse(task._in_combat)

    def test_trigger_task_subclass_mro_is_valid(self):
        class TriggerCombatTask(BaseCombatTask, TriggerTask):
            def in_team(self):
                return True, 0, 1

            def detect_in_combat(self):
                return False

        scene = CombatScene()
        executor = SimpleNamespace(scene=scene, debug=False, pause_start=None, paused=False, locale=None)
        task = TriggerCombatTask(executor, SimpleNamespace(tr=str))
        self.assertIsInstance(task, BaseCombatTask)
        self.assertIsInstance(task, TriggerTask)
        self.assertEqual(task.sleep_check_interval, 0.4)


if __name__ == "__main__":
    unittest.main()
