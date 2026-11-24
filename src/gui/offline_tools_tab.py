"""「离线工具」页：养成计算 / 抽卡分析 / 记账与报表。

三块都不发键鼠、不截图，只做计算与文件读写，所以可以安全地在界面线程里跑。
「识别结算画面」这种需要 OCR 的动作放在任务里（任务列表里的「结算记账」），
这里只接收粘贴进来的文字，避免和正在运行的任务抢截图。
"""

from __future__ import annotations

from pathlib import Path

from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    TextEdit,
)

from src.core.gacha import GachaParams, PityBernoulliModel, parse_rate_table
from src.core.growth import (
    GROWTH_KINDS,
    Dataset,
    GrowthDataError,
    GrowthInputError,
    Target,
    compute_plan,
    export_csv,
    export_png,
    import_dataset,
    material_rows,
)
from src.core.growth.table_import import import_table
from src.core.ledger import Ledger, RunLog, SettlementFormatError, parse_settlement_text

PREBUILD_DELAY_MS = 3000
DETAIL_HEIGHT = 170
KIND_LABELS = {
    "character": "角色",
    "pet": "奇波",
    "spirit": "灵子",
    "equipment": "装备",
}


class OfflineToolsTab(CustomTab):
    """养成计算 + 抽卡分析 + 记账报表。"""

    def __init__(self):
        super().__init__()
        self._built = False
        self._build_scheduled = False
        self._dataset: Dataset | None = None
        self._dataset_error = ""
        self._entry_keys: list[str] = []
        self._targets: list[Target] = []
        self._plan = None

        self.entry_combo: ComboBox | None = None
        self.level_from: SpinBox | None = None
        self.level_to: SpinBox | None = None
        self.copies_box: SpinBox | None = None
        self.potential_box: SpinBox | None = None
        self.star_box: SpinBox | None = None
        self.inventory_edit: TextEdit | None = None
        self.target_label: BodyLabel | None = None
        self.growth_detail: QPlainTextEdit | None = None

        self.copies_gacha: SpinBox | None = None
        self.pity_gacha: SpinBox | None = None
        self.budget_gacha: SpinBox | None = None
        self.target_rate_combo: ComboBox | None = None
        self.rates_edit: LineEdit | None = None
        self.gacha_detail: QPlainTextEdit | None = None

        self.settlement_edit: TextEdit | None = None
        self.ledger_detail: QPlainTextEdit | None = None

        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._schedule_build)

    # ── CustomTab 元信息 ─────────────────────────────────
    @property
    def name(self):
        return self.tr("离线工具")

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return True

    @property
    def icon(self):
        return FluentIcon.CALORIES

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_build()
        if self._built:
            self.refresh_ledger_view()

    # ── 构建 ─────────────────────────────────────────────
    def _schedule_build(self):
        if self._build_scheduled:
            return
        self._build_scheduled = True
        QTimer.singleShot(0, self, self._build)

    def _build(self):
        if self._built:
            return
        self._built = True
        self.add_card(self.tr("养成计算"), self._build_growth_card())
        self.add_card(self.tr("抽卡分析"), self._build_gacha_card())
        self.add_card(self.tr("记账与报表"), self._build_ledger_card())
        QTimer.singleShot(0, self.refresh_growth_entries)

    def _build_growth_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("养成目标")))
        self.entry_combo = ComboBox()
        self.entry_combo.setMinimumWidth(260)
        row.addWidget(self.entry_combo, 1)

        row.addWidget(BodyLabel(self.tr("起始等级")))
        self.level_from = SpinBox()
        self.level_from.setRange(1, 200)
        self.level_from.setValue(1)
        row.addWidget(self.level_from)

        row.addWidget(BodyLabel(self.tr("目标等级")))
        self.level_to = SpinBox()
        self.level_to.setRange(1, 200)
        self.level_to.setValue(80)
        row.addWidget(self.level_to)

        row.addWidget(BodyLabel(self.tr("份数")))
        self.copies_box = SpinBox()
        self.copies_box.setRange(1, 20)
        self.copies_box.setValue(1)
        row.addWidget(self.copies_box)

        row.addWidget(BodyLabel(self.tr("潜能等级")))
        self.potential_box = SpinBox()
        self.potential_box.setRange(0, 9)
        row.addWidget(self.potential_box)

        row.addWidget(BodyLabel(self.tr("升星")))
        self.star_box = SpinBox()
        self.star_box.setRange(0, 6)
        row.addWidget(self.star_box)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        for text, icon, slot in (
            (self.tr("添加目标"), FluentIcon.ADD, self._add_target),
            (self.tr("清空目标"), FluentIcon.DELETE, self._clear_targets),
            (self.tr("计算"), FluentIcon.SPEED_HIGH, self._compute_growth),
            (self.tr("导出 CSV"), FluentIcon.SAVE, lambda: self._export_growth("csv")),
            (self.tr("导出 PNG"), FluentIcon.PHOTO, lambda: self._export_growth("png")),
            (self.tr("导入数据"), FluentIcon.DOWNLOAD, self._import_growth_data),
            (self.tr("重新载入数据"), FluentIcon.SYNC, self.refresh_growth_entries),
        ):
            button = PushButton(icon, text)
            button.clicked.connect(slot)
            row2.addWidget(button)
        row2.addStretch(1)
        layout.addLayout(row2)

        self.target_label = BodyLabel(self.tr("还没有目标：选好参数后点「添加目标」"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        inventory_row = QHBoxLayout()
        inventory_row.setSpacing(6)
        inventory_row.addWidget(BodyLabel(self.tr("库存")))
        self.inventory_edit = TextEdit()
        self.inventory_edit.setFixedHeight(64)
        self.inventory_edit.setPlaceholderText(self.tr("每行一个：材料=数量，例如 尤尼=20000"))
        inventory_row.addWidget(self.inventory_edit, 1)
        layout.addLayout(inventory_row)

        self.growth_detail = QPlainTextEdit()
        self.growth_detail.setReadOnly(True)
        self.growth_detail.setFixedHeight(DETAIL_HEIGHT)
        layout.addWidget(self.growth_detail)
        return card

    def _build_gacha_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel(self.tr("目标份数")))
        self.copies_gacha = SpinBox()
        self.copies_gacha.setRange(1, 6)
        self.copies_gacha.setValue(1)
        row.addWidget(self.copies_gacha)

        row.addWidget(BodyLabel(self.tr("已垫抽数")))
        self.pity_gacha = SpinBox()
        self.pity_gacha.setRange(0, 89)
        row.addWidget(self.pity_gacha)

        row.addWidget(BodyLabel(self.tr("预算抽数")))
        self.budget_gacha = SpinBox()
        self.budget_gacha.setRange(1, 2000)
        self.budget_gacha.setValue(180)
        row.addWidget(self.budget_gacha)

        row.addWidget(BodyLabel(self.tr("出目标概率")))
        self.target_rate_combo = ComboBox()
        self.target_rate_combo.addItems(["100%（大保底）", "50%（小保底）"])
        row.addWidget(self.target_rate_combo)

        row.addWidget(BodyLabel(self.tr("自定义概率表")))
        self.rates_edit = LineEdit()
        self.rates_edit.setPlaceholderText(self.tr("可选：2.8%,28.8%,100% 这样的表，长度需等于硬保底"))
        row.addWidget(self.rates_edit, 1)

        calc = PrimaryPushButton(FluentIcon.SPEED_HIGH, self.tr("计算"))
        calc.clicked.connect(self._compute_gacha)
        row.addWidget(calc)
        layout.addLayout(row)

        self.gacha_detail = QPlainTextEdit()
        self.gacha_detail.setReadOnly(True)
        self.gacha_detail.setFixedHeight(DETAIL_HEIGHT)
        layout.addWidget(self.gacha_detail)
        return card

    def _build_ledger_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(
            BodyLabel(
                self.tr(
                    "把结算界面的文字粘进来记账；要直接识别画面，请在任务列表运行「结算记账」。"
                )
            )
        )
        self.settlement_edit = TextEdit()
        self.settlement_edit.setFixedHeight(90)
        self.settlement_edit.setPlaceholderText(
            self.tr("例如：获得的奖励：尤尼 ×1,500 / 12 × 波力果 / 消耗：星脉结晶 ×3")
        )
        layout.addWidget(self.settlement_edit)

        row = QHBoxLayout()
        row.setSpacing(6)
        for text, icon, slot in (
            (self.tr("记账"), FluentIcon.ADD, self._record_settlement),
            (self.tr("刷新统计"), FluentIcon.SYNC, self.refresh_ledger_view),
            (self.tr("导出记账 CSV"), FluentIcon.SAVE, lambda: self._export_ledger("ledger")),
            (self.tr("导出报表 CSV"), FluentIcon.SAVE, lambda: self._export_ledger("runs")),
        ):
            button = PushButton(icon, text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self.ledger_detail = QPlainTextEdit()
        self.ledger_detail.setReadOnly(True)
        self.ledger_detail.setFixedHeight(DETAIL_HEIGHT)
        layout.addWidget(self.ledger_detail)
        return card

    # ── 养成 ─────────────────────────────────────────────
    def _import_growth_data(self):
        """导入养成数据（JSON 直投，或从 wiki 抄出来的 CSV/TSV 表格）。

        表格走 :mod:`src.core.growth.table_import`，命令行版本见 ``tools/import_growth.py``——
        两边同一套解析与校验，报错都指到第几行哪一列。
        """
        path, _selected = QFileDialog.getOpenFileName(
            self,
            self.tr("选择养成数据（JSON 或 CSV）"),
            str(Path.cwd()),
            "养成数据 (*.json *.csv *.tsv *.txt);;JSON (*.json);;表格 (*.csv *.tsv *.txt)",
        )
        if not path:
            return
        is_table = Path(path).suffix.lower() in (".csv", ".tsv", ".txt")
        result = import_table(path) if is_table else import_dataset(path)
        if result.ok:
            self._notify(result.describe())
            self.refresh_growth_entries()
        else:
            self._notify(result.describe(), error=True)

    def refresh_growth_entries(self):
        self._dataset = None
        self._dataset_error = ""
        try:
            self._dataset = Dataset.load_default()
        except GrowthDataError as exc:
            self._dataset_error = str(exc)

        self._entry_keys = []
        if self.entry_combo is not None:
            self.entry_combo.blockSignals(True)
            self.entry_combo.clear()
        if self._dataset is None:
            if self.entry_combo is not None:
                self.entry_combo.addItem(self.tr("（没有可用数据）"))
                self.entry_combo.blockSignals(False)
            self._set_detail(
                self.growth_detail,
                self._dataset_error or self.tr("没有可用的养成数据"),
            )
            return

        order = {kind: index for index, kind in enumerate(GROWTH_KINDS)}
        entries = sorted(
            self._dataset.entries.values(),
            key=lambda entry: (order.get(entry.kind, 99), entry.name),
        )
        for entry in entries:
            self._entry_keys.append(entry.key)
            if self.entry_combo is not None:
                self.entry_combo.addItem(f"{KIND_LABELS.get(entry.kind, entry.kind)} · {entry.name}")
        if self.entry_combo is not None:
            self.entry_combo.blockSignals(False)

        counts = self._dataset.kinds_summary()
        summary = "、".join(
            f"{KIND_LABELS.get(kind, kind)} {count}" for kind, count in counts.items() if count
        )
        self._set_detail(
            self.growth_detail,
            self.tr("数据载入成功：{summary}（共 {total} 个目标）").format(
                summary=summary, total=len(self._dataset.entries)
            )
            + "\n"
            + self._dataset.note,
        )

    def _current_entry_key(self) -> str:
        if self.entry_combo is None or not self._entry_keys:
            return ""
        index = self.entry_combo.currentIndex()
        return self._entry_keys[index] if 0 <= index < len(self._entry_keys) else ""

    def _add_target(self):
        key = self._current_entry_key()
        if not key or self._dataset is None:
            self._notify(self.tr("没有可用的养成目标"), error=True)
            return
        try:
            entry = self._dataset.entry(key)
        except GrowthDataError as exc:
            self._notify(str(exc), error=True)
            return
        target = Target(
            entry_key=key,
            from_level=self.level_from.value() if self.level_from else 1,
            to_level=self.level_to.value() if self.level_to else entry.max_level,
            copies=self.copies_box.value() if self.copies_box else 1,
            potential_level=self.potential_box.value() if self.potential_box else 0,
            star=self.star_box.value() if self.star_box else 0,
        )
        self._targets.append(target)
        self._update_target_label()

    def _clear_targets(self):
        self._targets = []
        self._plan = None
        self._update_target_label()
        if self.growth_detail is not None:
            self.growth_detail.clear()

    def _update_target_label(self):
        if self.target_label is None:
            return
        if not self._targets:
            self.target_label.setText(self.tr("还没有目标：选好参数后点「添加目标」"))
            return
        parts = []
        for target in self._targets:
            name = target.entry_key
            if self._dataset is not None and target.entry_key in self._dataset.entries:
                name = self._dataset.entries[target.entry_key].name
            end = target.to_level if target.to_level is not None else "满"
            extra = []
            if target.copies > 1:
                extra.append(f"×{target.copies}")
            if target.potential_level:
                extra.append(f"潜能{target.potential_level}")
            if target.star:
                extra.append(f"{target.star}星")
            suffix = (" " + " ".join(extra)) if extra else ""
            parts.append(f"{name} Lv{target.from_level}→{end}{suffix}")
        self.target_label.setText(self.tr("已选 {count} 个目标：{items}").format(
            count=len(self._targets), items="；".join(parts)
        ))

    def _parse_inventory(self) -> dict[str, float]:
        text = self.inventory_edit.toPlainText() if self.inventory_edit else ""
        inventory: dict[str, float] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            for separator in ("=", "：", ":", " "):
                if separator in line:
                    name, _, raw = line.partition(separator)
                    break
            else:
                name, raw = line, "0"
            name = name.strip()
            try:
                amount = float(raw.strip().replace(",", ""))
            except ValueError:
                continue
            if name and amount > 0:
                inventory[name] = amount
        return inventory

    def _compute_growth(self):
        if self._dataset is None:
            self._notify(self._dataset_error or self.tr("没有可用的养成数据"), error=True)
            return
        if not self._targets:
            self._notify(self.tr("请先添加至少一个目标"), error=True)
            return
        try:
            self._plan = compute_plan(self._dataset, self._targets, self._parse_inventory())
        except (GrowthInputError, GrowthDataError) as exc:
            self._notify(str(exc), error=True)
            return
        self._set_detail(self.growth_detail, self._describe_plan(self._plan))
        self._notify(self.tr("计算完成"))

    def _describe_plan(self, plan) -> str:
        lines = list(plan.summary_lines())
        lines.append("")
        lines.append(self.tr("材料清单："))
        for row in material_rows(plan):
            stamina = f"{row['体力']:g} 体力 / {row['次数']} 次" if row["次数"] else row["来源"] or "-"
            lines.append(f"  {row['材料']}：需求 {row['需求']:g}，缺口 {row['缺口']:g}，{stamina}")
        if plan.crystals:
            lines.append("")
            lines.append(
                self.tr("货币凑法：")
                + "、".join(f"{name} ×{count}" for name, count in plan.crystals.items())
            )
        if plan.exp_items:
            lines.append(
                self.tr("经验道具：")
                + "、".join(f"{name} ×{count}" for name, count in plan.exp_items.items())
            )
        return "\n".join(lines)

    def _export_growth(self, kind: str):
        if self._plan is None:
            self._notify(self.tr("请先点「计算」"), error=True)
            return
        suffix = "csv" if kind == "csv" else "png"
        path, _filter = QFileDialog.getSaveFileName(
            self,
            self.tr("保存养成材料单"),
            f"养成材料单.{suffix}",
            self.tr("CSV 文件 (*.csv)") if suffix == "csv" else self.tr("PNG 图片 (*.png)"),
        )
        if not path:
            return
        try:
            written = (
                export_csv(self._plan, path) if suffix == "csv" else export_png(self._plan, path)
            )
        except Exception as exc:
            self._notify(self.tr("导出失败：{error}").format(error=exc), error=True)
            return
        # 用导出函数返回的路径：它会补上 .csv/.png（用户可能手输没扩展名的名字）
        self._notify(self.tr("已导出：{path}").format(path=written))

    # ── 抽卡 ─────────────────────────────────────────────
    def _compute_gacha(self):
        target_rate = 1.0 if (self.target_rate_combo is None or self.target_rate_combo.currentIndex() == 0) else 0.5
        try:
            params = GachaParams()
            rates_text = self.rates_edit.text().strip() if self.rates_edit else ""
            if rates_text:
                params = GachaParams(custom_rates=parse_rate_table(rates_text))
            model = PityBernoulliModel(params, target_rate=target_rate)
            result = model.plan(
                copies=self.copies_gacha.value() if self.copies_gacha else 1,
                pity=self.pity_gacha.value() if self.pity_gacha else 0,
                budget=self.budget_gacha.value() if self.budget_gacha else 180,
            )
        except ValueError as exc:
            self._notify(str(exc), error=True)
            return

        lines = [result.summary(), ""]
        lines.append(self.tr("软保底关键点：71 抽 {p71}、81 抽 {p81}、90 抽必出").format(
            p71=f"{model.rate_at(71) * 100:.1f}%" if result.rates else "-",
            p81=f"{model.rate_at(81) * 100:.1f}%" if result.rates else "-",
        ))
        lines.append(
            self.tr("参数：目标 {copies} 份，已垫 {pity} 抽，出目标 {rate}").format(
                copies=result.copies,
                pity=result.pity,
                rate=f"{result.target_rate * 100:.0f}%",
            )
        )
        self._set_detail(self.gacha_detail, "\n".join(lines))

    # ── 记账 ─────────────────────────────────────────────
    def _ledger(self) -> Ledger:
        return Ledger()

    def _run_log(self) -> RunLog:
        return RunLog()

    def _record_settlement(self):
        text = self.settlement_edit.toPlainText() if self.settlement_edit else ""
        if not text.strip():
            self._notify(self.tr("请先粘贴结算文字"), error=True)
            return
        try:
            entries = parse_settlement_text(text, source=self.tr("手动记账"))
        except SettlementFormatError as exc:
            self._notify(str(exc), error=True)
            return
        written = self._ledger().record(entries)
        self._notify(self.tr("已记录 {count} 条").format(count=written))
        self.refresh_ledger_view()

    def refresh_ledger_view(self):
        if self.ledger_detail is None:
            return
        ledger = self._ledger()
        run_log = self._run_log()
        today = ledger.today_summary()
        totals = ledger.totals()
        today_income = sum(item["income"] for item in today.values())
        today_expense = sum(item["expense"] for item in today.values())

        lines = [
            self.tr("今日：收入 {income:g} / 支出 {expense:g}").format(
                income=today_income, expense=today_expense
            ),
            self.tr("累计：收入 {income:g} / 支出 {expense:g} / 净 {net:g}").format(
                income=totals["income"], expense=totals["expense"], net=totals["net"]
            ),
            "",
            self.tr("运行报表："),
        ]
        stats = run_log.stats()
        if stats["count"]:
            lines.append(
                self.tr("  共 {count} 次，成功 {success} 次，成功率 {rate:.1f}%，平均 {avg:.0f} 秒").format(
                    count=int(stats["count"]),
                    success=int(stats["success"]),
                    rate=stats["success_rate"] * 100,
                    avg=stats["avg_seconds"],
                )
            )
            # 按任务的明细 + 失败原因汇总（report_lines 第一行是总览，这里已经单独渲染了）
            for line in run_log.report_lines()[1:]:
                lines.append(f"  {line}")
        else:
            lines.append(self.tr("  还没有运行记录"))

        if today:
            lines.append("")
            lines.append(self.tr("今日明细："))
            for item, values in sorted(today.items()):
                lines.append(
                    f"  {item}：+{values['income']:g} / -{values['expense']:g} / 净 {values['net']:g}"
                )
        self._set_detail(self.ledger_detail, "\n".join(lines))

    def _export_ledger(self, kind: str):
        default = "ledger.csv" if kind == "ledger" else "runs.csv"
        path, _filter = QFileDialog.getSaveFileName(
            self, self.tr("导出 CSV"), default, self.tr("CSV 文件 (*.csv)")
        )
        if not path:
            return
        try:
            written = (
                self._ledger().export_csv(path)
                if kind == "ledger"
                else self._run_log().export_csv(path)
            )
        except Exception as exc:
            self._notify(self.tr("导出失败：{error}").format(error=exc), error=True)
            return
        self._notify(self.tr("已导出：{path}").format(path=written))

    # ── 工具 ─────────────────────────────────────────────
    def _set_detail(self, widget: QPlainTextEdit | None, text: str):
        if widget is not None:
            widget.setPlainText(text)

    def _notify(self, message: str, error: bool = False):
        try:
            window = self.window()
            factory = InfoBar.error if error else InfoBar.success
            factory(
                title=self.tr("离线工具"),
                content=message,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM_RIGHT,
                duration=5000 if error else 3000,
                parent=window,
            )
        except Exception:
            self.logger.info(message)
