from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException, TriggerTask, WaitFailedException
from ok.task.TaskExecutor import TaskExecutor

from src.char.starborn import Starborn
from src.combat import (
    BaseChar,
    BaseScheduler,
    CharDeadException,
    CharFactory,
    Combat,
    CombatScene,
    NotInCombatException,
    SlotState,
    SwitchPriority,
    TeamManager,
)
from src.core.base_game_task import BaseGameTask


class GameTask(BaseGameTask):
    """实际任务基类搭配可控时钟和队伍 HUD。每次取帧都经过框架的 scene 失效边界。"""

    def __init__(self, *, with_scene=True):
        executor = SimpleNamespace(frame=None, scene=CombatScene() if with_scene else None, debug=False)
        super().__init__(executor, SimpleNamespace(tr=str))
        self.now = 100.0
        self.hud = (True, 0, 3)
        self.battle_visible = True
        self.sent_keys = []

    def active_time(self):
        return self.now

    def detect_in_combat(self):
        return self.battle_visible

    def next_frame(self):
        self.now += 0.05
        if self.scene is not None:
            self.scene.reset()
        return self.frame

    def sleep(self, seconds):
        self.now += seconds

    def send_key(self, key):
        self.sent_keys.append(key)
        if key in ("1", "2", "3"):
            self.hud = (True, int(key) - 1, self.hud[2])


def make_combat(task=None, scheduler=None):
    if task is None:
        task = GameTask()
    combat = Combat(task, scheduler)
    combat.team_manager = TeamManager()
    combat.team_manager.team_state = lambda frame: task.hud
    return combat


class TestCombatState(unittest.TestCase):
    def test_all_characters_see_perform_and_confirmed_switch_at_the_same_time(self):
        combat = make_combat()
        state = combat.state
        observations = []

        class RecordingChar(BaseChar):
            def do_perform(self):
                observations.append(self.combat.chars[2].state.slots[self.index].last_perform)

        combat.team_manager.factory.default_char_cls = RecordingChar
        self.assertTrue(combat.in_combat())
        first, second, third = combat.chars
        self.assertTrue(all(char.state is state and char.task is combat.task for char in combat.chars))
        first.perform()
        self.assertEqual(observations, [100.0])
        self.assertEqual(first.last_perform, 100.0)
        self.assertEqual(state.slots[0].last_switch_in_time, 100.0)
        self.assertEqual(state.slots[1], SlotState())

        combat.task.now += 2.5
        combat.task.hud = (True, 1, 3)
        self.assertIs(combat.update_state(), second)
        self.assertEqual([c.is_current_char for c in combat.chars], [False, True, False])
        self.assertEqual(third.state.slots[0].last_switch_time, 102.5)
        self.assertEqual(first.state.slots[1].last_switch_in_time, 102.5)
        self.assertEqual(first.last_switch_time, second.last_switch_in_time)
        combat.task.now += 1
        combat.update_state()
        self.assertEqual(state.slots[1].last_switch_in_time, 102.5)

        for invalid in ((False, -1, 0), (True, 0, 1), (True, -1, 3)):
            combat.task.hud = invalid
            self.assertIsNone(combat.update_state())
            self.assertIs(combat.get_current_char(), second)
        combat.combat_end()
        self.assertIs(third.state, state)
        self.assertIsNone(state.current_index)
        self.assertEqual(state.slots, (SlotState(),) * 3)
        self.assertFalse(any(c.is_current_char for c in combat.chars))

    def test_sleep_suppression_restores_nested_state_even_on_error(self):
        combat = make_combat()
        combat.load_chars()
        char = combat.chars[0]
        for previous in (False, True):
            for check_combat in (False, True):
                with self.subTest(previous=previous, check_combat=check_combat):
                    combat.state.skip_combat_check = previous

                    def interrupt(seconds, expected=previous or not check_combat):
                        self.assertEqual(combat.state.skip_combat_check, expected)
                        raise TaskDisabledException()

                    combat.task.sleep = interrupt
                    with self.assertRaises(TaskDisabledException):
                        char.sleep(0.1, check_combat=check_combat)
                    self.assertEqual(combat.state.skip_combat_check, previous)
                    self.assertEqual(combat.task.sleep_check_interval, -1)

        flags = []

        def nested_sleep(seconds):
            flags.append(combat.state.skip_combat_check)
            if seconds == 1.0:
                combat.sleep(0.1)
                flags.append(combat.state.skip_combat_check)

        combat.task.sleep = nested_sleep
        combat.state.skip_combat_check = False
        combat.sleep(1.0, check_combat=False)
        self.assertEqual(flags, [True, True, True])
        self.assertFalse(combat.state.skip_combat_check)

    def test_freeze_and_scene_cache_are_isolated_even_with_the_same_host(self):
        task = GameTask()
        first = make_combat(task)
        second = make_combat(task)
        for combat in (first, second):
            combat.detect_in_combat = Mock(return_value=True)
            self.assertTrue(combat.in_combat())
            self.assertTrue(combat.in_combat())
            combat.detect_in_combat.assert_called_once_with()
        first.reset_combat_state()
        self.assertTrue(second.in_combat())
        task.next_frame()
        self.assertTrue(second.in_combat())
        self.assertEqual(second.detect_in_combat.call_count, 2)

        start = task.active_time()
        task.now += 1
        freeze_start = task.active_time()
        task.now += 2
        second.chars[0].add_freeze_duration(freeze_start, freeze_time=0.1)
        self.assertAlmostEqual(second.chars[2].time_elapsed_accounting_for_freeze(start), 1.1)
        self.assertEqual(second.time_elapsed_accounting_for_freeze(-1.0), 10000.0)
        self.assertEqual(first.state.freeze_durations, [])
        self.assertIsNot(first.scheduler, second.scheduler)

        no_scene = make_combat(GameTask(with_scene=False))
        no_scene.detect_in_combat = Mock(return_value=True)
        self.assertTrue(no_scene.in_combat())
        self.assertTrue(no_scene.in_combat())
        self.assertEqual(no_scene.detect_in_combat.call_count, 2)

    def test_combat_check_hooks_and_end_conditions(self):
        combat = make_combat()
        self.assertTrue(combat.in_combat())
        current = combat.get_current_char()
        current.skip_combat_check = lambda: True
        combat.task.battle_visible = False
        combat.task.next_frame()
        self.assertTrue(combat.in_combat())

        current.skip_combat_check = lambda: False
        combat.task.battle_visible = True
        combat.combat_end_condition = lambda: True
        combat.task.next_frame()
        self.assertFalse(combat.in_combat())
        self.assertEqual(combat.state.end_reason, "end condition reached")
        combat.combat_end_condition = None
        self.assertTrue(combat.in_combat())
        combat.on_combat_check = lambda: False
        combat.task.next_frame()
        self.assertFalse(combat.in_combat())
        self.assertEqual(combat.state.end_reason, "on_combat_check failed")

    def test_explicit_detector_can_replace_host_detection(self):
        task = GameTask()
        detector = Mock(return_value=False)
        combat = Combat(task, detect_in_combat=detector)
        self.assertFalse(combat.in_combat())
        detector.assert_called_once_with()

    def test_exit_and_death_checks(self):
        combat = make_combat()
        self.assertTrue(combat.in_combat())
        combat.task.battle_visible = False
        combat.state.skip_combat_check = True
        combat.task.next_frame()
        combat.check_combat()
        combat.sleep_check()
        combat.state.skip_combat_check = False
        with self.assertRaises(NotInCombatException):
            combat.sleep_check()
        self.assertFalse(combat.state.active)

        combat.task.battle_visible = True
        self.assertTrue(combat.in_combat())
        combat.is_char_dead = lambda: True
        combat.task.battle_visible = False
        combat.task.next_frame()
        with self.assertRaises(CharDeadException):
            combat.check_combat()
        self.assertFalse(combat.state.active)

    def test_host_owns_reentrancy_flag_and_disabled_propagates(self):
        combat = make_combat()
        for previous in (False, True):
            with self.subTest(previous=previous):
                combat.task.in_sleep_check = previous

                def disabled():
                    self.assertTrue(combat.task.in_sleep_check)
                    raise TaskDisabledException()

                combat.detect_in_combat = disabled
                with self.assertRaises(TaskDisabledException):
                    combat.in_combat()
                self.assertEqual(combat.task.in_sleep_check, previous)

    def test_load_chars_dynamic_size_reuse_and_identity_reporting(self):
        combat = make_combat()
        combat.task.info_set = Mock()
        combat.task.hud = (True, 1, 2)
        self.assertTrue(combat.load_chars())
        first, second = combat.chars
        self.assertIs(combat.get_current_char(), second)
        self.assertIs(combat.has_char(BaseChar), first)
        self.assertTrue(combat.load_chars())
        self.assertIs(combat.chars[0], first)
        reported = [c.args for c in combat.task.info_set.call_args_list if c.args[0] == "Chars"]
        self.assertEqual(reported, [("Chars", "默认, 默认")])

        for invalid in ((False, -1, 2), (True, 5, 2), (True, 0, 4)):
            combat.task.hud = invalid
            self.assertFalse(combat.load_chars())
            self.assertIs(combat.get_current_char(), second)
        combat.task.hud = (True, 0, 1)
        self.assertTrue(combat.load_chars())
        self.assertEqual(len(combat.state.slots), 1)
        self.assertFalse(second.is_current_char)
        self.assertIs(combat.get_current_char(), first)
        with self.assertRaises(ValueError):
            second.perform()


class TestCharFactory(unittest.TestCase):
    def test_create_by_stable_name_reuses_only_same_combat_and_slot(self):
        factory = CharFactory()
        spec = factory.get_spec("Starborn")
        self.assertIs(spec.cls, Starborn)
        self.assertEqual(spec.display_msgid, "星临者")
        combat = make_combat()
        char = factory.create(combat, 0, spec.canonical_name, 0.94)
        self.assertIsInstance(char, Starborn)
        self.assertEqual(char.char_name, "Starborn")
        self.assertIs(factory.create(combat, 0, "Starborn", 0.92, char), char)
        self.assertEqual(char.confidence, 0.92)
        self.assertIsNone(factory.get_spec("星临者"))
        self.assertIsNot(factory.create(make_combat(combat.task), 0, "Starborn", 0.95, char), char)
        self.assertIsNot(factory.create(combat, 1, "Starborn", 0.95, char), char)
        for name in (None, "BaseChar", "unknown", "MissingChar"):
            with self.subTest(name=name):
                unknown = factory.create(combat, 0, name, 0.99, char)
                self.assertIs(type(unknown), BaseChar)
                self.assertEqual((unknown.char_name, unknown.confidence), ("BaseChar", 0.0))

    def test_invalid_register(self):
        factory = CharFactory()
        for name, cls in (("", Starborn), ((), Starborn), ("OtherName", Starborn),
                          ("Starborn", BaseChar), ("BaseChar", BaseChar)):
            with self.subTest(name=name, cls=cls), self.assertRaises(ValueError):
                factory.register(name, cls)

    def test_char_equality_and_priority_ordering(self):
        combat = make_combat()
        self.assertEqual(BaseChar(combat, 0), BaseChar(combat, 0))
        self.assertNotEqual(BaseChar(combat, 0), BaseChar(combat, 1))
        self.assertNotEqual(BaseChar(combat, 0), Starborn(combat, 0))
        self.assertTrue(SwitchPriority.NO < SwitchPriority.LOW < SwitchPriority.NORMAL
                        < SwitchPriority.HIGH < SwitchPriority.MUST)


class TestCombatScheduling(unittest.TestCase):
    def test_default_scheduler_rotates_after_each_perform_and_cleans_up(self):
        events = []

        class RecordingChar(BaseChar):
            def do_perform(self):
                events.append(self.index)
                if len(events) == 4:
                    self.task.battle_visible = False

            def on_combat_end(self, chars):
                events.append(("end", self.index, len(chars)))

        combat = make_combat()
        combat.team_manager.factory.default_char_cls = RecordingChar
        self.assertTrue(combat.combat_once(wait_combat_time=1))
        self.assertEqual(events, [0, 1, 2, 0, ("end", 0, 3)])
        self.assertEqual(combat.task.sent_keys, ["2", "3", "1"])
        self.assertFalse(combat.state.active)
        self.assertIsNone(combat.get_current_char())
        self.assertEqual(combat.state.slots, (SlotState(),) * 3)

    def test_round_robin_priority_and_switch_cooldown(self):
        combat = make_combat()
        combat.load_chars()
        first, second, third = combat.chars
        choose = combat.scheduler.choose_switch_target
        for index, expected in enumerate((second, third, first)):
            combat.task.hud = (True, index, 3)
            combat.task.now += 2
            current = combat.update_state()
            self.assertIs(choose(combat, current), expected)
        slots = combat.state.slots
        slots[0].last_switch_time = combat.task.now - 0.2
        slots[1].last_switch_time = combat.task.now - 5
        self.assertIs(choose(combat, third), second)
        first.get_switch_priority = lambda current_char: SwitchPriority.HIGH + 1
        second.get_switch_priority = lambda current_char: SwitchPriority.HIGH
        self.assertIs(choose(combat, third), first)
        first.get_switch_priority = second.get_switch_priority = lambda current_char: SwitchPriority.NO
        self.assertIsNone(choose(combat, third))

    def test_explicit_switch_handles_hud_gap_wait_switch_and_post_action(self):
        combat = make_combat()
        combat.task.hud = (True, 0, 2)
        self.assertTrue(combat.in_combat())
        first, target = combat.chars
        target.wait_switch = Mock(side_effect=[True, False])
        combat.team_manager.team_state = Mock(side_effect=[
            (True, 0, 2), (True, 0, 2), (False, -1, 0), (True, 1, 2),
        ])
        observed = []
        switched = combat.switch_to(target, lambda char: observed.append((char, first.state.current_index)))
        self.assertIs(switched, target)
        self.assertEqual(observed, [(target, 1)])
        self.assertEqual(combat.task.sent_keys, ["2"])
        self.assertEqual(first.state.slots[0].last_switch_time, target.state.slots[1].last_switch_in_time)

    def test_switch_timeout_and_foreign_target_leave_slot_history_intact(self):
        combat = make_combat()
        self.assertTrue(combat.in_combat())
        with self.assertRaises(ValueError):
            combat.switch_to(BaseChar(combat, 1))
        self.assertEqual(combat.task.sent_keys, [])
        combat.switch_char_time_out = 0.2
        combat.team_manager.team_state = lambda frame: (True, 0, 3)
        with self.assertRaises(NotInCombatException):
            combat.switch_to(combat.chars[1])
        self.assertFalse(combat.state.active)
        self.assertEqual(combat.state.current_index, 0)
        self.assertEqual(combat.state.slots[1], SlotState())
        self.assertGreaterEqual(len(combat.task.sent_keys), 1)

    def test_custom_target_only_changes_rotation_order(self):
        events = []

        class ReverseScheduler(BaseScheduler):
            def choose_switch_target(self, combat, current):
                return combat.chars[current.index - 1]

        class RecordingChar(BaseChar):
            def do_perform(self):
                events.append(self.index)
                if len(events) == 3:
                    self.task.battle_visible = False

        combat = make_combat(scheduler=ReverseScheduler())
        combat.team_manager.factory.default_char_cls = RecordingChar
        self.assertTrue(combat.combat_once())
        self.assertEqual(events, [0, 2, 1])
        self.assertEqual(combat.task.sent_keys, ["3", "2"])

    def test_replacement_scheduler_can_act_directly_and_observe_external_switch(self):
        observed = []

        class SkillScheduler(BaseScheduler):
            def step(self, combat):
                observed.append(combat.state.current_index)
                if len(observed) == 1:
                    combat.task.send_key("e")
                    combat.switch_to(combat.chars[2])
                    observed.append(combat.state.current_index)
                    combat.task.send_key("2")
                else:
                    self.last_switch = combat.state.slots[2].last_switch_time
                    combat.task.battle_visible = False

        combat = make_combat()
        scheduler = SkillScheduler()
        combat.scheduler = scheduler
        with patch.object(BaseChar, "perform", side_effect=AssertionError("unexpected perform")):
            self.assertTrue(combat.combat_once())
        self.assertEqual(observed, [0, 2, 1])
        self.assertGreater(scheduler.last_switch, 100.0)
        self.assertEqual(combat.task.sent_keys, ["e", "3", "2"])

    def test_full_scheduler_can_finish_early_and_see_fresh_state_next_fight(self):
        calls = []

        class ScriptedScheduler(BaseScheduler):
            def run(self, combat):
                calls.append((combat.state, combat.state.slots[1].last_switch_in_time))
                combat.switch_to(combat.chars[1])
                combat.task.send_key("q")

        combat = make_combat(scheduler=ScriptedScheduler())
        for _ in range(2):
            combat.task.hud = (True, 0, 2)
            with patch.object(BaseChar, "perform", side_effect=AssertionError("unexpected perform")):
                self.assertTrue(combat.combat_once())
            self.assertFalse(combat.state.active)
        self.assertEqual(calls, [(combat.state, -1.0)] * 2)
        self.assertEqual(combat.task.sent_keys, ["2", "q", "2", "q"])

    def test_wait_timeout_and_no_entry_do_not_dispatch(self):
        combat = make_combat()
        combat.task.battle_visible = False
        combat.on_wait_combat_tick = Mock()
        combat.scheduler.run = Mock()
        self.assertFalse(combat.combat_once(wait_combat_time=0.15, raise_if_not_found=False))
        combat.on_wait_combat_tick.assert_called()
        self.assertFalse(combat.combat_once(raise_if_not_found=False))
        for wait in (0.0, 0.15):
            with self.assertRaises(WaitFailedException):
                combat.combat_once(wait_combat_time=wait)
        combat.scheduler.run.assert_not_called()

    def test_errors_clear_shared_state_and_restore_host_without_normal_end_hook(self):
        for error in (TaskDisabledException(), CharDeadException("char died"), RuntimeError("action failed")):
            with self.subTest(error=type(error)):
                combat = make_combat()
                combat.task.sleep_check_interval = 0.75
                combat.scheduler.run = Mock(side_effect=error)
                with patch.object(BaseChar, "on_combat_end") as end, self.assertRaises(type(error)):
                    combat.combat_once()
                end.assert_not_called()
                self.assertFalse(combat.state.active)
                self.assertIsNone(combat.state.current_index)
                self.assertEqual(combat.state.freeze_durations, [])
                self.assertEqual(combat.state.slots, (SlotState(),) * len(combat.chars))
                self.assertEqual(combat.task.sleep_check_interval, 0.75)
                if isinstance(error, TaskDisabledException):
                    self.assertEqual(combat.chars, [])

    def test_frame_driven_scheduler_propagates_death_without_a_switch_or_sleep(self):
        combat = make_combat()
        combat.scheduler.step = lambda combat: setattr(combat.task, "battle_visible", False)
        combat.is_char_dead = lambda: True
        with patch.object(BaseChar, "on_combat_end") as end, self.assertRaises(CharDeadException):
            combat.combat_once()
        end.assert_not_called()
        self.assertFalse(combat.state.active)
        self.assertEqual(combat.state.slots, (SlotState(),) * 3)

    def test_task_disabled_during_end_hook_clears_the_team(self):
        combat = make_combat()
        combat.scheduler.run = Mock()
        with (
            patch.object(BaseChar, "on_combat_end", side_effect=TaskDisabledException()),
            self.assertRaises(TaskDisabledException),
        ):
            combat.combat_once()
        self.assertEqual(combat.chars, [])
        self.assertFalse(combat.state.active)
        self.assertEqual(combat.state.slots, ())
        self.assertEqual(combat.state.end_reason, "task disabled")

    def test_framework_sleep_during_direct_scheduler_input_detects_exit(self):
        class TriggerGameTask(GameTask, TriggerTask):
            pass

        task = TriggerGameTask()
        combat = make_combat(task)
        executor = task.executor
        executor.debug_mode = False
        executor.current_task = task
        executor.check_enabled = Mock()
        executor.reset_scene = lambda **kwargs: task.scene.reset()
        executor.next_frame = Mock(wraps=task.next_frame)
        executor.sleep = lambda seconds: TaskExecutor.sleep(executor, seconds)
        task.sleep = BaseGameTask.sleep.__get__(task)
        host_check = Mock()

        def run(combat):
            task.battle_visible = False
            task.sleep(10)
            self.fail("scheduler continued after combat ended")

        combat.scheduler.run = run
        with task.use_sleep_check(host_check, 0.8):
            self.assertTrue(combat.combat_once())
            self.assertEqual(task.sleep_check_interval, 0.8)
        host_check.assert_called_once_with()
        executor.next_frame.assert_called_once_with()
        self.assertFalse(task.in_sleep_check)
        self.assertEqual(task.sleep_check_interval, -1)
        self.assertFalse(combat.state.active)


if __name__ == "__main__":
    unittest.main()
