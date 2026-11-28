"""战斗任务基类：观测 → 目标判定 → 调度 → 执行。

一拍只推进一个动作（不阻塞），对应 ok-ap 触发任务的轮询约定；
决策模型（角色计划 / 诉求 / 路线 / 评分切人）由 planner 统一实现。
"""

from __future__ import annotations

import re

from ok import Logger

from src.combat import profile
from src.combat.characters import build_character
from src.combat.detection import CombatChecker, CombatObservation
from src.combat.dungeon_gate import DungeonGate
from src.combat.enums import ActionSlot, CombatMode, StopReason
from src.combat.objectives import CaptureObjective, CombatObjective, FullAutoObjective
from src.combat.planner import CombatPlanner
from src.combat.recognition.interact import find_interact_prompt
from src.combat.recognition.skills import read_skill
from src.combat.types import SwitchDecision
from src.core.base_game_task import BaseGameTask

logger = Logger.get_logger(__name__)

MODE_LABELS = {
    "全自动": CombatMode.FULL_AUTO,
    "捉宠": CombatMode.CAPTURE,
}


class BattlePhase:
    OUTSIDE = "outside"
    INSIDE = "inside"


class BaseCombatTask(BaseGameTask):
    """自动战斗任务基类（不含具体角色脚本）。"""

    ERROR_LOG_INTERVAL = 10.0
    HEARTBEAT_INTERVAL = 10.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.phase = BattlePhase.OUTSIDE
        self.chars: list = []
        self.current_index = 0
        self.battle_count = 0
        self.battle_started_at = 0.0
        self.last_stop_reason: StopReason | None = None
        self.planner = CombatPlanner(self)
        self._checker: CombatChecker | None = None
        self._gate: DungeonGate | None = None
        self._objective: CombatObjective | None = None
        self._frame = None
        self._last_error_log_at = 0.0
        self._last_heartbeat_at = 0.0
        self._stop_latch = False
        self._slot_candidate: int | None = None
        self._slot_hits = 0
        self._was_retargeting = False
        self._game_hwnd: int | None = None
        self._last_interact_at = 0.0
        self._cd_cache: dict[str, tuple[float | None, float]] = {}

    # ── 配置 ──────────────────────────────────────────────

    def config_value(self, key: str, default):
        config = self.config
        if not config:
            return default
        try:
            return config.get(key, default)
        except AttributeError:  # pragma: no cover - 防御异常配置对象
            return default

    @property
    def combat_mode(self) -> CombatMode:
        label = str(self.config_value("_战斗模式", "全自动"))
        return MODE_LABELS.get(label, CombatMode.FULL_AUTO)

    # ── 组件（延迟构建，保证配置已加载）────────────────────

    @property
    def checker(self) -> CombatChecker:
        if self._checker is None:
            self._checker = CombatChecker(
                self.find_one,
                enter_frames=int(self.config_value("_进入战斗确认帧数", profile.ENTER_CONFIRM_FRAMES)),
                exit_frames=int(self.config_value("_退出战斗确认帧数", profile.EXIT_CONFIRM_FRAMES)),
                middle_click=self._combat_middle_click,
                uncertain_seconds=float(self.config_value("_不确定期(秒)", profile.EXIT_UNCERTAIN_SECONDS)),
                retarget_seconds=float(self.config_value("_索敌时长(秒)", profile.RETARGET_SECONDS)),
                click_interval=float(self.config_value("_索敌间隔(秒)", profile.RETARGET_CLICK_INTERVAL)),
                revive_frames=int(self.config_value("_续战确认帧数", profile.REVIVE_CONFIRM_FRAMES)),
                max_retargets=int(self.config_value("_索敌次数上限", profile.MAX_RETARGET_ROUNDS)),
                sweep_interval=float(self.config_value("_索敌扫描间隔(秒)", profile.TARGET_SWEEP_INTERVAL)),
                sweep_seconds=float(self.config_value("_索敌续战时长(秒)", profile.SWEEP_AFTER_EXIT_SECONDS)),
            )
        return self._checker

    def _combat_middle_click(self) -> None:
        """脱战前的中键索敌；只在游戏前台时按，避免抢鼠标。"""
        if not self._game_in_foreground():
            self.log_debug("游戏不在前台，跳过索敌中键")
            return
        self.middle_click()

    def _game_in_foreground(self) -> bool:
        """游戏窗口是否在前台；判断不了时按前台处理（避免功能直接失效）。"""
        try:
            from ok.alas.platform_windows import get_focused_window

            if self._game_hwnd is None:
                self._game_hwnd = self.get_game_hwnd() or 0
            if not self._game_hwnd:
                return True
            return get_focused_window() == self._game_hwnd
        except Exception:
            return True

    @property
    def gate(self) -> DungeonGate:
        if self._gate is None:
            self._gate = DungeonGate(
                self.find_one,
                enabled=bool(self.config_value("已解锁自动战斗时让给游戏", True)),
            )
        return self._gate

    @property
    def objective(self) -> CombatObjective:
        if self._objective is None:
            self._objective = self.create_objective()
        return self._objective

    def create_objective(self) -> CombatObjective:
        if self.combat_mode is CombatMode.CAPTURE:
            threshold = float(self.config_value("_捉宠血量阈值(%)", profile.CAPTURE_HP_THRESHOLD * 100)) / 100.0
            return CaptureObjective(hp_threshold=threshold)
        return FullAutoObjective()

    # ── 一拍 ──────────────────────────────────────────────

    def combat_tick(self, frame=None) -> bool:
        now = self.active_time()
        self._frame = frame if frame is not None else self.next_frame()
        if self._frame is None:
            self._log_heartbeat(now, None)
            return False
        if self.gate.should_yield(self._frame):
            self._yield_to_game_auto()
            return False

        observation = self.checker.observe(self._frame, now)
        if observation.retargeting and not self._was_retargeting:
            self.log_info("战斗信号丢失：进入不确定期，开始中键索敌")
        elif not observation.retargeting and self._was_retargeting:
            self.log_debug("索敌结束（信号恢复或判定脱战）")
        self._was_retargeting = observation.retargeting
        self._sync_current_slot(observation.current_slot)
        self._log_heartbeat(now, observation)

        if not observation.ended:
            self._maybe_interact(now)

        if self._stop_latch:
            if not observation.in_combat:
                self._stop_latch = False
            return False

        if self.phase == BattlePhase.INSIDE:
            reason = self.objective.should_stop(self, observation, now)
            if reason is not None:
                self._end_session(reason)
                return False

        if not observation.in_combat:
            return False

        if self.phase != BattlePhase.INSIDE:
            self._ensure_chars()
            self._begin_session(now)

        char = self.current_char
        if char is None:
            return False
        if char.is_dead:
            self._switch_to_alive()
            return False
        return self._perform_turn(char)

    def _perform_turn(self, char) -> bool:
        result = self.planner.perform_current_char(char)
        acted = self.planner.last_action
        # 刚放完技能/大招的这一拍不切人：等后摇结束，下一拍再评估切人
        if acted is not None and acted.slot in (ActionSlot.SKILL, ActionSlot.SKILL2, ActionSlot.ULTIMATE):
            return bool(result is not None and result.success)
        decision = self.planner.decide_switch(char)
        if decision.target_index is not None and decision.target_index != char.index:
            self.switch_to(decision, current=char)
        return bool(result is not None and result.success)

    # ── 会话 ──────────────────────────────────────────────

    def _begin_session(self, now: float) -> None:
        self.phase = BattlePhase.INSIDE
        self.battle_count += 1
        self.battle_started_at = now
        self.checker.reset()
        self.objective.reset()
        self.planner.reset(self.chars)
        for char in self.chars:
            char.reset()
            char.rebuild_plan()
        current = self.current_char
        if current is not None:
            current.is_current_char = True
        self.log_info(f"进入战斗（第 {self.battle_count} 场），模式={self.combat_mode}")
        decision = self.planner.decide_combat_start_char(current)
        if decision is not None:
            self.switch_to(decision, current=current)

    def _end_session(self, reason: StopReason) -> None:
        self.phase = BattlePhase.OUTSIDE
        self.last_stop_reason = reason
        self._stop_latch = True
        self.log_info(f"战斗结束：{reason}")
        self.objective.on_stop(self, reason)
        if reason is StopReason.HP_THRESHOLD and bool(self.config_value("_捉宠后按星结", True)):
            self.press(profile.KEY_CAPTURE, hold=0.05, after_sleep=0.2)

    def _yield_to_game_auto(self) -> None:
        self.last_stop_reason = StopReason.YIELDED_TO_GAME
        if self.phase == BattlePhase.INSIDE:
            self._end_session(StopReason.YIELDED_TO_GAME)
        self.log_info("检测到游戏自带自动战斗，停止本任务并交给游戏")
        self.disable()

    # ── 角色 ──────────────────────────────────────────────

    @property
    def current_char(self):
        if 0 <= self.current_index < len(self.chars):
            return self.chars[self.current_index]
        return None

    def _ensure_chars(self) -> None:
        if self.chars:
            return
        script = str(self.config_value("_角色脚本", "generic"))
        count = len(profile.KEY_SWAPS)
        self.chars = [build_character(self, index, script) for index in range(count)]
        self.chars[0].is_current_char = True
        self.current_index = 0
        self.planner.reset(self.chars)
        self.log_info(f"加载角色槽位：{count} 个（脚本 {script}）")

    def _switch_to_alive(self) -> None:
        for offset in range(1, len(self.chars) + 1):
            index = (self.current_index + offset) % len(self.chars)
            char = self.chars[index]
            if char is not None and not char.is_dead:
                self.switch_to(SwitchDecision(index, "当前角色阵亡", 500_000, skip_waits=True))
                return

    def switch_to(self, decision: SwitchDecision, current=None) -> bool:
        target_index = decision.target_index
        if target_index is None or not 0 <= target_index < len(self.chars):
            return False
        if target_index == self.current_index:
            return False
        target = self.chars[target_index]
        if target is None or target.is_dead:
            return False

        guard = target.switch_in_guard()
        if guard.should_delay():
            self.log_debug(f"切人延迟：{guard.reason}")
            return False

        now = self.active_time()
        current = current if current is not None else self.current_char
        self.press(profile.KEY_SWAPS[target_index], hold=0.05, after_sleep=0.1)

        if current is not None:
            current.switch_out()
        target.switch_in(now)
        self.current_index = target_index
        self.planner.state.record_switch(target_index, now)
        self.planner.state.set_expected_entry(target_index, decision.expected_entry)
        self.log_info(
            f"切人：{current.display_name if current is not None else '-'} -> {target.display_name}"
            f"（{decision.reason}）"
        )
        return True

    # ── 输入与就绪 ────────────────────────────────────────

    def press(self, key: str, hold: float = 0.02, after_sleep: float = 0.05) -> None:
        # 游戏不在前台时不要按键，否则会敲到别的窗口里
        if not self._game_in_foreground():
            self.log_debug(f"游戏不在前台，跳过按键 {key}")
            return
        if key == profile.KEY_ATTACK:
            self.click(down_time=hold, after_sleep=after_sleep)
        else:
            self.send_key(key, down_time=hold, after_sleep=after_sleep)

    def slot_cooldown_seconds(self, slot: ActionSlot) -> float | None:
        """读技能按钮上的冷却秒数。

        只在上场角色的按钮上有效；读不到数字时返回 None，由调用方用默认重试间隔。
        """
        region = profile.SKILL_REGIONS.get(slot.value)
        if region is None or self._frame is None:
            return None
        now = self.active_time()
        cached = self._cd_cache.get(slot.value)
        if cached is not None and now - cached[1] < 0.5:
            return cached[0]
        try:
            box = self.box_of_screen(*region)
            texts = self.ocr(box=box, frame=self._frame, match=r"\d{1,2}(?:\.\d)?")
        except Exception:
            return None
        for text in texts or []:
            found = re.search(r"\d+(?:\.\d)?", getattr(text, "name", "") or "")
            if found is None:
                continue
            try:
                value = float(found.group(0))
            except ValueError:
                continue
            self._cd_cache[slot.value] = (value, now)
            self.log_debug(f"冷却读数 {slot.value}={value}")
            return value
        self._cd_cache[slot.value] = (None, now)
        return None

    def slot_ready(self, slot: ActionSlot) -> bool | None:
        """技能/大招是否就绪；区域未校准或取不到帧时返回 None（未知）。"""
        if slot is ActionSlot.ATTACK:
            return None
        region = profile.SKILL_REGIONS.get(slot.value)
        if region is None or self._frame is None:
            return None
        reading = read_skill(
            self._frame,
            slot.value,
            region,
            band=profile.READY_RING_BAND,
            threshold=profile.READY_RING_COVERAGE,
            white_saturation_max=profile.READY_RING_WHITE_S_MAX,
            white_value_min=profile.READY_RING_V_MIN,
            cyan_hue=profile.READY_RING_CYAN_HUE,
            cyan_saturation_min=profile.READY_RING_CYAN_S_MIN,
            cyan_value_min=profile.READY_RING_CYAN_V_MIN,
        )
        return None if reading is None else reading.ready

    def _sync_current_slot(self, slot: int | None) -> None:
        """把识别到的当前槽位同步到本地账本（切人失败的兜底）。

        槽位识别口径：队伍栏里**淡色**编号徽章 = 当前角色（实机三张样本验证）。
        用配置 `_启用槽位识别` 控制，默认开启；误判时只会让本地账本短暂不一致，
        下一次成功切人会纠正。
        """
        if not bool(self.config.get("_启用槽位识别", True)):
            return
        if slot is None:
            self._slot_candidate = None
            self._slot_hits = 0
            return
        # 连续两帧识别到同一个槽位才采信：单帧误判（背景/特效）很常见
        if slot == self._slot_candidate:
            self._slot_hits += 1
        else:
            self._slot_candidate, self._slot_hits = slot, 1
        if self._slot_hits < 2:
            return
        if not self.chars or not 0 <= slot < len(self.chars):
            return
        if slot == self.current_index:
            return
        previous = self.current_char
        if previous is not None:
            previous.switch_out()
        self.current_index = slot
        self.chars[slot].is_current_char = True
        self.log_debug(f"识别到当前角色槽位：{slot + 1}")

    # ── 交互 ──────────────────────────────────────────────

    def _maybe_interact(self, now: float) -> bool:
        """看到可交互提示（F）就按 F；用冷却避免连点。"""
        if not bool(self.config_value("_自动交互", True)):
            return False
        cooldown = float(self.config_value("_交互冷却(秒)", 1.5))
        if now - self._last_interact_at < cooldown:
            return False
        frame = self._frame
        if frame is None:
            return False
        try:
            target = find_interact_prompt(frame)
        except Exception:
            return False
        if target is None:
            return False
        self._last_interact_at = now
        self.log_info("检测到交互提示（F），按 F")
        self.press("f", hold=0.02, after_sleep=0.1)
        return True

    # ── 可观测性 ──────────────────────────────────────────

    def _log_heartbeat(self, now: float, observation: CombatObservation | None) -> None:
        interval = float(self.config_value("_判据日志间隔(秒)", self.HEARTBEAT_INTERVAL))
        if interval <= 0 or now - self._last_heartbeat_at < interval:
            return
        self._last_heartbeat_at = now
        if observation is None:
            self.log_info(f"战斗判据心跳：阶段={self.phase} 取帧失败")
            return
        hp = "-" if observation.enemy_hp_ratio is None else f"{observation.enemy_hp_ratio:.2f}"
        slot = "-" if observation.current_slot is None else str(observation.current_slot + 1)
        self.log_info(
            f"战斗判据心跳：阶段={self.phase} 入战={observation.in_combat} "
            f"结算={observation.ended} 锁定={observation.lock_on} 血量={hp} 槽位={slot} "
            f"索敌={observation.retargeting}"
        )
