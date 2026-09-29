"""Safe OCR character confusion patch.

Transforms match patterns to accept OCR-confused characters
without modifying OCR output text.

Safety guarantees:
  - Regex tokens (ESCAPE, metachar classes, quantifiers, groups) are
    never structurally modified — only LITERAL characters outside
    character classes get replaced with [confused+original] char sets.
  - Inside [...] blocks, confused characters are *appended* alongside
    the original — no restructuring, no nested brackets.
  - Every re.compile() is guarded; on any error the original match is
    returned unchanged.
  - String expansion is capped at MAX_VARIANTS to prevent blowup.
"""

from __future__ import annotations

import itertools
import logging
import re
from pathlib import Path

try:
    import json5
except ImportError:
    import json as json5  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

_PATCH_INSTALLED = False
_GLOBAL_CHAR_CONFUSION: dict[str, tuple[str, ...]] = {}

# 安全限制：字符串变体的最大数量（防止笛卡尔积爆炸）
_MAX_VARIANTS = 32

# 安全过滤：混淆字符若为正则元字符，跳过该替换
_REGEX_METACHARS = set(r".^$*+?{}()[]\|")


# ═══════════════════════════════════════════════════════════════════════
#  数据加载
# ═══════════════════════════════════════════════════════════════════════


def _get_ocr_fix_dir() -> Path:
    """获取 assets/ocr_fix 目录路径（参照 lang 模块，以代码所在仓库根目录定位，兼顾 cwd）。"""
    repo_root = Path(__file__).resolve().parents[2]
    fix_dir = repo_root / "assets" / "ocr_fix"
    if fix_dir.is_dir():
        return fix_dir
    cwd_dir = Path.cwd() / "assets" / "ocr_fix"
    if cwd_dir.is_dir():
        return cwd_dir
    return fix_dir


def _merge_fix_data(data: object, fix_map: dict[str, str]) -> None:
    """递归合并字典中的 OCR 混淆映射。

    支持格式:
      1. 扁平字典: {"错字/错词": "正字/正词"}
      2. 嵌套字典: {"module_name": {"错字": "正字"}}
    """
    if not isinstance(data, dict):
        return
    for k, v in data.items():
        if isinstance(v, str):
            wrong = str(k).strip()
            correct = v.strip()
            if wrong and correct:
                fix_map[wrong] = correct
        elif isinstance(v, dict):
            _merge_fix_data(v, fix_map)


def _load_fix_map(fix_dir: Path | None = None) -> dict[str, str]:
    """参照 lang 模块实现，扫描 assets/ocr_fix/ 目录下所有 JSON/JSON5 文件并合并映射。"""
    if fix_dir is None:
        fix_dir = _get_ocr_fix_dir()
    if not fix_dir.is_dir():
        return {}

    fix_files = sorted(
        p for p in fix_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".json", ".json5")
    )
    if not fix_files:
        return {}

    fix_map: dict[str, str] = {}
    loaded_files = 0
    for fix_file in fix_files:
        try:
            with fix_file.open(encoding="utf-8") as f:
                data = json5.load(f)
        except Exception as exc:
            logger.warning("Failed to load OCR fix file %s: %s", fix_file.name, exc)
            continue

        if not isinstance(data, dict):
            continue

        _merge_fix_data(data, fix_map)
        loaded_files += 1

    logger.debug("Loaded %d OCR fix pairs from %d files in %s", len(fix_map), loaded_files, fix_dir)
    return fix_map


def _build_char_confusion(fix_map: dict[str, str]) -> dict[str, tuple[str, ...]]:
    """从 {错误→正确} 文本对中提取字符级混淆映射。

    例: {"乾員養成": "幹員養成"} → {"幹": "乾"}
    含义: 正确的"幹"在 OCR 结果中被识别为错误的"乾"。

    返回: {正确字符: (OCR错误字符, ...)}
    """
    char_map: dict[str, set[str]] = {}
    for wrong, correct in fix_map.items():
        if len(wrong) != len(correct):
            continue
        for wc, cc in zip(wrong, correct, strict=True):
            if wc != cc:
                char_map.setdefault(cc, set()).add(wc)
    return {correct_char: tuple(sorted(wrong_chars)) for correct_char, wrong_chars in char_map.items()}


# ═══════════════════════════════════════════════════════════════════════
#  Regex 分词器 — 安全处理的基础
# ═══════════════════════════════════════════════════════════════════════

# Token 类型:
#   ESCAPE     : \x (2 字符)         — 永远不修改
#   CHAR_CLASS : [...] 块 (含括号)    — 只在内部追加字符
#   LITERAL    : 普通字符              — 可能施加混淆替换
#   OTHER      : 正则元字符 / 孤立的 [ — 永远不修改


def _tokenize_pattern(pattern: str) -> list[tuple[str, str]]:
    """将 regex pattern 分词为安全 token 列表。

    返回 [(type, value), ...]，type 取值为 ESCAPE / CHAR_CLASS / LITERAL / OTHER。
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(pattern)

    while i < n:
        ch = pattern[i]

        # ── ESCAPE: \x ────────────────────────────────────────────
        if ch == "\\" and i + 1 < n:
            tokens.append(("ESCAPE", pattern[i : i + 2]))
            i += 2
            continue

        # ── CHAR_CLASS: [...] ──────────────────────────────────────
        if ch == "[":
            j = i + 1
            while j < n:
                if pattern[j] == "\\" and j + 1 < n:
                    j += 2  # 跳过转义序列（\] 是字面量）
                    continue
                if pattern[j] == "]":
                    break
                j += 1
            if j < n and pattern[j] == "]":
                tokens.append(("CHAR_CLASS", pattern[i : j + 1]))
                i = j + 1
            else:
                # 孤立的 [ → 当作字面量处理，避免 grammar 破坏
                tokens.append(("OTHER", ch))
                i += 1
            continue

        # ── OTHER: 正则元字符 ──────────────────────────────────────
        if ch in r".^$*+?{}()|":
            tokens.append(("OTHER", ch))
            i += 1
            continue

        # ── LITERAL: 可施加混淆的普通字符 ─────────────────────────
        tokens.append(("LITERAL", ch))
        i += 1

    return tokens


# ═══════════════════════════════════════════════════════════════════════
#  核心混淆应用函数
# ═══════════════════════════════════════════════════════════════════════


def _apply_confusion_to_str(text: str, char_confusion: dict[str, tuple[str, ...]]) -> str | list[str]:
    """对字符串应用字符混淆：笛卡尔积生成所有变体，上限 _MAX_VARIANTS。"""
    char_options: list[tuple[str, ...]] = []
    for ch in text:
        if ch in char_confusion:
            char_options.append((ch, *char_confusion[ch]))
        else:
            char_options.append((ch,))

    variants: list[str] = []
    seen: set[str] = set()
    for combo in itertools.product(*char_options):
        candidate = "".join(combo)
        if candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)
            if len(variants) >= _MAX_VARIANTS:
                break

    if len(variants) <= 1:
        return text
    return variants


def _apply_confusion_to_regex(
    pattern: re.Pattern, char_confusion: dict[str, tuple[str, ...]]
) -> re.Pattern:
    """对 regex pattern 应用字符混淆。

    安全规则:
      1. ESCAPE token → 原样保留，不参与任何替换。
      2. CHAR_CLASS token → 在内部扫描混淆字，将其后追加混淆字符
         （不嵌套 []，不改变结构）。
      3. LITERAL token → 若在混淆表中，替换为 [错正] 字符集。
      4. OTHER token → 原样保留。
      5. 任何 re.compile 失败 → 返回原始 pattern。
    """
    raw = pattern.pattern
    flags = pattern.flags

    # 快速跳过：没有任何混淆字
    if not any(ch in char_confusion for ch in raw):
        return pattern

    tokens = _tokenize_pattern(raw)
    result_parts: list[str] = []
    modified = False

    for typ, val in tokens:
        if typ == "LITERAL":
            if val in char_confusion:
                confused_chars = "".join(
                    confused
                    for confused in char_confusion[val]
                    if confused not in _REGEX_METACHARS
                )
                if confused_chars:
                    result_parts.append(f"[{confused_chars}{val}]")
                    modified = True
                else:
                    result_parts.append(val)
            else:
                result_parts.append(val)

        elif typ == "CHAR_CLASS":
            # 仅扫描内部，在混淆字后追加错字 —— 不修改结构
            inner = val[1:-1]  # 去掉 []
            new_inner: list[str] = []
            j = 0
            while j < len(inner):
                c = inner[j]
                if c == "\\" and j + 1 < len(inner):
                    new_inner.append(inner[j : j + 2])
                    j += 2
                    continue
                new_inner.append(c)
                if c in char_confusion:
                    confused_chars = "".join(
                        confused
                        for confused in char_confusion[c]
                        if confused not in _REGEX_METACHARS
                    )
                    if confused_chars:
                        new_inner.append(confused_chars)
                        modified = True
                j += 1
            result_parts.append("[" + "".join(new_inner) + "]")

        else:
            # ESCAPE / OTHER — 原样保留
            result_parts.append(val)

    if not modified:
        return pattern

    try:
        return re.compile("".join(result_parts), flags)
    except re.error:
        return pattern


def _apply_confusion_to_match(match, char_confusion: dict[str, tuple[str, ...]]):
    """对 match 应用字符级混淆，让 match 也能匹配 OCR 识别错的文本。

    支持三种输入:
      - str        → 生成最多 _MAX_VARIANTS 个变体字符串
      - re.Pattern → 用安全分词器替换混淆字符
      - list       → 递归处理每个元素

    任何异常或正则编译失败都返回原始输入，不破坏调用链。
    """
    if not char_confusion:
        return match

    try:
        if isinstance(match, str):
            return _apply_confusion_to_str(match, char_confusion)

        if isinstance(match, re.Pattern):
            return _apply_confusion_to_regex(match, char_confusion)

        if isinstance(match, list):
            result: list = []
            for item in match:
                transformed = _apply_confusion_to_match(item, char_confusion)
                if isinstance(transformed, list):
                    result.extend(transformed)
                else:
                    result.append(transformed)
            return result

        return match

    except Exception:
        return match


# ═══════════════════════════════════════════════════════════════════════
#  安装补丁
# ═══════════════════════════════════════════════════════════════════════


def reload_ocr_text_fix(fix_dir: Path | None = None) -> dict[str, tuple[str, ...]]:
    """重新加载 assets/ocr_fix/ 下的所有配置并更新全局混淆表。"""
    global _GLOBAL_CHAR_CONFUSION
    fix_map = _load_fix_map(fix_dir)
    _GLOBAL_CHAR_CONFUSION = _build_char_confusion(fix_map)
    return _GLOBAL_CHAR_CONFUSION


def install_ocr_text_fix_patch():
    global _PATCH_INSTALLED, _GLOBAL_CHAR_CONFUSION
    if _PATCH_INSTALLED:
        return

    from ok.task.task import OCR
    from ok.task.TaskExecutor import TaskExecutor

    # 1. 读取并构建字符级混淆表
    reload_ocr_text_fix()

    # 2. 保存混淆表到 executor（方便各 task 实例获取）
    original_executor_init = TaskExecutor.__init__

    def patched_executor_init(self, *args, **kwargs):
        original_executor_init(self, *args, **kwargs)
        self.ocr_char_confusion = _GLOBAL_CHAR_CONFUSION

    TaskExecutor.__init__ = patched_executor_init

    # 3. Patch fix_match_regex: 在现有翻译逻辑之后，再施加字符混淆
    original_fix_match_regex = OCR.fix_match_regex

    def patched_fix_match_regex(self, match):
        match = original_fix_match_regex(self, match)
        confusion = getattr(self.executor, "ocr_char_confusion", None) or _GLOBAL_CHAR_CONFUSION
        if confusion:
            match = _apply_confusion_to_match(match, confusion)
        return match

    OCR.fix_match_regex = patched_fix_match_regex

    _PATCH_INSTALLED = True
