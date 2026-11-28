"""自动战斗框架（第二版）单元测试：视觉判据 / 检测 / 调度 / 模式 / 门禁。

全部离线：帧用合成图，按键用 mock，不依赖游戏窗口。
"""

from __future__ import annotations

import unittest
from unittest import mock

import cv2
import numpy as np

from src.combat import profile
from src.combat.base_combat_task import BattlePhase
from src.combat.characters import build_character, character_ids
from src.combat.detection import CombatChecker, CombatObservation
from src.combat.dungeon_gate import DungeonGate
from src.combat.enums import FieldClaimLevel, StopReason
from src.combat.objectives import CaptureObjective, FullAutoObjective
from src.combat.recognition import (
    HealthBarReading,
    LockStarReading,
    Region,
    read_health_bar,
    read_marker,
    read_skill,
)
from src.combat.recognition.report import RecognitionReport
from src.combat.types import FieldClaim, RouteStep
from src.config import config as app_config
from src.icons import Icons
from src.tasks.trigger.auto_combat_task import AutoCombatTask


def _battle_frame(fill: float = 0.9):
    """黑底 + 中部薄血条 + 点亮的技能按钮白环。"""
    frame = np.zeros((1080, 1920, 3), np.uint8)
    cv2.rectangle(frame, (760, 400), (1040, 406), (40, 40, 40), -1)
    width = int(280 * fill)
    if width > 0:
        cv2.rectangle(frame, (760, 400), (760 + width, 406), (0, 0, 220), -1)
    for region in profile.SKILL_REGIONS.values():
        x1, y1, x2, y2 = Region(*region).pixels(1920, 1080)
        center = ((x1 + x2) // 2, (y1 + y2) // 2)
        radius = int(min(x2 - x1, y2 - y1) * 0.45)
        cv2.circle(frame, center, radius, (255, 255, 255), 3)
    return frame


def _empty_frame():
    return np.zeros((1080, 1920, 3), np.uint8)


def _build_task(config: dict | None = None, frame=None) -> AutoCombatTask:
    executor = mock.MagicMock()
    executor.debug = False
    executor.frame = None
    executor.pause_start = None
    executor.method.width = 1920
    executor.method.height = 1080
    task = AutoCombatTask(executor, mock.MagicMock())
    task.config = dict(task.default_config)
    if config:
        task.config.update(config)
    task.active_time = mock.Mock(return_value=1000.0)
    task.next_frame = mock.Mock(return_value=frame if frame is not None else _battle_frame())
    task.press = mock.Mock()
    task.disable = mock.Mock()
    task.log_info = mock.Mock()
    task.log_debug = mock.Mock()
    task.log_warning = mock.Mock()
    task.log_error = mock.Mock()
    return task


class TestRecognitionSignals(unittest.TestCase):
    def test_reads_health_bar_fill_ratio(self):
        reading = read_health_bar(_battle_frame(0.9), profile.ENEMY_HP_REGION, profile.ENEMY_HP_HSV_RANGES)
        self.assertIsNotNone(reading)
        self.assertAlmostEqual(reading.fill_ratio, 0.9, delta=0.05)

    def test_no_health_bar_on_empty_frame(self):
        self.assertIsNone(read_health_bar(_empty_frame(), profile.ENEMY_HP_REGION, profile.ENEMY_HP_HSV_RANGES))

    def test_finds_lock_marker(self):
        frame = _empty_frame()
        cv2.rectangle(frame, (900, 500), (940, 540), (0, 220, 0), -1)  # 绿色锁定光环
        marker = read_marker(frame, profile.LOCK_REGION, profile.LOCK_HSV_RANGES)
        self.assertIsNotNone(marker)
        self.assertGreaterEqual(marker.area, 1500)

    def test_skill_ready_uses_white_ring_coverage(self):
        region = profile.SKILL_REGIONS["skill"]
        ready = read_skill(_battle_frame(), "skill", region)
        self.assertTrue(ready.ready)
        self.assertGreaterEqual(ready.ready_ratio, profile.READY_RING_COVERAGE)
        cooling = read_skill(_empty_frame(), "skill", region)
        self.assertFalse(cooling.ready)
        self.assertIsNone(read_skill(_empty_frame(), "skill", None))


class TestChecker(unittest.TestCase):
    def test_requires_consecutive_frames_to_enter(self):
        checker = CombatChecker(lambda feature, frame=None: None, enter_frames=2, exit_frames=3)
        frame = _battle_frame()
        self.assertFalse(checker.observe(frame).in_combat)
        observation = checker.observe(frame)
        self.assertTrue(observation.in_combat)
        self.assertAlmostEqual(observation.enemy_hp_ratio, 0.9, delta=0.05)

    def test_lock_only_starts_combat(self):
        checker = CombatChecker(lambda feature, frame=None: None, enter_frames=1, exit_frames=1)
        lock_report = RecognitionReport(
            frame_ok=True, lock=LockStarReading(box=(0, 0, 10, 10), score=0.9, red_ratio=0.2)
        )
        with mock.patch("src.combat.detection.recognize", return_value=lock_report):
            checker.observe(_empty_frame(), 0.0)
        self.assertTrue(checker.last.in_combat)

    def test_lock_keeps_combat_alive_until_budget_runs_out(self):
        checker = CombatChecker(
            lambda feature, frame=None: None,
            enter_frames=1,
            exit_frames=1,
            middle_click=lambda: None,
            max_retargets=0,  # 预算为 0：不确定期结束直接脱战
        )
        hp_report = RecognitionReport(
            frame_ok=True, health=HealthBarReading(box=(0, 0, 10, 4), fill_ratio=1.0, confidence=1.0)
        )
        with mock.patch("src.combat.detection.recognize", return_value=hp_report):
            checker.observe(_battle_frame(), 0.0)
        self.assertTrue(checker.last.in_combat)
        empty_report = RecognitionReport(frame_ok=True)
        with mock.patch("src.combat.detection.recognize", return_value=empty_report):
            checker.observe(_empty_frame(), 1.0)  # 信号丢失 → 不确定期
            self.assertTrue(checker.last.in_combat)
            checker.observe(_empty_frame(), 5.0)  # 不确定期结束 → 预算为 0 → 脱战
        self.assertFalse(checker.last.in_combat)

    def test_sweep_only_runs_after_a_battle(self):
        clicks = []
        checker = CombatChecker(
            lambda feature, frame=None: None,
            enter_frames=1,
            exit_frames=1,
            middle_click=lambda: clicks.append(1),
            max_retargets=0,
            sweep_interval=1.0,
            sweep_seconds=2.0,
        )
        # 从未进战：不按中键
        checker.observe(_empty_frame(), 0.0)
        self.assertEqual(len(clicks), 0)
        # 打一场后脱战：进入扫描窗口
        checker.observe(_battle_frame(), 1.0)
        checker.observe(_empty_frame(), 2.0)
        checker.observe(_empty_frame(), 3.0)
        before = len(clicks)
        checker.observe(_empty_frame(), 3.1)
        self.assertGreater(len(clicks), before)
        # 窗口结束后不再按
        after = len(clicks)
        checker.observe(_empty_frame(), 10.0)
        self.assertEqual(len(clicks), after)

    def test_leaves_combat_after_miss_window(self):
        clicks = []
        checker = CombatChecker(
            lambda feature, frame=None: None,
            enter_frames=2,
            exit_frames=2,
            middle_click=lambda: clicks.append(1),
        )
        for _ in range(2):
            checker.observe(_battle_frame(), 0.0)
        self.assertTrue(checker.last.in_combat)

        # 失去信号：进入不确定期，仍算在战斗，并按一次中键索敌
        checker.observe(_empty_frame(), 1.0)
        checker.observe(_empty_frame(), 1.1)
        self.assertTrue(checker.last.in_combat)
        self.assertTrue(checker.last.retargeting)
        self.assertTrue(clicks)

        # 不确定期结束 → 中键索敌期，仍算在战斗
        checker.observe(_empty_frame(), 2.0)
        self.assertTrue(checker.last.in_combat)

        # 索敌超时（2 秒）才真正脱战
        observation = checker.observe(_empty_frame(), 5.0)
        self.assertFalse(observation.in_combat)

    def test_retarget_revives_combat_after_two_frames(self):
        checker = CombatChecker(lambda feature, frame=None: None, enter_frames=1, exit_frames=1)
        checker.observe(_battle_frame(), 0.0)
        checker.observe(_empty_frame(), 1.0)
        self.assertTrue(checker.last.in_combat)
        # 单帧回来不算，防闪烁
        checker.observe(_battle_frame(), 1.2)
        self.assertTrue(checker.last.retargeting)
        checker.observe(_battle_frame(), 1.4)
        self.assertFalse(checker.last.retargeting)
        self.assertTrue(checker.last.in_combat)
        self.assertFalse(checker.last.retargeting)

    def test_game_auto_feature_is_not_a_combat_signal(self):
        def find(feature, frame=None):
            return object() if feature in profile.GAME_AUTO_FEATURES else None

        checker = CombatChecker(find, enter_frames=1, exit_frames=1)
        observation = checker.observe(_empty_frame())
        # 副本界面到处都有自动战斗图标，不能据此判定"在战斗"
        self.assertFalse(observation.in_combat)
        self.assertTrue(observation.game_auto)


class TestObjectives(unittest.TestCase):
    class _Task:
        battle_started_at = 0.0

    def test_full_auto_stops_when_out_of_combat(self):
        objective = FullAutoObjective()
        reason = objective.should_stop(self._Task(), CombatObservation(in_combat=False), 10.0)
        self.assertIs(reason, StopReason.OUT_OF_COMBAT)

    def test_full_auto_stops_on_result_screen(self):
        objective = FullAutoObjective()
        reason = objective.should_stop(self._Task(), CombatObservation(in_combat=True, ended=True), 10.0)
        self.assertIs(reason, StopReason.BATTLE_ENDED)

    def test_capture_stops_at_hp_threshold_after_confirm(self):
        objective = CaptureObjective(hp_threshold=0.2, confirm_frames=2)
        low = CombatObservation(in_combat=True, enemy_hp_ratio=0.1)
        self.assertIsNone(objective.should_stop(self._Task(), low, 10.0))
        self.assertIs(objective.should_stop(self._Task(), low, 10.1), StopReason.HP_THRESHOLD)

    def test_capture_ignores_high_hp(self):
        objective = CaptureObjective(hp_threshold=0.2, confirm_frames=2)
        high = CombatObservation(in_combat=True, enemy_hp_ratio=0.8)
        self.assertIsNone(objective.should_stop(self._Task(), high, 10.0))


class TestGate(unittest.TestCase):
    def test_yields_after_confirm_frames(self):
        gate = DungeonGate(lambda feature, frame=None: object(), enabled=True, confirm_frames=2)
        frame = _empty_frame()
        self.assertFalse(gate.should_yield(frame))
        self.assertTrue(gate.should_yield(frame))

    def test_disabled_gate_never_yields(self):
        gate = DungeonGate(lambda feature, frame=None: object(), enabled=False)
        for _ in range(5):
            self.assertFalse(gate.should_yield(_empty_frame()))


class TestTaskTick(unittest.TestCase):
    def test_metadata_and_registration(self):
        task = _build_task()
        self.assertEqual(task.name, "自动战斗")
        self.assertEqual(task.icon, Icons.Trigger)
        self.assertIs(task.default_config["_enabled"], False)
        self.assertEqual(task.default_config["_战斗模式"], "全自动")
        # 捉宠相关的界面项先隐藏，配置里仍可切换
        self.assertNotIn("战斗模式", task.default_config)
        self.assertNotIn("战斗模式", (task.config_type or {}))
        self.assertIn(
            ["src.tasks.trigger.auto_combat_task", "AutoCombatTask"],
            app_config["trigger_tasks"],
        )

    def test_nothing_happens_outside_battle(self):
        task = _build_task(frame=_empty_frame())
        task.find_one = mock.Mock(return_value=None)
        self.assertFalse(task.combat_tick())
        task.press.assert_not_called()

    def test_enters_battle_and_presses_ultimate(self):
        task = _build_task(frame=_battle_frame())
        task.find_one = mock.Mock(return_value=None)
        self.assertFalse(task.combat_tick())  # 第 1 帧：确认中
        self.assertTrue(task.combat_tick())  # 第 2 帧：进入并出手
        self.assertEqual(task.phase, BattlePhase.INSIDE)
        pressed = [call.args[0] for call in task.press.call_args_list]
        self.assertIn(profile.KEY_ULTIMATE, pressed)

    def test_presses_ultimate_key(self):
        task = _build_task({}, frame=_battle_frame())
        task.find_one = mock.Mock(return_value=None)
        for _ in range(3):
            task.combat_tick()
        pressed = [call.args[0] for call in task.press.call_args_list]
        self.assertIn(profile.KEY_ULTIMATE, pressed)

    def test_interact_prompt_presses_f(self):
        task = _build_task(frame=_empty_frame())
        task.find_one = mock.Mock(return_value=None)
        with mock.patch("src.combat.base_combat_task.find_interact_prompt", return_value=(10, 10, 50, 50)):
            task.combat_tick()
            pressed = [call.args[0] for call in task.press.call_args_list]
            self.assertIn("f", pressed)
            # 冷却内不重复按
            task.press.reset_mock()
            task.combat_tick()
            pressed = [call.args[0] for call in task.press.call_args_list]
            self.assertNotIn("f", pressed)

    def test_config_switches_rebuild_plan(self):
        task = _build_task({"释放大招": False, "释放技能": False, "释放E技能": False})
        task._ensure_chars()
        char = task.chars[0]
        for item in task.chars:
            item.rebuild_plan()
        self.assertEqual([action.name for action in char.plan.actions], ["普攻"])

    def test_e_skill_registered_and_pressed(self):
        task = _build_task({}, frame=_battle_frame())
        task._ensure_chars()
        char = task.chars[0]
        char.rebuild_plan()
        self.assertIsNotNone(char.action("E技能"))
        now = task.active_time()
        for name in ("大招", "技能"):
            char._next_ready[name] = now  # 只有 E 可用
        task.press.reset_mock()
        char.execute(char.action("E技能"))
        self.assertEqual(task.press.call_args.args[0], profile.KEY_SKILL2)

    def test_yields_to_game_auto_and_disables_task(self):
        task = _build_task({})
        task.find_one = mock.Mock(return_value=object())
        task.combat_tick()
        task.combat_tick()
        task.disable.assert_called_once()
        self.assertIs(task.last_stop_reason, StopReason.YIELDED_TO_GAME)

    def test_capture_stop_presses_capture_key(self):
        task = _build_task({})
        task._end_session(StopReason.HP_THRESHOLD)
        task.press.assert_called_once_with(profile.KEY_CAPTURE, hold=0.05, after_sleep=0.2)
        self.assertIs(task.last_stop_reason, StopReason.HP_THRESHOLD)

    def test_character_factory_falls_back_to_generic(self):
        task = _build_task()
        self.assertIn("generic", character_ids())
        char = build_character(task, 0, "不存在的脚本")
        self.assertIsInstance(char, type(build_character(task, 0, "generic")))
        task.log_warning.assert_called()


class TestPlanner(unittest.TestCase):
    def _planner_task(self, config=None):
        task = _build_task(config)
        task._ensure_chars()
        for char in task.chars:
            char.rebuild_plan()
        return task

    def test_perform_picks_highest_priority_action(self):
        task = self._planner_task({})
        char = task.chars[0]
        task.press.reset_mock()
        result = task.planner.perform_current_char(char)
        self.assertTrue(result.success)
        self.assertEqual(task.press.call_args.args[0], profile.KEY_ULTIMATE)

    def test_identical_characters_rotate_to_use_ready_actions(self):
        # 语义：当前角色不参与评分，只要别人有可评分动作就可能切出去
        task = self._planner_task()
        decision = task.planner.decide_switch(task.chars[0])
        self.assertNotEqual(decision.target_index, 0)

    def test_keeps_current_when_no_candidate_has_scored_action(self):
        task = self._planner_task()
        now = task.active_time()
        for char in task.chars:
            for action in char.plan.actions:
                if action.scored:
                    char._next_ready[action.name] = now + 60
        decision = task.planner.decide_switch(task.chars[0])
        self.assertEqual(decision.target_index, 0)

    def test_tie_break_prefers_longest_idle(self):
        task = self._planner_task()
        now = task.active_time()
        task.chars[1].last_perform = now - 5
        task.chars[2].last_perform = now - 1
        decision = task.planner.decide_switch(task.chars[0])
        self.assertEqual(decision.target_index, 1)

    def test_entry_steps_run_in_order_and_block_switch(self):
        task = self._planner_task()
        char = task.chars[0]
        char.plan.entry_steps = ("技能", "E技能")
        char.reset_entry()

        first = task.planner.perform_current_char(char)
        self.assertTrue(first.success)
        self.assertEqual(task.planner.last_action.name, "技能")
        self.assertTrue(char.entry_active)
        # 连招没走完：不切人
        decision = task.planner.decide_switch(char)
        self.assertEqual(decision.target_index, char.index)

        second = task.planner.perform_current_char(char)
        self.assertTrue(second.success)
        self.assertEqual(task.planner.last_action.name, "E技能")
        self.assertFalse(char.entry_active)

    def test_entry_skips_unavailable_step(self):
        task = self._planner_task()
        char = task.chars[0]
        char.plan.entry_steps = ("技能", "E技能")
        char.reset_entry()
        now = task.active_time()
        char._next_ready["技能"] = now + 99
        step = char.next_entry_step(now)
        self.assertIsNotNone(step)
        self.assertEqual(step.name, "E技能")

    def test_strict_claim_forces_switch(self):
        task = self._planner_task()
        task.chars[1].claims = mock.Mock(return_value=[FieldClaim(FieldClaimLevel.STRICT, "测试强制切入")])
        decision = task.planner.decide_switch(task.chars[0])
        self.assertEqual(decision.target_index, 1)

    def test_high_claim_switches_support_in(self):
        task = self._planner_task()
        task.chars[2].claims = mock.Mock(return_value=[FieldClaim(FieldClaimLevel.HIGH, "技能好了")])
        decision = task.planner.decide_switch(task.chars[0])
        self.assertEqual(decision.target_index, 2)

    def test_route_executes_steps_in_order(self):
        task = self._planner_task()
        char = task.chars[0]
        context = char.combat_context
        context.request_route([RouteStep(target_index=None, action_name="技能", note="先手技能")])
        self.assertIsNotNone(task.planner.state.locked_route)
        result = task.planner.perform_current_char(char)
        self.assertTrue(result.success)
        self.assertIsNone(task.planner.state.locked_route)

    def test_route_switches_before_action(self):
        task = self._planner_task()
        char = task.chars[0]
        char.combat_context.request_route(
            [
                RouteStep(target_index=1, note="先切辅助"),
                RouteStep(target_index=None, action_name="技能", note="辅助技能"),
            ]
        )
        task.planner.perform_current_char(char)
        self.assertEqual(task.current_index, 1)

    def test_substitute_char_ignores_hud_ready_hint(self):
        """替补角色读不到技能按钮：当前角色 Q 在冷却，替补仍按“未知=可用”算。"""
        task = self._planner_task()
        task.slot_ready = mock.Mock(return_value=False)
        current, other = task.chars[0], task.chars[1]
        current.is_current_char = True
        other.is_current_char = False
        current._next_ready.clear()
        other._next_ready.clear()
        skill = current.action("技能")
        self.assertFalse(current.available(skill))
        self.assertTrue(other.available(skill))


if __name__ == "__main__":
    unittest.main()
