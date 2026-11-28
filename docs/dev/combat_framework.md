# 自动战斗框架

自动战斗的代码都在 `src/combat/`，入口是 `src/tasks/trigger/auto_combat_task.py`。

## 1. 两种模式

| 模式 | 战斗逻辑 | 终止条件 |
|---|---|---|
| 全自动 | 完全相同 | 脱战 / 结算 / 超时（默认 600s） |
| 捉宠 | 完全相同 | 敌人血量比例连续 N 帧 ≤ 阈值 → 停手（可自动星结）→ 按脱战处理 |

两种模式**只差一个 `CombatObjective`**，检测、调度、角色、动作全部共用。

## 2. 副本门禁

- 游戏自带自动战斗**未解锁** → 本模块接管；
- **已解锁**（检测到 `auto_combat_check` / `auto_combat_setting`）→ 日志提示后 `disable()` 自己。

## 3. 架构

```
src/combat/
  profile.py          游戏事实：按键 / 区域 / 颜色 / 节奏（唯一适配点）
  detection.py        入战 / 脱战状态机（多信号 + 中键索敌）
  objectives.py       终止条件：全自动 / 捉宠
  dungeon_gate.py     副本门禁
  enums.py            角色定位 / 动作标签 / 权重表
  types.py            动作、计划、诉求、切人决策等数据模型
  requests.py         路线 / 保留 / 切人 三种协作请求
  state.py            请求队列、锁定路线、切入期望
  context.py          角色侧 API
  planner.py          这一拍做什么 + 切给谁
  character.py        角色基类：动作表 / 诉求 / 出招表 / 执行
  characters/         角色脚本（generic + 示例）
  base_combat_task.py 串联：观测 → 目标 → 调度 → 执行
  recognition/        识别层（血条 / 锁定 / 技能就绪 / 当前角色 / 模板库）
```

一次 tick：

```
combat_tick()
  frame = next_frame()                       # 截图
  checker.observe(frame, now)                # 识别 + 入战/脱战
  objective.should_stop(...)                 # 脱战 / 结算 / 超时 / 血量阈值
  planner.perform_current_char(char)         # 路线 → 出招表 → 评分出招
  planner.decide_switch(char)                # 五层切人
  press(key)                                 # 键鼠（前台校验）
```

## 4. 入战 / 脱战

信号：敌人血条、锁定标记、游戏 auto UI。任一成立即可视为在战斗。

```
OUT ──信号连续 2 帧──▶ IN ──信号丢失 3 帧──▶ UNCERTAIN(0.25s，按一次中键)
                                              │
                            ┌─────────────────┘
                            ▼
                     RETARGET(1s，每 0.3s 中键)──信号连续 2 帧──▶ IN
                            │
                            └─超时 / 超过 3 轮──▶ OUT（扫描窗口 3s，每 1s 中键）
```

- 结算特征出现立即脱战；
- 续战要连续 2 帧确认，避免单帧闪烁；
- 脱战后的扫描只在 5 秒窗口内进行，且只有游戏窗口在前台时才按键。

## 5. 切人决策

| 顺序 | 层 | 优先级 |
|---|---|---|
| 1 | 锁定路线（`request_route`） | 1,000,000 |
| 2 | STRICT 诉求（`FieldClaim(STRICT)`） | 999,999 |
| 3 | 切人请求（`request_switch`） | 900,000 |
| 4 | 普通评分 | 动态 |

评分：

```
候选得分 = 最佳"可评分动作"的标签分
         + 最高诉求分
         + 站场偏好（有请求 -80/-20/0/-60；无请求 120/40/-40/-80；当前主C +60）
         + 保留分 150
         - 1000（该角色在切人冷却内刚被切入过）
当前角色不参与比较，兜底 -999999；同分选 last_perform 更早者
```

| 权重 | 值 |
|---|---|
| 动作标签 | 大招 200 / 强优先 160 / 技能 75 / 辅助 45 / 伤害 35 / 默认 10 |
| 诉求 | LOW 120 / NORMAL 240 / HIGH 420 / CRITICAL 700 |
| 站场偏好 | 主C 120 / 副C 40 / 辅助 -40 / 只做开场 -80 |

普攻不参与评分（`ActionSpec.scored=False`），只在站场时兜底执行。

## 6. 出招表

角色可声明上场后的动作顺序：

```python
plan.entry_steps = ("E技能", "技能", "大招")
```

- 一拍推进一步，不阻塞；不可用步骤自动跳过；
- 走完或剩余步骤全部不可用 → 回到普通评分；
- **连招期间不切人**（只有路线 / STRICT 可打断）；
- 每次切入重置游标；
- 未声明 `entry_steps` 的角色行为不变。

## 7. 角色脚本

```python
class MyChar(BaseCombatChar):
    char_id = "my_char"
    cn_name = "某角色"

    def describe_role(self):
        return RoleProfile(role=Role.MAIN_DPS, combat_start_priority=10, max_field_time=2.0)

    def combat_plan(self):
        plan = super().combat_plan()          # 技能 / E技能 / 大招 / 普攻
        plan.register(ActionSpec(
            name="二段技能",
            slot=ActionSlot.SKILL,
            tags=frozenset({ActionTag.SKILL_ACTION}),
            priority=120, key="e", cooldown=2.0,
            can_execute=lambda: self.task.find_one(FeatureList.xxx) is not None,
        ))
        plan.entry_steps = ("E技能", "技能", "大招")
        return plan

    def claims(self):
        return [FieldClaim(FieldClaimLevel.HIGH, "大招就绪")]

    def switch_in_guard(self):
        return SwitchInGuard.allow()          # 或 delay_until_ready(...)
```

注册：在 `src/combat/characters/__init__.py` 的 `CHARACTER_CLASSES` 里加一行。

## 8. 配置项

界面可见 5 项：

| 键 | 默认 | 说明 |
|---|---|---|
| 释放大招 / 释放技能 / 释放E技能 / 自动普攻 | 开 | 控制动作表（R / Q / E / 左键） |
| 已解锁自动战斗时让给游戏 | 开 | 副本门禁 |

其余为隐藏项（下划线前缀）：

| 键 | 默认 | 说明 |
|---|---|---|
| `_战斗模式` | 全自动 | 全自动 / 捉宠 |
| `_捉宠后按星结` / `_捉宠血量阈值(%)` | 开 / 20 | 捉宠模式 |
| `_技能冷却(秒)` / `_副技能冷却(秒)` / `_大招冷却(秒)` | 8 / 8 / 20 | 兜底冷却，真实 CD 以按钮 OCR 为准 |
| `_切人冷却(秒)` / `_普攻间隔(秒)` | 3 / 0.35 | 切人评分窗口 / 普攻最小间隔 |
| `_就绪重试间隔(秒)` | 2 | 按钮没好且读不到 CD 数字时的封锁 |
| `_进入战斗确认帧数` / `_退出战斗确认帧数` | 2 / 3 | 入战 / 进入不确定期 |
| `_不确定期(秒)` / `_索敌时长(秒)` / `_索敌间隔(秒)` | 0.25 / 1.0 / 0.3 | 脱战前的中键索敌 |
| `_续战确认帧数` / `_索敌次数上限` | 2 / 3 | 防闪烁 / 防无限索敌 |
| `_索敌扫描间隔(秒)` / `_索敌续战时长(秒)` | 1.0 / 3.0 | 脱战后扫描（结算界面不扫描） |
| `_自动交互` / `_交互冷却(秒)` | 开 / 1.5 | 出现 F 交互提示时自动按 F |
| `_判据日志间隔(秒)` | 10 | 心跳 |
| `_角色脚本` | generic | 角色脚本 id |

## 9. 校准结果（12 张 1920×1080 实机截图）

| 判据 | 结果 |
|---|---|
| 敌人血条 | 6 张战斗图全部命中，6 张非战斗图 0 误报；小怪 / 精英 / Boss 三种血条都接 |
| 技能就绪 | 就绪 0.6~0.8 / 冷却 0.1~0.4，阈值 0.5 可分 |
| 当前角色 | 切前=1、切后=2、idle=1 三张验证通过 |
| 锁定标记 | 锁定 2/2 命中，未锁定 10/10 不命中 |
| 副本门禁 | 未开 auto 0 命中；开启后 0.806 / 0.954 |
