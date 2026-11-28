"""蓝色星原战斗侧的游戏事实：按键、区域、颜色阈值与默认节奏。

已用 12 张 1920x1080 实机截图校准的部分：
- 技能按钮区域与「外圈蓝环覆盖率」阈值（整圈=就绪 >=0.50）；
- 敌人血条：小怪/精英浮在画面中部，Boss 是顶部大条，同一套检测都能接；
- 当前角色：队伍栏里**淡色**编号徽章（V-0.7*S 最大）；
- 锁定标记：白星模板 + 红色柔光双重校验（锁定 2/2 命中，未锁定 0 误报）。
"""

from src.data.feature_list import FeatureList

# ── 按键（Q/E/R/左键/1-3 已实机验证；G 待验证）────────────────
KEY_ATTACK = "left"  # 鼠标左键：普攻
KEY_SKILL = "q"  # 技能（右下角 Q）
KEY_SKILL2 = "e"  # 副技能（右下角 E）
KEY_ULTIMATE = "r"  # 大招（右下角 R）
KEY_SWAPS = ("1", "2", "3")  # 切人：三个出战槽位
KEY_CAPTURE = "g"  # 星结（捉宠）

# ── 战斗区域 ──────────────────────────────────────────────────
#: 敌人血条搜索区：实机截图显示小怪/精英血条浮在画面中部
ENEMY_HP_REGION = (0.04, 0.05, 0.90, 0.80)
#: 血条筛选：薄横条（排除粗红色块/特效）
HP_MIN_WIDTH_RATIO = 0.02  # 相对搜索区宽度
HP_MIN_WIDTH_PX_RATIO = 0.03  # 相对整帧宽度：过窄的碎片直接丢弃
HP_MIN_HEIGHT_PX = 2
HP_MAX_HEIGHT_RATIO = 0.02  # 相对整帧高度（1080p 下 21px）
HP_MIN_ASPECT = 6.0
#: 多目标时优先取离画面中心最近的一条
HP_PREFER_CENTER = True

#: 技能按钮区域（1920x1080 实机截图，Q / E / R 三个圆形按钮外框）
SKILL_REGIONS: dict[str, tuple[float, float, float, float]] = {
    "skill": (0.840, 0.892, 0.872, 0.948),
    "skill2": (0.892, 0.898, 0.924, 0.954),
    "ultimate": (0.943, 0.898, 0.975, 0.954),
}
#: 技能就绪判据：按钮外圈蓝环的角度覆盖率（整圈=就绪，弧=冷却）
READY_RING_BAND = (0.40, 0.50)  # 相对按钮框（0~0.5 为半径）
READY_RING_COVERAGE = 0.50  # 实测：就绪 Q 0.78 / R 0.61；冷却 0.31~0.36
READY_RING_WHITE_S_MAX = 100  # 环的高光：近白
READY_RING_V_MIN = 190
READY_RING_CYAN_HUE = (80, 135)  # 环的冷色部分：青/蓝
READY_RING_CYAN_S_MIN = 100
READY_RING_CYAN_V_MIN = 200

#: 队伍槽位的编号徽章（1920x1080 实测：x 1840~1876，y 每行间隔 85px）
SLOT_REGIONS = (
    (0.9583, 0.1991, 0.9771, 0.2324),
    (0.9583, 0.2778, 0.9771, 0.3111),
    (0.9583, 0.3565, 0.9771, 0.3898),
)
#: 当前角色 = 淡色徽章（实测：V - 0.7*S 最大且领先 >=4）
SLOT_SATURATION_WEIGHT = 0.7
SLOT_ACCEPT_MARGIN = 4.0

# ── 颜色阈值 ──────────────────────────────────────────────────
#: 敌人血条红色（OpenCV H 0~179，红色跨 0 边界，所以两段）
ENEMY_HP_HSV_RANGES = (
    ((0, 130, 80), (10, 255, 255)),
    ((168, 130, 80), (179, 255, 255)),
)
#: 血条空槽（深灰）
HP_TRACK_VALUE_MIN = 20
HP_TRACK_VALUE_MAX = 130
HP_TRACK_SATURATION_MAX = 70
#: 锁定标记：被锁定的怪身上出现「白色四角星 + 红色柔光」，用模板 + 红光双重校验
LOCK_ENABLED = True
LOCK_REGION = (0.10, 0.12, 0.88, 0.85)
#: 白星模板匹配分数与红光占比阈值（实机：锁定 0.764~0.815/红光 0.10~0.17；未锁定 ≤0.686/红光 0）
LOCK_STAR_SCORE_MIN = 0.72
LOCK_STAR_RED_MIN = 0.05
#: 颜色块标记判据（保留 API；当前锁定走星形模板）
LOCK_HSV_RANGES = (((45, 120, 120), (95, 255, 255)),)

# ── 判据特征（复用仓库已有模板）─────────────────────────────────
#: 游戏自带自动战斗的 UI 特征：出现即说明「已解锁自动战斗」
GAME_AUTO_FEATURES = (FeatureList.auto_combat_check, FeatureList.auto_combat_setting)
#: 结算/返回按钮
BATTLE_END_FEATURES = (FeatureList.combat_button_return,)

# ── 默认节奏 ──────────────────────────────────────────────────
ENTER_CONFIRM_FRAMES = 2  # 连续多少帧命中才算进入战斗
EXIT_CONFIRM_FRAMES = 3  # 连续多少帧失去信号才进入"不确定期"
#: 脱战判定
EXIT_UNCERTAIN_SECONDS = 0.25  # 不确定期：仍算在战斗，等信号回来
RETARGET_SECONDS = 1.0  # 中键索敌时长：期间每 0.3 秒按一次中键
RETARGET_CLICK_INTERVAL = 0.3
REVIVE_CONFIRM_FRAMES = 2  # 信号要连续几帧回来才算"真的还在战斗"（防单帧闪烁）
MAX_RETARGET_ROUNDS = 3  # 一场战斗最多索敌几轮，超过就脱战（防无限循环）
TARGET_SWEEP_INTERVAL = 1.0  # 脱战后每隔多久按一次中键扫描敌人
SWEEP_AFTER_EXIT_SECONDS = 3.0  # 扫描只持续这么久，之后不再按中键（避免抢鼠标）
MIN_SESSION_SECONDS = 1.0  # 进战后最短时长：避免刚进战因血条闪一下立刻判脱战
HP_CONFIRM_FRAMES = 2  # 捉宠阈值连续多少帧满足才停手
ATTACK_INTERVAL = 0.05  # 普攻最小间隔（秒）
SKILL_COOLDOWN = 8.0  # 技能节流（秒）
SKILL2_COOLDOWN = 8.0  # 副技能（E）节流（秒）
ULTIMATE_COOLDOWN = 20.0  # 大招节流（秒）
SWITCH_COOLDOWN = 3.0  # 切人冷却（秒）：是 0.9，但那建立在"换人消耗环合值"上；
# 本作换人无代价，0.9 会切得太频繁，实测取 6.0，可在配置里调。
CAPTURE_HP_THRESHOLD = 0.20  # 捉宠阈值：血量比例 <= 该值就停手
SESSION_TIMEOUT = 600.0  # 单场战斗上限（秒）
