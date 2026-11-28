"""一次识别所有战斗信号，供检测层与校准调试共用。"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.combat import profile
from src.combat.recognition.hp import HealthBarReading, read_health_bar
from src.combat.recognition.markers import LockStarReading, MarkerReading, find_lock_star
from src.combat.recognition.skills import SkillReading, read_skill
from src.combat.recognition.slots import SlotConfig, SlotDetection, detect_current_slot


@dataclass(frozen=True, slots=True)
class RecognitionReport:
    """一帧的全部识别结果。"""

    frame_ok: bool
    health: HealthBarReading | None = None
    lock: LockStarReading | MarkerReading | None = None
    skills: dict[str, SkillReading] = field(default_factory=dict)
    slot: SlotDetection | None = None
    result_visible: bool = False
    game_auto: bool = False

    def summary(self) -> str:
        hp = "-" if self.health is None else f"{self.health.fill_ratio:.2f}"
        slot = "-" if self.slot is None or not self.slot.accepted else str(self.slot.index + 1)
        skills = " ".join(f"{name}={reading.ready_ratio:.3f}" for name, reading in self.skills.items())
        return (
            f"血量={hp} 锁定={bool(self.lock)} 槽位={slot} "
            f"技能[{skills or '-'}] 结算={self.result_visible} auto={self.game_auto}"
        )


def feature_visible(find, features, frame=None) -> bool:
    """任一特征可见；判据缺失/识别异常按不可见处理。"""
    if find is None:
        return False
    for feature in features:
        try:
            if find(feature, frame=frame):
                return True
        except Exception:
            continue
    return False


def slot_config() -> SlotConfig:
    return SlotConfig(
        saturation_weight=profile.SLOT_SATURATION_WEIGHT,
        accept_margin=profile.SLOT_ACCEPT_MARGIN,
    )


def recognize(frame, *, find=None, ocr=None) -> RecognitionReport:
    """跑一帧全部识别。``find`` 为框架特征查询（结算/游戏 auto），可为 None。"""
    if frame is None or getattr(frame, "size", 0) == 0:
        return RecognitionReport(frame_ok=False)

    health = read_health_bar(
        frame,
        profile.ENEMY_HP_REGION,
        profile.ENEMY_HP_HSV_RANGES,
        min_width_ratio=profile.HP_MIN_WIDTH_RATIO,
        min_width_px_ratio=profile.HP_MIN_WIDTH_PX_RATIO,
        min_height_px=profile.HP_MIN_HEIGHT_PX,
        max_height_ratio=profile.HP_MAX_HEIGHT_RATIO,
        min_aspect=profile.HP_MIN_ASPECT,
        prefer_center=profile.HP_PREFER_CENTER,
    )
    lock = (
        find_lock_star(
            frame,
            profile.LOCK_REGION,
            score_min=profile.LOCK_STAR_SCORE_MIN,
            red_min=profile.LOCK_STAR_RED_MIN,
        )
        if profile.LOCK_ENABLED
        else None
    )

    skills: dict[str, SkillReading] = {}
    for slot, region in profile.SKILL_REGIONS.items():
        reading = read_skill(
            frame,
            slot,
            region,
            band=profile.READY_RING_BAND,
            threshold=profile.READY_RING_COVERAGE,
            white_saturation_max=profile.READY_RING_WHITE_S_MAX,
            white_value_min=profile.READY_RING_V_MIN,
            cyan_hue=profile.READY_RING_CYAN_HUE,
            cyan_saturation_min=profile.READY_RING_CYAN_S_MIN,
            cyan_value_min=profile.READY_RING_CYAN_V_MIN,
            ocr=ocr,
        )
        if reading is not None:
            skills[slot] = reading

    slot_detection = detect_current_slot(frame, profile.SLOT_REGIONS, slot_config())

    return RecognitionReport(
        frame_ok=True,
        health=health,
        lock=lock,
        skills=skills,
        slot=slot_detection,
        result_visible=feature_visible(find, profile.BATTLE_END_FEATURES, frame),
        game_auto=feature_visible(find, profile.GAME_AUTO_FEATURES, frame),
    )
