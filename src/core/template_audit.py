"""模板自检：新标注的截图区域值不值得做成模板。

需求里的原话是「先检查选区有没有纹理（纯色块会被拒绝）」——纯色块永远匹配不上
（`TM_CCOEFF_NORMED` 对常量图会给出无意义的高分或 NaN），做进配置只会让人误以为
识别坏了。所以这里用三个便宜但有效的指标：

| 指标 | 含义 | 纯色块的表现 |
|------|------|--------------|
| `stddev` | 灰度标准差（对比度） | ≈ 0 |
| `unique_colors` | 去重后的颜色数 | 1~2 |
| `edge_density` | Canny 边缘像素占比 | ≈ 0 |

三项都低于阈值就判为 `flat`（拒绝）；有一项勉强及格判为 `weak`（建议换区域）；
都不错才是 `ok`。

另外给每个模板算 **SHA-256**：同一张图重复标注、或者两个模板其实一模一样，
靠它一眼就能发现（重复模板会让「全模板复测」的分数变得难解释）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

#: 判据默认值（按 1080P 下 20~40px 的小图标实测取值）
MIN_STDDEV = 6.0
MIN_UNIQUE_COLORS = 8
MIN_EDGE_DENSITY = 0.01
MIN_SIDE = 6

VERDICT_OK = "ok"
VERDICT_WEAK = "weak"
VERDICT_FLAT = "flat"


class TemplateAuditError(ValueError):
    """标注数据不合法。"""


@dataclass(frozen=True)
class TemplateQuality:
    name: str
    width: int = 0
    height: int = 0
    stddev: float = 0.0
    unique_colors: int = 0
    edge_density: float = 0.0
    sha256: str = ""
    verdict: str = VERDICT_FLAT
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.verdict == VERDICT_OK

    def score(self) -> float:
        """0~1 的粗打分，用来排序「最该重做」的模板。"""
        contrast = min(1.0, self.stddev / (MIN_STDDEV * 4))
        colors = min(1.0, self.unique_colors / (MIN_UNIQUE_COLORS * 4))
        edges = min(1.0, self.edge_density / (MIN_EDGE_DENSITY * 4))
        return round((contrast + colors + edges) / 3, 3)

    def label(self) -> str:
        verdict = {VERDICT_OK: "可用", VERDICT_WEAK: "偏弱", VERDICT_FLAT: "纯色块"}.get(self.verdict, self.verdict)
        return f"{self.name}  {verdict}  {self.width}×{self.height}  std={self.stddev:.1f}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "stddev": round(self.stddev, 2),
            "unique_colors": self.unique_colors,
            "edge_density": round(self.edge_density, 4),
            "sha256": self.sha256,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "score": self.score(),
        }


@dataclass
class AuditReport:
    templates: list[TemplateQuality] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)
    source: str = ""

    @property
    def count(self) -> int:
        return len(self.templates)

    def by_verdict(self, verdict: str) -> list[TemplateQuality]:
        return [item for item in self.templates if item.verdict == verdict]

    @property
    def flat(self) -> list[TemplateQuality]:
        return self.by_verdict(VERDICT_FLAT)

    @property
    def weak(self) -> list[TemplateQuality]:
        return self.by_verdict(VERDICT_WEAK)

    def worst(self, limit: int = 10) -> list[TemplateQuality]:
        return sorted(self.templates, key=lambda item: item.score())[:limit]

    def summary(self) -> str:
        parts = [f"共 {self.count} 个模板"]
        flat = len(self.flat)
        weak = len(self.weak)
        if flat:
            parts.append(f"{flat} 个纯色块（建议删掉重做）")
        if weak:
            parts.append(f"{weak} 个纹理偏弱")
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} 组重复")
        if not flat and not weak and not self.duplicates:
            parts.append("全部可用")
        return "，".join(parts)


# ── 单块分析 ─────────────────────────────────────────────
def analyze_region(region: np.ndarray, name: str = "", sha256: str = "") -> TemplateQuality:
    """分析一块裁切图，给出可用性判定。"""
    if region is None or not isinstance(region, np.ndarray) or region.size == 0:
        return TemplateQuality(name=name, verdict=VERDICT_FLAT, reasons=("空区域",))

    height, width = region.shape[:2]
    if width < MIN_SIDE or height < MIN_SIDE:
        return TemplateQuality(
            name=name,
            width=int(width),
            height=int(height),
            verdict=VERDICT_FLAT,
            reasons=(f"尺寸太小（< {MIN_SIDE}px）",),
        )

    if region.ndim == 3:
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.shape[2] >= 3 else region[:, :, 0]
        pixels = region[:, :, :3].reshape(-1, 3)
    else:
        gray = region
        pixels = region.reshape(-1, 1)

    stddev = float(gray.std())
    unique_colors = int(np.unique(pixels, axis=0).shape[0])
    edges = cv2.Canny(gray, 40, 120)
    edge_density = float(np.count_nonzero(edges)) / float(edges.size)

    reasons: list[str] = []
    if stddev < MIN_STDDEV:
        reasons.append(f"对比度太低（std {stddev:.1f} < {MIN_STDDEV:g}）")
    if unique_colors < MIN_UNIQUE_COLORS:
        reasons.append(f"颜色太少（{unique_colors} < {MIN_UNIQUE_COLORS}）")
    if edge_density < MIN_EDGE_DENSITY:
        reasons.append(f"几乎没有边缘（{edge_density:.3f} < {MIN_EDGE_DENSITY:g}）")

    if len(reasons) == 3:
        verdict = VERDICT_FLAT
    elif reasons:
        verdict = VERDICT_WEAK
    else:
        verdict = VERDICT_OK

    return TemplateQuality(
        name=name,
        width=int(width),
        height=int(height),
        stddev=stddev,
        unique_colors=unique_colors,
        edge_density=edge_density,
        sha256=sha256 or sha256_of_region(region),
        verdict=verdict,
        reasons=tuple(reasons),
    )


def sha256_of_region(region: np.ndarray) -> str:
    if region is None or not isinstance(region, np.ndarray):
        return ""
    return hashlib.sha256(np.ascontiguousarray(region).tobytes()).hexdigest()


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(bytes(data or b"")).hexdigest()


# ── 批量自检 ─────────────────────────────────────────────
def audit_coco(coco_path: str | Path, images_dir: str | Path | None = None) -> AuditReport:
    """读 COCO 标注，逐条裁出模板并自检。

    ``images_dir`` 为空时用 coco 文件所在目录（标注里的 ``images/1.png`` 相对它）。
    """
    coco_file = Path(coco_path)
    try:
        data = json.loads(coco_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TemplateAuditError(f"读不到标注文件: {exc}") from exc
    except ValueError as exc:
        raise TemplateAuditError(f"标注文件不是合法 JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise TemplateAuditError("标注文件顶层必须是对象")

    images = {int(item.get("id", 0)): item for item in data.get("images", []) if isinstance(item, Mapping)}
    categories = {int(item.get("id", 0)): str(item.get("name", "")) for item in data.get("categories", [])
                  if isinstance(item, Mapping)}
    annotations = [item for item in data.get("annotations", []) if isinstance(item, Mapping)]

    base = Path(images_dir) if images_dir is not None else coco_file.parent
    atlas_cache: dict[int, np.ndarray] = {}
    report = AuditReport(source=str(coco_file))

    for annotation in annotations:
        image_id = int(annotation.get("image_id", 0))
        category_id = int(annotation.get("category_id", 0))
        name = categories.get(category_id, f"category_{category_id}")
        image = images.get(image_id)
        if image is None:
            continue
        atlas = atlas_cache.get(image_id)
        if atlas is None:
            atlas_path = base / str(image.get("file_name", ""))
            atlas = _read_image(atlas_path)
            if atlas is None:
                raise TemplateAuditError(f"读不到模板源图: {atlas_path}")
            atlas_cache[image_id] = atlas

        bbox = annotation.get("bbox") or [0, 0, 0, 0]
        quality = analyze_region(_crop(atlas, bbox), name=name)
        report.templates.append(quality)

    rebuild_duplicates(report)
    return report


def _read_image(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _crop(atlas: np.ndarray, bbox: Iterable[Any]) -> np.ndarray:
    try:
        x, y, width, height = (round(float(value)) for value in list(bbox)[:4])
    except (TypeError, ValueError):
        return np.empty((0, 0, 3), dtype=np.uint8)
    height_px, width_px = atlas.shape[:2]
    x1 = max(0, min(width_px, x))
    y1 = max(0, min(height_px, y))
    x2 = max(x1, min(width_px, x + max(0, width)))
    y2 = max(y1, min(height_px, y + max(0, height)))
    return atlas[y1:y2, x1:x2]


def rebuild_duplicates(report: AuditReport) -> AuditReport:
    """重算重复分组（相同 SHA-256 的模板名放在一起）。"""
    groups: dict[str, list[str]] = {}
    for item in report.templates:
        if item.sha256:
            groups.setdefault(item.sha256, []).append(item.name)
    report.duplicates = {digest: names for digest, names in groups.items() if len(names) > 1}
    return report


def audit_directory(root: str | Path, images_dir: str | Path | None = None) -> AuditReport:
    """在目录里找 ``coco_annotations.json`` 并自检（找不到就报错）。"""
    base = Path(root)
    candidates = [base / "coco_annotations.json", base / "assets" / "coco_annotations.json"]
    for candidate in candidates:
        if candidate.is_file():
            return audit_coco(candidate, images_dir=images_dir)
    raise TemplateAuditError(f"没有找到 coco_annotations.json: {base}")
