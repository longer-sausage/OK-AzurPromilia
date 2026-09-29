"""花瓣音游的颜色/形状识别，使用说明图标校准 UI 的等比缩放及偏移。

只处理像素，不发送输入。轨道上下有浮动，使用整条带而非单行采样。
"""

from dataclasses import dataclass
from itertools import combinations

import cv2
import numpy as np

COLORS = {
    "blue": ((85, 45, 150), (112, 220, 255)),
    "red": ((155, 45, 150), (179, 220, 255)),
    "purple": ((113, 45, 150), (145, 220, 255)),
}
JUDGE_X = 587.0
LEGEND_Y = (678, 726, 774)


@dataclass(frozen=True)
class RhythmLayout:
    frame_size: tuple[int, int]
    scale: float
    x_offset: float
    y_offset: float
    legend: tuple[tuple[float, float], ...] = ()

    @classmethod
    def for_frame(cls, frame):
        height, width = frame.shape[:2]
        scale = min(width / 1920, height / 1080)
        return cls((height, width), scale, (width - 1920 * scale) / 2, (height - 1080 * scale) / 2)

    def crop(self, frame, x1, y1, x2, y2):
        # 只归一化小 ROI；不拉伸整张截图，也不假定截图本身为 16:9。
        transform = np.float32(
            [
                [1 / self.scale, 0, -self.x_offset / self.scale - x1],
                [0, 1 / self.scale, -self.y_offset / self.scale - y1],
            ]
        )
        # WGC 返回 BGRA；先裁成小 ROI 再去掉 alpha，避免每帧复制整张 4K 截图。
        return cv2.warpAffine(frame, transform, (x2 - x1, y2 - y1))[:, :, :3]


@dataclass(frozen=True)
class RhythmNote:
    color: str
    head: float
    tail: float
    y: float
    long: bool
    clipped: bool = False

    @property
    def keys(self):
        return {"blue": ("q",), "red": ("e",), "purple": ("q", "e")}[self.color]


class RhythmDetector:
    def __init__(self):
        self.layout = None
        self._glyphs = {}

    @staticmethod
    def _valid_frame(frame):
        return frame is not None and frame.ndim == 3 and frame.shape[2] >= 3 and min(frame.shape[:2]) >= 120

    @staticmethod
    def _legend_visible(frame, layout):
        radius = max(2, round(14 * layout.scale))
        height, width = frame.shape[:2]
        for color, (cx, cy) in zip(COLORS, layout.legend, strict=True):
            x, y = round(cx), round(cy)
            if not (radius <= x < width - radius and radius <= y < height - radius):
                return False
            patch = frame[y - radius : y + radius, x - radius : x + radius, :3]
            hsv = cv2.cvtColor(cv2.resize(patch, (28, 28)), cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, *COLORS[color])
            white = cv2.inRange(hsv, (0, 0, 225), (179, 45, 255))
            if np.mean(mask > 0) < 0.30 or np.mean(white > 0) < 0.025:
                return False
        return True

    def _find_layout(self, frame):
        height, width = frame.shape[:2]
        base = RhythmLayout.for_frame(frame)
        centers = tuple((width - 60 * base.scale, base.y_offset + y * base.scale) for y in LEGEND_Y)
        base = RhythmLayout(base.frame_size, base.scale, base.x_offset, base.y_offset, centers)
        if self._legend_visible(frame, base):
            return base
        x0, y0, y1 = round(width * 0.65), round(height * 0.35), round(height * 0.88)
        resize = min(1.0, 1080 / height)
        strip = cv2.resize(frame[y0:y1, x0:, :3], None, fx=resize, fy=resize)
        hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
        candidates = {}
        for color, bounds in COLORS.items():
            mask = cv2.inRange(hsv, *bounds)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            candidates[color] = []
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if not (5 <= w <= 48 and 5 <= h <= 48 and 0.65 <= w / h <= 1.5):
                    continue
                if cv2.contourArea(contour) / (w * h) < 0.35:
                    continue
                white = cv2.inRange(hsv[y : y + h, x : x + w], (0, 0, 225), (179, 45, 255))
                if np.mean(white > 0) < 0.025:
                    continue
                # 红/紫色的白色乐符切掉掩膜下缘，包围圆比 bbox 中点更准确。
                (cx, cy), radius = cv2.minEnclosingCircle(contour)
                candidates[color].append((cx, cy, radius))

        layouts = []
        # 背景/命中特效可能粘连其中一枚图标；用任意两枚定位，仍须校验全部三枚。
        for (i, first_color), (j, second_color) in combinations(enumerate(COLORS), 2):
            for first in candidates[first_color]:
                for second in candidates[second_color]:
                    radius = (first[2] + second[2]) / 2
                    gap = (second[1] - first[1]) / (j - i)
                    if not radius * 2 < gap < radius * 3.8:
                        continue
                    if abs(first[0] - second[0]) > radius * 0.4 or abs(first[2] - second[2]) > radius * 0.3:
                        continue
                    scale = gap / resize / 48
                    cx = (first[0] + second[0]) / 2 / resize + x0
                    y_offset = first[1] / resize + y0 - LEGEND_Y[i] * scale
                    centers = tuple((cx, y_offset + y * scale) for y in LEGEND_Y)
                    layout = RhythmLayout((height, width), scale, (width - 1920 * scale) / 2, y_offset, centers)
                    if self._legend_visible(frame, layout):
                        error = abs(first[0] - second[0]) / radius + abs(first[2] - second[2]) / radius
                        layouts.append((error, layout))
        return min(layouts, key=lambda item: item[0], default=(None, None))[1]

    @staticmethod
    def _touching_tail(mask, bright, left):
        """用长条圆角的上轮廓还原尾心；相邻短音符可覆盖圆角右半边。"""
        if left < 80:
            return None
        rows = np.arange(mask.shape[0])[:, None]
        top = np.where(mask > 0, rows, mask.shape[0]).min(axis=0)
        bright_top = np.where(bright > 0, rows, mask.shape[0]).min(axis=0)
        columns = np.arange(left - 45, min(left + 13, mask.shape[1]))
        base = np.median(top[left - 80 : left - 45])
        tails = np.arange(left - 45, left + 15, 0.5)
        dx = np.maximum(columns[None, :] - tails[:, None], 0)
        curve = base + 33.5 - np.sqrt(np.maximum(33.5**2 - dx**2, 0))
        curve[dx > 33.5] = mask.shape[0]
        prediction = np.minimum(curve, bright_top[columns])
        errors = np.mean(np.minimum(abs(prediction - top[columns]), 8), axis=1)
        best = int(np.argmin(errors))
        return float(515 + tails[best]) if errors[best] < 2.5 else None

    def _covered_body(self, mask, bright, circles, x, width):
        """头部被命中特效连上 ROI 边界时，仍从右侧清晰的身体更新长条尾部。"""
        if x > 100 or width < 220:
            return None
        start = x + min(360, width - 110)
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask[:, start : x + width])
        for bx, y, w, h, area in stats[1:]:
            if bx > 2 or w < 110 or not 45 <= h <= 105 or y <= 0 or area / (w * h) < 0.50:
                continue
            tail = float(515 + start + bx + w - 33.5)
            following = [
                cx for cx, cy, cw, _ in circles if cx > start + 30 and cx + cw <= x + width + 2 and abs(cy - y) < 15
            ]
            if following:
                fitted = self._touching_tail(mask, bright, min(following))
                tail = fitted if fitted is not None else float(515 + min(following) - 10.5)
            # 这里只代表进入判定圈后的身体；不会在清晰区创建新轨迹。
            return tail, float(475 + y + h / 2), start + bx + w >= mask.shape[1] - 2
        return None

    def is_active(self, frame):
        if not self._valid_frame(frame):
            self.layout = None
            return False
        # 右侧三枚说明图标固定不动；底部大按键会随命中特效缩放，不能用作闸门。
        if self.layout and self.layout.frame_size == frame.shape[:2] and self._legend_visible(frame, self.layout):
            return True
        self.layout = self._find_layout(frame)
        return self.layout is not None

    def track_band(self, frame):
        layout = self.layout or RhythmLayout.for_frame(frame)
        return layout.crop(frame, 515, 475, 1500, 600)[::4, ::4]

    @staticmethod
    def _opaque_head(hsv, color, cx, cy):
        cx, cy = round(cx), round(cy)
        patch = hsv[max(0, cy - 30) : cy + 31, max(0, cx - 30) : cx + 31]
        white = cv2.inRange(patch, (0, 0, 245), (179, 30, 255))
        lower, upper = COLORS[color]
        colored = cv2.inRange(patch, (lower[0], 45, 200), upper)
        saturation = patch[:, :, 1][colored > 0]
        return (
            np.count_nonzero(white) >= 120
            and len(saturation) >= 300
            and np.quantile(saturation, 0.9)
            >= {
                "blue": 125,
                "red": 95,
                "purple": 105,
            }[color]
        )

    def _colored_hold(self, hsv, high, color, circle, circles):
        """按头部高度隔离身体，避免任一种颜色的背景粘连到音符。"""
        x, y, w, h = circle
        center = y + h // 2
        start = x + w - 5
        stop = min(start + 160, hsv.shape[1])
        if stop - start < 120:
            return None
        lower, upper = COLORS[color]
        mask = cv2.inRange(hsv, (lower[0], 25, 180), upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        support = mask[max(0, center - 20) : center + 20, start:stop]
        if np.count_nonzero(support) / support.size < 0.50:
            return None
        top, bottom = max(0, y - 5), min(hsv.shape[0], y + h + 5)
        _, labels, stats, _ = cv2.connectedComponentsWithStats(mask[top:bottom])
        head_labels = labels[max(0, y - top) : y - top + h, x : x + w]
        counts = np.bincount(head_labels.ravel())
        if len(counts) <= 1:
            return None
        label = int(np.argmax(counts[1:]) + 1)
        bx, _, bw, bh, area = stats[label]
        if bx > x or bx + bw < stop or area / (bw * bh) < 0.50:
            return None
        tail = float(515 + bx + bw - 33.5)
        cap = self._tail_cap(hsv, high, color, max(515 + x + w / 2 + 85, tail - 180), tail + 180)
        if cap is None and any(25 < cx + cw / 2 - x - w / 2 < 230 for cx, cy, cw, ch in circles):
            return None
        # 轨道右侧的渐隐及其它音符遮挡都可能切断身体，颜色边界只是尾部下界。
        return RhythmNote(
            color,
            float(515 + x + w / 2),
            cap[0] if cap else tail,
            float(475 + center),
            True,
            cap is None,
        )

    def _tail_cap(self, hsv, high, color, left, right):
        template = self._glyphs.get(color)
        if template is None:
            return None
        start, stop = max(0, round(left - 515 - 24)), min(hsv.shape[1], round(right - 515 + 25))
        if stop - start < 49:
            return None
        scores = cv2.matchTemplate(high[:, start:stop], template, cv2.TM_CCOEFF_NORMED)
        for _ in range(8):
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score < 0.54:
                break
            cx, cy = x + start + 24, y + 24
            lower, upper = COLORS[color]
            patch = hsv[max(0, cy - 27) : cy + 28, cx - 30 : cx + 31]
            white = cv2.inRange(patch, (0, 0, 245), (179, 30, 255))
            beam = hsv[max(0, cy - 15) : cy + 16, max(0, cx - 100) : max(0, cx - 38)]
            # 尾符被游戏置灰；颜色只检查它左侧的身体，不检查灰色图案本身。
            if np.count_nonzero(white) < 160 and beam.size:
                support = cv2.inRange(beam, (lower[0], 20, 140), upper)
                if np.mean(support > 0) > 0.50:
                    return float(cx + 515), float(cy + 475)
            scores[max(0, y - 20) : y + 21, max(0, x - 25) : x + 26] = 0
        return None

    def _glyph_heads(self, hsv, high, color):
        """颜色掩膜连到场景时，用本轮清晰头部的乐符找回圆心。"""
        template = self._glyphs.get(color)
        if template is None:
            return []
        scores = cv2.matchTemplate(high, template, cv2.TM_CCOEFF_NORMED)
        heads = []
        lower, upper = COLORS[color]
        for _ in range(12):
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score < 0.58:
                break
            cx, cy = x + 24, y + 24
            if 807 < cx + 515 < 1500 and 33 <= cy < high.shape[0] - 33:
                patch = hsv[cy - 27 : cy + 28, cx - 27 : cx + 28]
                white = cv2.inRange(patch, (0, 0, 245), (179, 30, 255))
                colored = cv2.inRange(patch, (lower[0], lower[1], 180), upper)
                if (
                    np.count_nonzero(white) >= 160
                    and np.mean(colored > 0) > 0.25
                    and self._opaque_head(hsv, color, cx, cy)
                ):
                    heads.append((cx - 33, cy - 33, 67, 67))
            scores[max(0, y - 20) : y + 21, max(0, x - 25) : x + 26] = 0
        return heads

    def detect(self, frame):
        if not self._valid_frame(frame):
            return []
        if self.layout is None or self.layout.frame_size != frame.shape[:2]:
            self.layout = self._find_layout(frame)
        layout = self.layout or RhythmLayout.for_frame(frame)
        # 右侧清晰区用于建轨迹，完整轨道用于量长条尾部；裁到 1500 会低估长按时长。
        roi = layout.crop(frame, 515, 475, 1900, 600)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
        high = gray - cv2.GaussianBlur(gray, (0, 0), 4)
        notes = []
        for color, bounds in COLORS.items():
            mask = cv2.inRange(hsv, *bounds)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
            _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
            # 圆形音符不透明，长条身体半透明。高亮掩膜能拆开粘连的同色音符。
            bright = cv2.inRange(hsv, (bounds[0][0], bounds[0][1], 240), bounds[1])
            bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
            _, _, bright_stats, _ = cv2.connectedComponentsWithStats(bright)
            circles = [
                (int(cx), int(cy), int(cw), int(ch))
                for cx, cy, cw, ch, ca in bright_stats[1:]
                if 50 <= cw <= 80
                and 45 <= ch <= 85
                and 0.8 <= cw / ch <= 1.25
                and ca >= 1200
                and self._opaque_head(hsv, color, cx + cw / 2, cy + ch / 2)
            ]
            if color not in self._glyphs:
                for x, y, w, h in circles:
                    cx, cy = round(x + w / 2), round(y + h / 2)
                    if not 807 < cx + 515 < 1400 or cy < 24 or cy + 25 > high.shape[0]:
                        continue
                    white = cv2.inRange(hsv[y : y + h, x : x + w], (0, 0, 240), (179, 30, 255))
                    if np.count_nonzero(white) >= 80:
                        self._glyphs[color] = high[cy - 24 : cy + 25, cx - 24 : cx + 25].copy()
                        break
            for circle in self._glyph_heads(hsv, high, color):
                if not any(abs(circle[0] + circle[2] / 2 - cx - cw / 2) < 20 for cx, cy, cw, ch in circles):
                    circles.append(circle)
            for x, y, w, h, area in stats[1:]:
                long = w > 100
                if long and (y <= 0 or h > 105) and (body := self._covered_body(mask, bright, circles, int(x), int(w))):
                    tail, center_y, clipped = body
                    notes.append(RhythmNote(color, 548.5, tail, center_y, True, clipped))
                max_h = 105 if long else 85
                if not (45 <= h <= max_h and w >= 43 and area >= 1200):
                    continue
                if y <= 0:
                    continue
                if y + h >= roi.shape[0]:
                    if not long or y > 55 or area / (w * h) < 0.50:
                        continue
                elif area / (w * h) < 0.38:
                    continue
                # 单音符的红色掩膜底部被白色图案分割，圆心应取宽度而非高度。
                radius = 33.5 if long else w / 2
                head = float(515 + x + radius)
                tail = float(515 + x + w - radius)
                if long:
                    following = [
                        (cx, cy, cw, ch)
                        for cx, cy, cw, ch in circles
                        if (cx - x > 30) and cx + cw <= x + w + 2 and abs(cy - y) < 15
                    ]
                    for cx, cy, cw, ch in following:
                        center = float(515 + cx + cw / 2)
                        notes.append(RhythmNote(color, center, center, float(475 + cy + ch / 2), False))
                    if following:
                        first_cx = min(cx for cx, _, _, _ in following)
                        fitted_tail = self._touching_tail(mask, bright, first_cx)
                        tail = fitted_tail if fitted_tail is not None else float(515 + first_cx - 10.5)
                # 花瓣背景也可能有蓝色，要求音符头部内有白色乐符。
                core = hsv[y : y + h, x : x + min(w, 67)]
                white = cv2.inRange(core, (0, 0, 230), (179, 45, 255))
                # 长条被背景切断后的身体不是另一枚头部；清晰区内的长条也须有乐符。
                if (not long or head >= JUDGE_X + 220) and np.count_nonzero(white) < 80:
                    continue
                if head >= JUDGE_X + 220 and not self._opaque_head(hsv, color, head - 515, y + h / 2):
                    continue
                # 场景连通域的左边缘会偏离真正圆心；已找到的圆形头部负责输出。
                if head >= JUDGE_X + 220 and any(abs(head - 515 - cx - cw / 2) < 50 for cx, cy, cw, ch in circles):
                    continue
                clipped = x + w >= roi.shape[1] - 2
                if long:
                    cap = self._tail_cap(hsv, high, color, max(head + 85, tail - 180), tail + 180)
                    if cap:
                        tail = cap[0]
                    elif tail > 1600:
                        clipped = True
                notes.append(RhythmNote(color, head, tail, float(475 + y + h / 2), long, clipped))
            # 蓝色场景会连上普通颜色掩膜。圆形音符不透明，用高亮掩膜单独找回，
            # 并与长条头部/已经拆出的短音符去重。
            for x, y, w, h in circles:
                head = float(515 + x + w / 2)
                if head >= JUDGE_X + 220:
                    white = cv2.inRange(hsv[y : y + h, x : x + w], (0, 0, 230), (179, 45, 255))
                    if np.count_nonzero(white) >= 80 and (
                        hold := self._colored_hold(hsv, high, color, (x, y, w, h), circles)
                    ):
                        notes = [item for item in notes if item.color != color or abs(item.head - head) >= 25]
                        notes.append(hold)
                if (
                    y <= 0
                    or y + h >= roi.shape[0]
                    or any(item.color == color and abs(item.head - head) < 25 for item in notes)
                ):
                    continue
                white = cv2.inRange(hsv[y : y + h, x : x + w], (0, 0, 230), (179, 45, 255))
                if np.count_nonzero(white) >= 80:
                    notes.append(RhythmNote(color, head, head, float(475 + y + h / 2), False))
            # 被按下后的头部隐藏在特效中，独立找出与左侧身体连接的淡色尾符。
            cap = self._tail_cap(hsv, high, color, 650, 1800)
            if cap and not any(
                n.color == color and (807 <= n.head < cap[0] - 75 or (n.long and abs(n.tail - cap[0]) < 45))
                for n in notes
            ):
                notes.append(RhythmNote(color, 548.5, cap[0], cap[1], True))
        # 远端淡入的头部尚不完整；只用扩展区域量尾部，仍在清晰区跟踪音符头部。
        return sorted((note for note in notes if note.head <= 1500), key=lambda note: note.head)
