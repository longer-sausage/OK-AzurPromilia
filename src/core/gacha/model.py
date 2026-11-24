"""抽卡概率模型：保底层 + 伯努利层。

模型与 OneBST/GGanalysis 的 ``PityBernoulliModel`` 同构：

- **保底层**：第 i 抽出「最高稀有度」的概率 p(i)。本项目内置《蓝色星原：旅谣》
  社区数值：基础 0.8%，第 71 抽起每抽 +2 个百分点，第 81 抽起每抽 +8 个百分点，
  第 90 抽硬保底（p=100%）。对应 71 抽 2.8%、81 抽 28.8%、90 抽 100%。
- **伯努利层**：每次最高稀有度「是不是目标」是独立的 p=q。q=100% 表示必出
  （大保底）；q=50% 表示小保底。

由此可以严格算出：期望抽数、预算内完成概率、最坏情况。

**已知局限**：模型不追踪游戏里的「大保底状态」——需要模拟大保底时，请直接把
``target_rate`` 设成 1.0；需要从某个已垫抽数开始时，用 ``pity`` 参数。

游戏没有公开的官方概率表，内置数值按社区数据填写，请以游戏内公示为准；
也可以用 ``custom_rates`` 直接贴自己的概率表。
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field

MAX_PITY = 90
DEFAULT_HARD_PITY = 90
DEFAULT_BASE_RATE = 0.008
DEFAULT_SOFT_START_1 = 71
DEFAULT_SOFT_STEP_1 = 0.02
DEFAULT_SOFT_START_2 = 81
DEFAULT_SOFT_STEP_2 = 0.08
DEFAULT_TARGET_RATE = 1.0
PRECISION = 1e-12


@dataclass(frozen=True)
class GachaParams:
    """卡池参数。"""

    base_rate: float = DEFAULT_BASE_RATE
    hard_pity: int = DEFAULT_HARD_PITY
    soft_start_1: int = DEFAULT_SOFT_START_1
    soft_step_1: float = DEFAULT_SOFT_STEP_1
    soft_start_2: int = DEFAULT_SOFT_START_2
    soft_step_2: float = DEFAULT_SOFT_STEP_2
    custom_rates: Sequence[float] = field(default_factory=tuple)

    def __post_init__(self):
        if self.hard_pity < 1:
            raise ValueError("hard_pity 至少为 1")
        if self.custom_rates:
            rates = [float(rate) for rate in self.custom_rates]
            if len(rates) != self.hard_pity:
                raise ValueError(f"custom_rates 长度必须等于 hard_pity（{self.hard_pity}）")
            for rate in rates:
                if not 0.0 <= rate <= 1.0:
                    raise ValueError("概率必须在 0..1 之间")
            if rates[-1] < 1.0:
                raise ValueError("最后一抽必须是 100%（硬保底）")
            return
        if not 0.0 <= self.base_rate <= 1.0:
            raise ValueError("base_rate 必须在 0..1 之间")
        if not 1 <= self.soft_start_1 <= self.soft_start_2 <= self.hard_pity:
            raise ValueError("软保底起点必须满足 1 ≤ 起点1 ≤ 起点2 ≤ hard_pity")

    def rates(self) -> list[float]:
        """第 1..hard_pity 抽出最高稀有度的概率表。"""
        if self.custom_rates:
            return [float(rate) for rate in self.custom_rates]

        table = []
        for pull in range(1, self.hard_pity + 1):
            if pull < self.soft_start_1:
                rate = self.base_rate
            elif pull < self.soft_start_2:
                rate = self.base_rate + (pull - self.soft_start_1 + 1) * self.soft_step_1
            else:
                rate = (
                    self.base_rate
                    + (self.soft_start_2 - self.soft_start_1) * self.soft_step_1
                    + (pull - self.soft_start_2 + 1) * self.soft_step_2
                )
            table.append(min(1.0, rate))
        table[-1] = 1.0
        return table


@dataclass
class GachaResult:
    """一次规划的计算结果。"""

    expected_pulls: float
    probability: float
    worst_case: int | None
    budget: int
    copies: int
    target_rate: float
    pity: int
    truncated: bool = False
    rates: list[float] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"期望 {self.expected_pulls:.1f} 抽",
            f"{self.budget} 抽内完成 {self.probability * 100:.2f}%",
        ]
        if self.worst_case is not None:
            parts.append(f"最坏 {self.worst_case} 抽")
        else:
            parts.append("最坏情况不确定（目标概率 < 100%）")
        if self.truncated:
            parts.append("（预算过小，期望被截断）")
        return "，".join(parts)


class PityBernoulliModel:
    """保底层 + 伯努利层。"""

    def __init__(self, params: GachaParams | None = None, target_rate: float = DEFAULT_TARGET_RATE):
        self.params = params or GachaParams()
        if not 0.0 < target_rate <= 1.0:
            raise ValueError("target_rate 必须在 (0, 1] 之间")
        self.target_rate = float(target_rate)
        self._rates = self.params.rates()

    # ── 基础量 ───────────────────────────────────────────
    @property
    def rates(self) -> list[float]:
        return list(self._rates)

    def rate_at(self, pull: int) -> float:
        """从零垫开始，第 ``pull`` 抽出最高稀有度的概率（1 基）。"""
        if pull < 1:
            raise ValueError("pull 从 1 开始")
        if pull > self.params.hard_pity:
            return 1.0
        return self._rates[pull - 1]

    def first_hit_distribution(self, pity: int = 0) -> dict[int, float]:
        """从已垫 ``pity`` 抽开始，第一次出最高稀有度发生在第几次抽。

        返回 ``{抽数: 概率}``，抽数是「从现在算起还要抽几次」。
        """
        if pity < 0:
            raise ValueError("pity 不能是负数")
        if pity >= self.params.hard_pity:
            return {1: 1.0}

        distribution: dict[int, float] = {}
        survival = 1.0
        for offset in range(1, self.params.hard_pity - pity + 1):
            rate = self.rate_at(pity + offset)
            probability = survival * rate
            if probability > PRECISION:
                distribution[offset] = probability
            survival *= 1.0 - rate
            if survival <= PRECISION:
                break
        return distribution

    def expected_first_hit(self, pity: int = 0) -> float:
        return sum(pulls * probability for pulls, probability in self.first_hit_distribution(pity).items())

    # ── 目标（多份）分布 ─────────────────────────────────
    def finish_distribution(
        self,
        copies: int = 1,
        pity: int = 0,
        max_pulls: int | None = None,
    ) -> tuple[dict[int, float], float]:
        """集齐 ``copies`` 份目标的抽数分布。

        返回 ``(分布, 未完成概率)``；分布是 ``{抽数: 恰好在此时集齐的概率}``。
        """
        copies = max(1, int(copies))
        if max_pulls is None:
            max_pulls = self.worst_case(copies) or copies * self.params.hard_pity * 3
        max_pulls = max(1, int(max_pulls))

        q = self.target_rate
        # dp[(已完成份数, 当前垫抽)] = 概率
        dp: dict[tuple[int, int], float] = {(0, pity): 1.0}
        finished: dict[int, float] = {}
        hard = self.params.hard_pity

        for pull in range(1, max_pulls + 1):
            nxt: dict[tuple[int, int], float] = {}
            completed = 0.0
            for (done, current_pity), probability in dp.items():
                rate = self.rate_at(current_pity + 1)
                # 出最高稀有度且是目标
                if rate * q > 0:
                    hit = probability * rate * q
                    if done + 1 >= copies:
                        completed += hit
                    else:
                        nxt[(done + 1, 0)] = nxt.get((done + 1, 0), 0.0) + hit
                # 出最高稀有度但不是目标（歪了，垫抽清零）
                if rate * (1 - q) > 0:
                    miss = probability * rate * (1 - q)
                    nxt[(done, 0)] = nxt.get((done, 0), 0.0) + miss
                # 没出最高稀有度
                if rate < 1.0:
                    rest = probability * (1 - rate)
                    new_pity = min(current_pity + 1, hard - 1)
                    nxt[(done, new_pity)] = nxt.get((done, new_pity), 0.0) + rest
            if completed > PRECISION:
                finished[pull] = completed
            dp = {key: value for key, value in nxt.items() if value > PRECISION}
            if not dp:
                break

        unfinished = sum(dp.values())
        return finished, unfinished

    def expected_pulls(self, copies: int = 1, pity: int = 0, max_pulls: int | None = None) -> float:
        """集齐 ``copies`` 份的期望抽数（按截断分布归一）。"""
        distribution, _unfinished = self.finish_distribution(copies, pity, max_pulls)
        total = sum(distribution.values())
        if total <= 0:
            return float("nan")
        return sum(pulls * probability for pulls, probability in distribution.items()) / total

    def probability_within(self, budget: int, copies: int = 1, pity: int = 0) -> float:
        """``budget`` 抽内集齐 ``copies`` 份的概率。"""
        budget = max(0, int(budget))
        distribution, _unfinished = self.finish_distribution(copies, pity, budget)
        return sum(
            probability for pulls, probability in distribution.items() if pulls <= budget
        )

    def worst_case(self, copies: int = 1) -> int | None:
        """最坏情况抽数；目标概率 < 100% 时不存在有限上界。"""
        if self.target_rate >= 1.0:
            return max(1, int(copies)) * self.params.hard_pity
        return None

    # ── 汇总 ─────────────────────────────────────────────
    def plan(self, copies: int = 1, pity: int = 0, budget: int | None = None) -> GachaResult:
        """一次性算完期望 / 预算内概率 / 最坏情况。"""
        copies = max(1, int(copies))
        pity = max(0, min(int(pity), self.params.hard_pity - 1))
        if budget is None:
            budget = (self.worst_case(copies) or copies * self.params.hard_pity) * 2
        budget = max(1, int(budget))
        expected = self.expected_pulls(copies, pity)
        probability = self.probability_within(budget, copies, pity)
        _distribution, unfinished = self.finish_distribution(copies, pity, max_pulls=max(budget, 1))
        truncated = unfinished > 1e-6
        return GachaResult(
            expected_pulls=expected,
            probability=probability,
            worst_case=self.worst_case(copies),
            budget=budget,
            copies=copies,
            target_rate=self.target_rate,
            pity=pity,
            truncated=truncated,
            rates=self.rates,
        )


def parse_rate_table(text: str) -> list[float]:
    """把「2.8%, 28.8%, 100%」这类文本解析成概率表（支持逗号/空格/换行分隔）。

    校验比「能转成数字」严格一点，否则用户贴错一个数就会得到一个荒谬的模型：

    - 每项必须在 **0~1**（``150%``、``-5%`` 都拒绝，并指出是哪个值）；
    - 概率必须**单调不减**（抽得越多命中率不该下降）；
    - 最后一项必须是 **100%**（硬保底），否则模型会算出「永远抽不到」。
    """
    import re

    tokens = [token for token in re.split(r"[,，\s]+", str(text or "").strip()) if token]
    rates = []
    for token in tokens:
        percent = token.endswith("%")
        raw = token.rstrip("%").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"无法解析概率: {token}") from exc
        rate = value / 100.0 if percent or value > 1 else value
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"概率必须在 0%~100% 之间: {token}")
        rates.append(rate)
    if not rates:
        raise ValueError("概率表是空的")
    for previous, current in itertools.pairwise(rates):
        if current < previous:
            raise ValueError(f"概率必须单调不减: {previous * 100:g}% → {current * 100:g}%")
    if abs(rates[-1] - 1.0) > 1e-9:
        raise ValueError(f"最后一项必须是 100%（硬保底），当前 {rates[-1] * 100:g}%")
    return rates
