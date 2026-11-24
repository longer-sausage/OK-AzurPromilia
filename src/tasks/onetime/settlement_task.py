"""结算记账任务：读结算界面文字，把「物品 ×数量」记进 ``ledger.jsonl``。

为什么做成任务而不是纯 GUI 按钮：OCR 需要连上游戏、走框架的截图与识别管线，
放进任务体系才能复用暂停/停止、日志与异常处理；GUI 那边只提供**粘贴文本**记账。

配置项：

- `记录来源`：写进 ledger 的来源标记，方便以后区分「结算 / 手动 / 商店」
- `最低置信度`：OCR 置信度低于这个值的文本框直接丢弃（默认 0，即全收）
- `识别后清空画面`：保留项，占位给后续「一键清空」流程
"""

from __future__ import annotations

from src.core.base_game_task import BaseGameTask
from src.core.ledger import Ledger, SettlementFormatError, parse_settlement_text
from src.icons import Icons

DEFAULT_SOURCE = "结算界面"


class SettlementTask(BaseGameTask):
    """把结算界面的文字记成收支条目。"""

    support_schedule_task = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "结算记账"
        self.icon = Icons.Task
        self.description = (
            "识别结算界面（或任意物品列表界面）上的「物品 ×数量」，"
            "把收益记进 ledger.jsonl，并给出今日与累计收支。"
        )
        self.default_config = {
            "记录来源": DEFAULT_SOURCE,
            "最低置信度": 0.0,
        }
        self.config_description = {
            "记录来源": "写进记账本的来源标记，用来区分结算 / 手动 / 商店等场景",
            "最低置信度": "OCR 置信度低于该值的文本会被丢弃，0 表示全部保留",
        }
        self._ledger: Ledger | None = None

    # ── 依赖（测试可替换） ───────────────────────────────
    def ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger()
        return self._ledger

    def read_screen_text(self) -> str:
        """把当前画面按视觉行读成文本。"""
        min_confidence = float(self.config.get("最低置信度", 0.0) or 0.0)
        lines = self.ocr_lines(min_confidence=min_confidence)
        return "\n".join(lines)

    # ── 主流程 ───────────────────────────────────────────
    def run(self):
        source = str(self.config.get("记录来源", DEFAULT_SOURCE) or DEFAULT_SOURCE)
        text = self.read_screen_text()
        if not text.strip():
            self.log_info("画面上没有识别到文字", notify=True)
            return False
        return self.record_text(text, source=source)

    def record_text(self, text: str, source: str = DEFAULT_SOURCE) -> bool:
        """解析一段结算文字并写入记账本。"""
        try:
            entries = parse_settlement_text(text, source=source)
        except SettlementFormatError as exc:
            self.log_info(f"没有识别到「物品 ×数量」：{exc}", notify=True)
            return False

        written = self.ledger().record(entries)
        preview = "、".join(
            f"{entry.item}{'−' if entry.kind == 'expense' else '+'}{entry.amount:g}"
            for entry in entries[:6]
        )
        if len(entries) > 6:
            preview += f" 等 {len(entries)} 条"
        self.log_info(f"已记录 {written} 条（{source}）：{preview}", notify=True)

        totals = self.ledger().totals()
        today = self.ledger().today_summary()
        today_income = sum(item["income"] for item in today.values())
        today_expense = sum(item["expense"] for item in today.values())
        self.log_info(
            f"今日 收入 {today_income:g} / 支出 {today_expense:g}；"
            f"累计 收入 {totals['income']:g} / 支出 {totals['expense']:g} / 净 {totals['net']:g}"
        )
        return True
