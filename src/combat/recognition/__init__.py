"""识别基础设施。

分层：

- ``filters``    纯图像处理：二值化、颜色掩膜、掩角、形态学、Lab 对手色；
- ``regions``    相对坐标区域与多分辨率锚点换算；
- ``templates``  模板库：加载/缩放/预处理/匹配/probe 得分；
- ``hp``         敌人血条与血量比例；
- ``markers``    锁定标记等点状标识；
- ``skills``     技能按钮就绪（外圈蓝环覆盖率）；
- ``slots``      队伍槽位与当前角色识别（淡色编号徽章）；
- ``report``     一次识别所有信号，供调试与校准。

所有阈值/区域/颜色都在 ``src/combat/profile.py``，这里不含游戏常量。
"""

from src.combat.recognition.filters import (
    active_color_mask,
    binarize_brightness,
    color_mask,
    lab_opponent,
    mask_corners,
    morphology,
)
from src.combat.recognition.hp import HealthBarReading, read_health_bar
from src.combat.recognition.interact import (
    find_interact_prompt,
    load_interact_template,
    probe_interact_prompt,
)
from src.combat.recognition.markers import (
    LockStarReading,
    MarkerReading,
    find_lock_star,
    load_lock_star,
    probe_lock_star,
    read_marker,
)
from src.combat.recognition.regions import Region, scale_anchor
from src.combat.recognition.report import RecognitionReport, recognize
from src.combat.recognition.skills import SkillReading, read_skill
from src.combat.recognition.slots import SlotDetection, badge_scores, detect_current_slot
from src.combat.recognition.templates import Match, TemplateLibrary, TemplateSpec

__all__ = [
    "HealthBarReading",
    "LockStarReading",
    "MarkerReading",
    "Match",
    "RecognitionReport",
    "Region",
    "SkillReading",
    "SlotDetection",
    "TemplateLibrary",
    "TemplateSpec",
    "active_color_mask",
    "badge_scores",
    "binarize_brightness",
    "color_mask",
    "detect_current_slot",
    "find_interact_prompt",
    "find_lock_star",
    "lab_opponent",
    "load_interact_template",
    "load_lock_star",
    "mask_corners",
    "morphology",
    "probe_interact_prompt",
    "probe_lock_star",
    "read_health_bar",
    "read_marker",
    "read_skill",
    "recognize",
    "scale_anchor",
]
