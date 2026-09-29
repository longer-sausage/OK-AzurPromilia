"""祖赞卡之梯视觉观测；所有输出使用 1920×1080 坐标，不发送输入。

灰度高通模板消除昼夜光照与模糊背景的干扰。鸟用于确认悬挂状态，
图腾模板用于定位真实承接面；新造型可从鸟下方的清晰轮廓学习。
"""

from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "jenga"


@dataclass(frozen=True)
class JengaPiece:
    x: float
    top: float
    bottom: float
    width: float
    confidence: float = 1.0
    kind: str = ""


@dataclass(frozen=True)
class JengaObservation:
    carried: JengaPiece
    tower: JengaPiece
    bird: JengaPiece | None = None


def _detail(gray):
    gray = gray.astype(np.float32)
    # Moving perfect-placement sparkles must not outweigh the sculpture texture.
    return np.clip(gray - cv2.GaussianBlur(gray, (0, 0), 3), -15, 15)


class JengaDetector:
    def __init__(self):
        self.templates = []
        self.birds = []
        self._template_kinds = {}
        for path in sorted(ASSETS.glob("*.png")):
            gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if path.stem.startswith("bird"):
                self.birds.append(_detail(cv2.resize(gray, None, fx=0.5, fy=0.5)))
            elif path.stem == "controls":
                self.controls = gray
            elif path.stem == "score":
                self.score = gray
            else:
                variants = self._variants(gray)
                kind = (
                    "rabbit"
                    if path.stem.startswith("rabbit")
                    else "owl"
                    if path.stem.startswith("owl")
                    else "monkey"
                    if path.stem.startswith("stone")
                    else ""
                )
                self.templates.extend(variants)
                self._template_kinds.update((id(template), kind) for template in variants)
        self.learned = []
        self.pixels_per_unit = None
        self._held_template = None
        self._held_full_height = None
        self._held_full_width = None
        self._held_face_offset = None
        self._held_shape_samples = []
        self._tower_template = None
        self._tower_full_height = None
        self._tower_last = None
        self._held_kind = ""
        self._tower_kind = ""
        self._identity_retry = 0
        self._identity_candidate = ""
        self._identity_votes = 0
        self._identity_strong_votes = 0
        self._identity_extent = None
        self._identity_pixels = None
        self.result_templates = {}
        for path in (ASSETS / "results").glob("*.png"):
            gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            self.result_templates.setdefault(path.stem.split("_", 1)[0], []).extend(
                [_detail(cv2.resize(gray, None, fx=scale / 2, fy=scale / 2)) for scale in (0.8, 0.9, 1.0, 1.1, 1.2)]
            )

    def placement_result(self, frame):
        """Read the rendered judgment, independently of the sculpture counter."""
        frame = self.normalize(frame)
        if frame is None:
            return None
        gray = cv2.cvtColor(frame[250:920, 250:1250], cv2.COLOR_BGR2GRAY)
        detail = _detail(cv2.resize(gray, None, fx=0.5, fy=0.5))
        confidences = {}
        for name, templates in self.result_templates.items():
            confidence = 0.0
            for template in templates:
                scores = cv2.matchTemplate(detail, template, cv2.TM_CCOEFF_NORMED)
                score = cv2.minMaxLoc(scores)[1]
                confidence = max(confidence, score)
            confidences[name] = confidence
        ranked = sorted(confidences.items(), key=lambda item: -item[1])
        if ranked and ranked[0][1] >= 0.60 and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= 0.20):
            return ranked[0][0]
        return None

    @staticmethod
    def _variants(gray):
        variants = []
        for scale in (0.9, 1.0, 1.1):
            small = cv2.resize(gray, None, fx=0.5 * scale, fy=0.5 * scale)
            h, w = small.shape
            for angle in (-8, 0, 8):
                rotated = cv2.warpAffine(
                    small,
                    cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1),
                    (w, h),
                    borderMode=cv2.BORDER_REFLECT,
                )
                variants.append(_detail(rotated))
        return variants

    @staticmethod
    def normalize(frame):
        if frame is None or frame.ndim != 3 or frame.shape[2] < 3 or min(frame.shape[:2]) < 120:
            return None
        if abs(frame.shape[1] / frame.shape[0] - 16 / 9) > 0.04:
            return None
        image = frame[:, :, :3]
        return image if image.shape[:2] == (1080, 1920) else cv2.resize(image, (1920, 1080))

    def is_active(self, frame):
        frame = self.normalize(frame)
        if frame is None:
            return False
        gray = cv2.cvtColor(frame[48:92, 826:920], cv2.COLOR_BGR2GRAY)
        # 两处独立的固定 HUD 图形；不依赖中文标题或分数数值。
        result = cv2.matchTemplate(gray, self.controls, cv2.TM_CCOEFF_NORMED)
        if cv2.minMaxLoc(result)[1] < 0.75:
            return False
        # The star rotates/pulses continuously; its color occupancy is invariant.
        hsv = cv2.cvtColor(frame[932:1012, 1550:1640], cv2.COLOR_BGR2HSV)
        gold = cv2.inRange(hsv, (15, 100, 180), (40, 255, 255))
        white = cv2.inRange(hsv, (0, 0, 220), (179, 80, 255))
        return np.mean(gold > 0) > 0.20 and np.mean(white > 0) > 0.025

    @staticmethod
    def _find_birds(frame):
        hsv = cv2.cvtColor(frame[:450, 520:1440], cv2.COLOR_BGR2HSV)
        # Preserve the daylight mask; retry a less saturated neck only when it
        # finds no bird. Night lighting can otherwise truncate the component.
        for saturation, minimum_area, minimum_height in ((80, 750, 35), (60, 750, 35), (60, 300, 20)):
            yellow = cv2.inRange(hsv, (15, saturation, 140), (42, 255, 255))
            _, _, stats, _ = cv2.connectedComponentsWithStats(yellow)
            birds = []
            for x, y, w, h, area in stats[1:]:
                if not (minimum_area < area < 1600 and 27 <= w <= 45 and minimum_height <= h <= 67 and y + h < 420):
                    continue
                if x < 8 or x + w + 8 > hsv.shape[1]:
                    continue  # Border fragments cannot provide a complete bird/body ROI.
                white = cv2.inRange(hsv[y + h // 2 : y + h + 30, x - 8 : x + w + 8], (0, 0, 155), (179, 125, 255))
                if np.count_nonzero(white) > 250:
                    birds.append(JengaPiece(float(520 + x + w / 2), float(y), float(y + h + 12), float(w)))
            if birds:
                return birds
        return []

    def _matches(self, detail, templates, bounds, threshold, kind=None):
        # Reacquire at a coarser pyramid level; cached tracking stays finer.
        factor = 4 if len(templates) > 8 else 2
        if factor == 4:
            detail = cv2.resize(detail, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        x1, y1, x2, y2 = (round(v / factor) for v in bounds)
        roi = detail[y1:y2, x1:x2]
        matches = []
        for template in templates:
            matched_kind = kind if kind is not None else self._template_kinds.get(id(template), "")
            if factor == 4:
                template = cv2.resize(template, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
            h, w = template.shape
            if h >= roi.shape[0] or w >= roi.shape[1]:
                continue
            scores = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
            # Keep multiple vertically stacked figures, then deduplicate across variants.
            for _ in range(3):
                _, score, _, (x, y) = cv2.minMaxLoc(scores)
                if score < threshold:
                    break
                matches.append(
                    JengaPiece(
                        (x1 + x + w / 2) * factor,
                        (y1 + y) * factor,
                        (y1 + y + h) * factor,
                        w * factor,
                        score,
                        matched_kind,
                    )
                )
                scores[max(0, y - h // 2) : y + h // 2, max(0, x - w // 2) : x + w // 2] = 0
        unique = []
        for item in sorted(matches, key=lambda p: -p.confidence):
            if not item.kind:
                # A learned session crop may correlate better than a reference
                # while still depicting the same known sculpture. Transfer only
                # a strong, spatially agreeing identity; keep the learned bounds.
                known = max(
                    (
                        p
                        for p in matches
                        if p.kind
                        and p.confidence >= 0.72
                        and abs(p.x - item.x) <= 16
                        and abs(p.top - item.top) <= 35
                        and abs(p.bottom - item.bottom) <= 35
                    ),
                    key=lambda p: p.confidence,
                    default=None,
                )
                if known is not None:
                    item = JengaPiece(item.x, item.top, item.bottom, item.width, item.confidence, known.kind)
            if not any(abs(item.x - p.x) < 45 and abs(item.top - p.top) < 80 for p in unique):
                unique.append(item)
        return unique

    def _unknown_piece(self, frame, bird):
        # Depth of field makes the held sculpture sharp against the blurred scenery.
        x1, x2 = int(bird.x - 80), int(bird.x + 80)
        y1, y2 = int(bird.bottom + 4), min(650, int(bird.bottom + 245))
        gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        # Retry with lower contrast for backlit sculptures.
        first = None
        for low, high in ((60, 140), (20, 60)):
            edges = cv2.Canny(gray, low, high)
            mask = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            # A roof/railing can touch the feet. Cut only the rows spanning the whole ROI
            # before measuring the sculpture, rather than accepting the background width.
            filled = np.zeros_like(mask)
            cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
            filled[np.count_nonzero(filled, axis=1) > 140] = 0
            contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            candidates = []
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if (
                    45 <= w <= 140
                    and 95 <= h <= 230
                    and y < 90
                    and abs(x + w / 2 - 80) < 20
                    and x > 2
                    and x + w < 158
                    and y + h < gray.shape[0] - 2
                ):
                    candidates.append(JengaPiece(x1 + x + w / 2, y1 + y, y1 + y + h, w, 0.6))
            if candidates:
                found = max(candidates, key=lambda p: p.bottom - p.top)
                if first is None:
                    first = found
                    if found.bottom - found.top >= 160:
                        return found
                elif (
                    abs(found.x - first.x) <= 18
                    and abs(found.top - first.top) <= 15
                    and found.bottom >= first.bottom + 12
                ):
                    # Backlighting can disconnect the dark legs from a sharp
                    # face/torso. Do not cache that partial 100-pixel crop when
                    # the lower-contrast contour confirms the same full piece.
                    return found
        return first

    def _carried_below(self, frame, detail, bird, templates):
        """Validate a bird candidate using the sculpture directly below it."""
        held_bounds = (
            max(520, bird.x - 100),
            max(90, bird.bottom - 15),
            min(1440, bird.x + 100),
            min(650, bird.bottom + 270),
        )
        held = (
            self._matches(detail, [self._held_template], held_bounds, 0.65, self._held_kind)
            if self._held_template is not None
            else []
        )
        if held and self._held_full_height is not None:
            held = [
                replace(
                    piece,
                    top=piece.top - self._held_face_offset,
                    bottom=piece.top - self._held_face_offset + self._held_full_height,
                    width=self._held_full_width,
                )
                for piece in held
            ]
        held = [
            p
            for p in held
            if abs(p.x - bird.x) < 28
            and -12 <= p.top - bird.bottom <= (100 if p.kind == "rabbit" and self._held_full_height is None else 40)
        ]
        if not held:
            held = self._matches(detail, templates, held_bounds, 0.52)
            if self._held_full_height is not None:
                held = [
                    replace(piece, bottom=piece.top + self._held_full_height, width=self._held_full_width)
                    for piece in held
                ]
        held = [
            p
            for p in held
            if abs(p.x - bird.x) < 28
            and -12 <= p.top - bird.bottom <= (100 if p.kind == "rabbit" and self._held_full_height is None else 40)
        ]
        carried = max(held, key=lambda p: p.confidence, default=None)
        if carried is None:
            carried = self._unknown_piece(frame, bird)
        if carried is None:
            return None, held_bounds
        if carried.bottom - carried.top < 160 or carried.top - bird.bottom > 40:
            complete = self._unknown_piece(frame, bird)
            if (
                complete is not None
                and abs(complete.x - carried.x) <= 20
                and complete.top - bird.bottom < 90
                and complete.bottom - complete.top >= carried.bottom - carried.top + 12
            ):
                carried = replace(complete, kind=carried.kind)
        if carried.top - bird.bottom > 40 and carried.kind != "rabbit":
            return None, held_bounds
        return carried, held_bounds

    def _identify_carried(self, detail, carried, bounds):
        if carried.kind:
            return carried
        if self._held_kind:
            return replace(carried, kind=self._held_kind)
        self._identity_retry += 1
        if self._identity_retry % 5:
            return carried
        ranked = []
        for kind in ("monkey", "owl", "rabbit"):
            templates = [template for template in self.templates if self._template_kinds.get(id(template)) == kind]
            references = self._matches(detail, templates, bounds, 0.25)
            agreeing = [
                p
                for p in references
                if abs(p.x - carried.x) <= 16
                and abs(p.top - carried.top) <= 35
                and abs(p.bottom - carried.bottom) <= 35
            ]
            if agreeing:
                ranked.append((max(p.confidence for p in agreeing), kind))
        ranked.sort(reverse=True)
        if not ranked:
            return carried
        confidence, kind = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        if confidence >= 0.72 and runner_up < 0.72:
            return replace(carried, kind=kind)
        # A cached crop can explain the body more accurately than a reference
        # under directional light. Three strong views, or five weaker views
        # across a clear horizontal arc, must support one spatially agreeing
        # class separated from every competitor. Repeated pixels cannot vote.
        if confidence < 0.40 or confidence - runner_up < 0.15:
            return carried
        pixels = self._piece_template(detail, carried)
        if self._identity_candidate != kind:
            self._identity_candidate = kind
            self._identity_votes = 0
            self._identity_strong_votes = 0
            self._identity_extent = None
            self._identity_pixels = None
        if self._identity_pixels is None or not np.array_equal(pixels, self._identity_pixels):
            self._identity_votes += 1
            if confidence >= 0.56:
                self._identity_strong_votes += 1
            if self._identity_extent is None:
                self._identity_extent = (carried.x, carried.x)
            else:
                self._identity_extent = (
                    min(self._identity_extent[0], carried.x),
                    max(self._identity_extent[1], carried.x),
                )
            self._identity_pixels = pixels.copy()
        arc = self._identity_extent[1] - self._identity_extent[0] if self._identity_extent is not None else 0.0
        confirmed = self._identity_strong_votes >= 3 or (self._identity_votes >= 5 and arc >= 50)
        return replace(carried, kind=kind) if confirmed else carried

    def detect(self, frame):
        frame = self.normalize(frame)
        if frame is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detail = _detail(cv2.resize(gray, (960, 540)))
        templates = self.templates + [v for group in self.learned for v in group]
        candidates = []
        # A yellow wing can have the same color-component dimensions as the
        # neck. The actual neck is the candidate with a complete sculpture
        # below it; two yellow components alone do not mean an unknown bird.
        for bird in self._find_birds(frame):
            carried, bounds = self._carried_below(frame, detail, bird, templates)
            if carried is not None:
                candidates.append((bird, carried, bounds))
        if len(candidates) != 1:
            candidates = []
            for bird in self._matches(detail, self.birds, (520, 0, 1440, 450), 0.60):
                carried, bounds = self._carried_below(frame, detail, bird, templates)
                if carried is not None:
                    candidates.append((bird, carried, bounds))
        if len(candidates) != 1:
            return None
        bird, carried, held_bounds = candidates[0]
        # A learned crop can win localization without knowing the sculpture's
        # identity. Retry strong reference matches periodically, then retain the
        # confirmed identity through lighting/pose changes until this release.
        carried = self._identify_carried(detail, carried, held_bounds)
        native_shapes = {
            "monkey": (0.754531, 0.5303),
            "owl": (0.758492, 0.5175),
            "rabbit": (0.894245, 0.357515),
        }
        if carried.kind in native_shapes and self.pixels_per_unit is not None:
            height, width = native_shapes[carried.kind]
            if (
                150 < self.pixels_per_unit < 300
                and 0.75 * width * self.pixels_per_unit <= carried.width <= 1.2 * width * self.pixels_per_unit
                and -12 <= carried.top - bird.bottom <= 35
                and (180 if carried.kind == "rabbit" else 150)
                <= carried.bottom - carried.top
                <= (235 if carried.kind == "rabbit" else 200)
            ):
                # A known mesh and this round's measured projection provide a
                # full height even when the dark feet disappear. Keep the
                # observed upper anchor; never calibrate an unknown/partial
                # face or infer scale from the current background contour.
                self._held_full_height = height * self.pixels_per_unit
                self._held_full_width = carried.width
                # Rabbit ears are tall and sparse. Track its textured lower
                # body up to the feet, preserving the native full height.
                self._held_face_offset = self._held_full_height - 80 if carried.kind == "rabbit" else 8.0
                carried = replace(carried, bottom=carried.top + self._held_full_height)
        # Normalized correlation alone can match a blurred railing and then
        # reinforce it by caching that background crop. Both sculptures are in
        # focus: compare high-frequency texture to the carried piece in this
        # same frame, so the check follows illumination rather than RGB colors.
        carried_texture = float(np.mean(abs(self._piece_template(detail, carried))))

        def in_focus(piece):
            texture = abs(self._piece_template(detail, piece))
            return np.mean(texture) >= max(3.0, carried_texture * 0.5) and np.mean(texture > 5) >= 0.23

        def confidence_floor(piece):
            # Sparks can reduce a verified new top's reference correlation.
            # Permit a small loss only for a full, densely textured body at
            # the preceding receiver, under the measured world-to-pixel scale.
            heights = {"monkey": 0.754531, "owl": 0.758492, "rabbit": 0.894245}
            height = heights.get(piece.kind)
            if (
                self._tower_template is None
                and self._tower_kind
                and piece.kind == self._tower_kind
                and self._tower_last is not None
                and self.pixels_per_unit is not None
                and height is not None
                and abs(piece.x - self._tower_last.x) <= 60
                and abs(piece.top - self._tower_last.top) <= 60
                and 0.70 * height * self.pixels_per_unit <= piece.bottom - piece.top <= 1.25 * height * self.pixels_per_unit
                and np.mean(abs(self._piece_template(detail, piece)) > 5) >= 0.55
            ):
                return 0.46
            return 0.50

        def receiving_faces(pieces):
            return [
                p
                for p in pieces
                if p.confidence >= confidence_floor(p)
                and p.top - carried.bottom > 100
                and in_focus(p)
                # Without a release, a similar lower body cannot become the top.
                and (self._tower_last is None or p.top <= self._tower_last.top + 60)
            ]

        tower = []
        tower_templates = templates
        if self._tower_kind:
            # A successful release fixes the new receiver's identity even
            # while its appearance must be reacquired after the camera moves.
            tower_templates = [
                template
                for template in templates
                if self._template_kinds.get(id(template), "") in ("", self._tower_kind)
            ]
        if self._tower_last is not None and self._tower_template is not None:
            p = self._tower_last
            bounds = (
                100,
                max(570, p.top - 45),
                1820,
                min(1060, p.bottom + 45),
            )
            tower = self._matches(detail, self._tracking_variants(self._tower_template), bounds, 0.75, self._tower_kind)
            if self._tower_full_height is not None:
                tower = [replace(p, bottom=p.top + self._tower_full_height) for p in tower]
            tower = receiving_faces(tower)
        if not tower:
            tower = receiving_faces(self._matches(detail, tower_templates, (100, 570, 1820, 1060), 0.40))
        if not tower:
            return None
        # The tower is one column. A weak match on scenery must not outrank a
        # clear sculpture just because it is higher in the expanded search ROI.
        # Require comparable reference evidence before caching that higher
        # crop: a railing can otherwise become its own near-perfect template.
        anchor = max(tower, key=lambda p: p.confidence)
        tower = [
            p
            for p in tower
            if abs(p.x - anchor.x) <= max(60, anchor.width * 0.6)
            and p.confidence >= max(confidence_floor(p), anchor.confidence * 0.90)
        ]
        if not tower:
            return None
        top = min(tower, key=lambda p: p.top)
        self._tower_last = top
        self._tower_full_height = top.bottom - top.top
        face = replace(top, bottom=top.top + max(50, min(80, self._tower_full_height * 0.45)))
        # Track the receiving face. At high sway the torso and feet rotate
        # around it, and matching the whole body can shift the top's anchor.
        # Keep the full height separately so a tracking crop is not a new body.
        self._tower_template = self._piece_template(detail, face)
        self._track_carried(detail, carried)
        self._held_kind, self._tower_kind = carried.kind, top.kind
        return JengaObservation(carried, top, bird)

    def _track_carried(self, detail, carried):
        """Keep a full body anchor while tracking its stable upper texture."""
        if self._held_full_height is None:
            height = carried.bottom - carried.top
            if 160 <= height <= 222 and (carried.kind or 1.2 <= height / max(1, carried.width) <= 1.6):
                samples = self._held_shape_samples
                if samples and (
                    abs(height - np.median([p.bottom - p.top for p in samples])) > 6
                    or abs(carried.width - np.median([p.width for p in samples])) > 8
                ):
                    samples.clear()
                if not samples or abs(carried.x - samples[-1].x) > 2 or abs(carried.top - samples[-1].top) > 2:
                    samples.append(carried)
                del samples[:-8]
                if len(samples) >= 5 and np.ptp([p.x for p in samples]) >= 80:
                    self._held_full_height = float(np.median([p.bottom - p.top for p in samples]))
                    self._held_full_width = float(np.median([p.width for p in samples]))
                    if carried.kind == "rabbit":
                        self._held_face_offset = self._held_full_height - 80
                    else:
                        self._held_face_offset = self._held_full_height * 0.25 if self._held_full_height > 185 else 8.0
        if self._held_full_height is None:
            self._held_template = self._piece_template(detail, carried)
        else:
            top = carried.top + self._held_face_offset
            face = replace(carried, top=top, bottom=top + min(80, self._held_full_height * 0.45))
            self._held_template = self._piece_template(detail, face)

    @staticmethod
    def _piece_template(detail, piece):
        return detail[
            round(piece.top / 2) : round(piece.bottom / 2),
            round((piece.x - piece.width / 2) / 2) : round((piece.x + piece.width / 2) / 2),
        ].copy()

    @staticmethod
    def _tracking_variants(template):
        # A high tower can turn several degrees while a frame is being
        # processed. Keep the same receiving-face anchor during reacquisition
        # instead of switching to a differently cropped full-body reference.
        h, w = template.shape
        return [
            cv2.warpAffine(
                template,
                cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1),
                (w, h),
                borderMode=cv2.BORDER_REFLECT,
            )
            for angle in (-8, 0, 8)
        ]

    def reset_tracking(self):
        self.pixels_per_unit = None
        self._tower_last = None
        self._tower_template = None
        self._tower_full_height = None
        self._held_template = None
        self._held_full_height = None
        self._held_full_width = None
        self._held_face_offset = None
        self._held_shape_samples.clear()
        self._held_kind = self._tower_kind = ""
        self._identity_retry = 0
        self._identity_candidate = ""
        self._identity_votes = 0
        self._identity_strong_votes = 0
        self._identity_extent = None
        self._identity_pixels = None

    def learn(self, frame, piece):
        """Only learn a repeatedly observed carried piece at the confirmed release."""
        frame = self.normalize(frame)
        x1, x2 = round(piece.x - piece.width / 2), round(piece.x + piece.width / 2)
        gray = cv2.cvtColor(frame[round(piece.top) : round(piece.bottom), x1:x2], cv2.COLOR_BGR2GRAY)
        variants = self._variants(gray)
        self.learned.append(variants)
        self._template_kinds.update((id(template), piece.kind) for template in variants)
        for group in self.learned[:-3]:
            for template in group:
                self._template_kinds.pop(id(template), None)
        self.learned = self.learned[-3:]
        # Reacquire the new upper appearance. Keep the previous height bound:
        # the camera follows the new top, while its predecessor moves down by a
        # full sculpture and must not become the receiving face again.
        self._tower_template = None
        self._tower_kind = piece.kind
        self._held_template = None
        self._held_full_height = None
        self._held_full_width = None
        self._held_face_offset = None
        self._held_shape_samples.clear()
        self._held_kind = ""
        self._identity_retry = 0
        self._identity_candidate = ""
        self._identity_votes = 0
        self._identity_strong_votes = 0
        self._identity_extent = None
        self._identity_pixels = None
