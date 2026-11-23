"""真实游戏截图回归 + 连续音符/遮挡/长条/停止输入的时序测试。"""

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from src.config import config
from src.image.rhythm_detector import RhythmDetector, RhythmNote
from src.tasks.trigger.auto_rhythm_task import AutoRhythmTask, RhythmPlayer

FIXTURES = Path(__file__).parent / "fixtures" / "rhythm"


def note(x, color="blue", length=0):
    return RhythmNote(color, x, x + length, 535, length > 0)


def task_harness():
    executor, app = MagicMock(), MagicMock()
    app.tr.side_effect = lambda text: text
    executor.paused = False
    executor.exit_event = threading.Event()
    task = AutoRhythmTask(executor, app)
    task._enabled = True
    task.config = dict(task.default_config)
    return task


def ui_frame(source, width, height, top=0, bottom=0):
    """模拟游戏 Canvas 等比缩放：轨道居中，说明图标靠右，窗口边框另占高度。"""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    scale = min(width / 1920, (height - top - bottom) / 1080)
    ox = (width - 1920 * scale) / 2
    oy = top + (height - top - bottom - 1080 * scale) / 2

    def place(x1, y1, x2, y2, offset_x):
        patch_source = source[y1:y2, x1:x2]
        if not np.any(patch_source):
            # touching_red 只保留轨道；补上同一游戏的固定说明图标。
            patch_source = cv2.imread(str(FIXTURES / "blue_red.png"))[y1:y2, x1:x2]
        patch = cv2.resize(patch_source, (round((x2 - x1) * scale), round((y2 - y1) * scale)))
        x, y = round(offset_x + x1 * scale), round(oy + y1 * scale)
        frame[y : y + patch.shape[0], x : x + patch.shape[1]] = patch

    place(450, 450, 1540, 630, ox)
    place(1835, 650, 1885, 800, width - 1920 * scale)
    return frame, scale, ox, oy


class TestRhythmVision(unittest.TestCase):
    def setUp(self):
        self.detector = RhythmDetector()

    def test_real_frames_at_multiple_resolutions(self):
        for scale in (0.667, 1.0, 1.333):
            for name, colors in (
                ("blue_red", {"blue", "red"}),
                ("purple", {"purple"}),
                ("long_red", {"red"}),
                ("long_blue", {"blue"}),
            ):
                with self.subTest(scale=scale, name=name):
                    frame = cv2.imread(str(FIXTURES / f"{name}.png"))
                    frame = cv2.resize(frame, None, fx=scale, fy=scale)
                    self.assertTrue(self.detector.is_active(frame))
                    notes = self.detector.detect(frame)
                    self.assertEqual({item.color for item in notes}, colors)
                    if name == "long_red":
                        long = next(item for item in notes if item.long)
                        self.assertAlmostEqual(long.head, 897.5, delta=3)
                        self.assertAlmostEqual(long.tail, 1256.5, delta=3)
                    elif name == "long_blue":
                        long = next(item for item in notes if item.long)
                        self.assertAlmostEqual(long.head, 557.5, delta=3)
                        self.assertAlmostEqual(long.tail, 1250.5, delta=3)

    def test_world_rejected_effect_animation_accepted(self):
        self.assertFalse(self.detector.is_active(cv2.imread(str(FIXTURES / "world.png"))))
        self.assertTrue(self.detector.is_active(cv2.imread(str(FIXTURES / "hit_effect.png"))))
        self.assertFalse(self.detector.is_active(None))

    def test_long_tail_touching_next_same_color_note(self):
        for scale in (0.667, 1.0, 1.333):
            frame = cv2.imread(str(FIXTURES / "touching_red.png"))
            notes = self.detector.detect(cv2.resize(frame, None, fx=scale, fy=scale))
            self.assertEqual(len(notes), 3)
            self.assertEqual([item.long for item in notes], [True, False, False])
            self.assertAlmostEqual(notes[0].tail - notes[0].head, 405, delta=4)
            self.assertAlmostEqual(notes[1].head - notes[0].tail, 45, delta=4)

    def test_touching_red_shifted_separation(self):
        frame = cv2.imread(str(FIXTURES / "touching_red.png"))
        h, w = frame.shape[:2]
        # 长条头部移出 ROI 区域（shift 480~530）时，紧随的短音符不被长条吞噬
        for shift in (480, 500, 520):
            with self.subTest(shift=shift):
                M = np.float32([[1, 0, -shift], [0, 1, 0]])
                shifted = cv2.warpAffine(frame, M, (w, h))
                notes = self.detector.detect(shifted)
                long_notes = [item for item in notes if item.long]
                short_notes = [item for item in notes if not item.long]
                self.assertEqual(len(long_notes), 1)
                self.assertTrue(any(abs(item.head - (1112.5 - shift)) < 10 for item in short_notes))
                self.assertAlmostEqual(long_notes[0].tail, 1068.5 - shift, delta=4)

    def test_canvas_anchors_at_small_wide_tall_and_windowed_resolutions(self):
        for name in ("blue_red", "purple", "long_red", "touching_red"):
            source = cv2.imread(str(FIXTURES / f"{name}.png"))
            expected = RhythmDetector().detect(source)
            for width, height in (
                (640, 360),
                (800, 600),
                (1280, 720),
                (1920, 1002),
                (1920, 1200),
                (2560, 1080),
                (3440, 1440),
                (3840, 2160),
                (5120, 1440),
                (1080, 1920),
            ):
                with self.subTest(name=name, size=(width, height)):
                    frame, scale, ox, _ = ui_frame(source, width, height)
                    self.assertTrue(self.detector.is_active(frame))
                    self.assertAlmostEqual(
                        self.detector.layout.x_offset + 587 * self.detector.layout.scale, ox + 587 * scale, delta=3
                    )
                    actual = self.detector.detect(frame)
                    self.assertEqual(
                        [(n.color, bool(n.long)) for n in actual], [(n.color, bool(n.long)) for n in expected]
                    )
                    for found, reference in zip(actual, expected, strict=True):
                        self.assertAlmostEqual(found.head, reference.head, delta=5)
                        self.assertAlmostEqual(found.tail, reference.tail, delta=5)

    def test_window_decorations_and_bgra_capture(self):
        source = cv2.imread(str(FIXTURES / "long_red.png"))
        frame, _, _, _ = ui_frame(source, 1920, 1080, top=32, bottom=46)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        self.assertTrue(self.detector.is_active(frame))
        long = next(n for n in self.detector.detect(frame) if n.long)
        self.assertAlmostEqual(long.head, 897.5, delta=5)
        self.assertAlmostEqual(long.tail, 1256.5, delta=5)

    def test_other_pc_recording_recovers_blue_notes_and_long_tail(self):
        frame = cv2.imread(str(FIXTURES / "other_pc_notes.png"))
        self.assertTrue(self.detector.is_active(frame))
        notes = self.detector.detect(frame)
        blues = [n for n in notes if n.color == "blue"]
        self.assertEqual(len(blues), 2)
        self.assertAlmostEqual(blues[0].head, 975, delta=3)
        self.assertAlmostEqual(blues[1].head, 1204, delta=3)
        self.assertFalse(any(n.long for n in blues))
        frame = cv2.imread(str(FIXTURES / "other_pc_hold.png"))
        long = next(n for n in self.detector.detect(frame) if n.color == "red" and n.long)
        self.assertGreater(long.tail, 1600)  # 尾部超出旧的 1500 裁剪范围。
        # 颜色边界已进入渐隐区域，实际淡色尾符还在其右侧，不能确认松键点。
        self.assertTrue(long.clipped)
        frame = cv2.imread(str(FIXTURES / "other_pc_purple.png"))
        self.assertTrue(self.detector.is_active(frame))
        self.assertEqual([n.keys for n in self.detector.detect(frame)], [("q", "e"), ("q", "e")])

    def test_covered_long_head_still_reports_body_tail(self):
        frame = cv2.imread(str(FIXTURES / "other_pc_effect_body.png"))
        self.assertTrue(self.detector.is_active(frame))
        long = next(n for n in self.detector.detect(frame) if n.color == "red" and n.long)
        self.assertLess(long.head, 587)
        self.assertAlmostEqual(long.tail, 1455.5, delta=5)

    def test_blue_hold_connected_to_scene_keeps_head_and_unknown_tail(self):
        frame = cv2.imread(str(FIXTURES / "local_pc_blue_hold.png"))
        self.assertTrue(self.detector.is_active(frame))
        long = next(n for n in self.detector.detect(frame) if n.color == "blue" and n.long)
        self.assertAlmostEqual(long.head, 1043, delta=5)
        self.assertGreater(long.tail, 1400)
        self.assertTrue(long.clipped)

    def test_purple_hold_connected_to_background_has_a_gray_tail(self):
        frame = cv2.imread(str(FIXTURES / "local_pc_purple_hold.png"))
        self.assertTrue(self.detector.is_active(frame))
        long = next(n for n in self.detector.detect(frame) if n.color == "purple")
        self.assertTrue(long.long)
        self.assertAlmostEqual(long.head, 1052, delta=4)
        self.assertAlmostEqual(long.tail, 1740, delta=4)
        self.assertFalse(long.clipped)

    def test_missing_legend_and_invalid_frames_clear_layout(self):
        source = cv2.imread(str(FIXTURES / "blue_red.png"))
        self.assertTrue(self.detector.is_active(source))
        missing = source.copy()
        missing[700:750, 1835:1885] = 0
        self.assertFalse(self.detector.is_active(missing))
        self.assertIsNone(self.detector.layout)
        for frame in (None, np.zeros((100, 100, 3), dtype=np.uint8), np.zeros((1080, 1920), dtype=np.uint8)):
            with self.subTest(shape=None if frame is None else frame.shape):
                self.assertFalse(self.detector.is_active(frame))
                self.assertEqual(self.detector.detect(frame), [])


class TestRhythmTiming(unittest.TestCase):
    def test_capture_stall_keeps_a_recent_note_but_discards_an_expired_one(self):
        for delay, expected in ((0.07, "red"), (0.20, None)):
            with self.subTest(delay=delay):
                player = RhythmPlayer()
                for index in range(9):
                    at = index * 0.1
                    player.update(
                        [note(1250 - 600 * at), note(1307 - 600 * at, "red")],
                        at,
                        lead=player.speed * 0.05,
                    )
                _, first = player.update([], player.next_deadline(0.05) + 0.0001, lead=30)
                self.assertEqual(first.color, "blue")
                player.update([], player.release_at, lead=30)
                _, second = player.update([], (1307 - 587) / 600 + delay, lead=30)
                self.assertEqual(None if second is None else second.color, expected)

    def test_expired_track_does_not_move_a_timer_deadline(self):
        player = RhythmPlayer()
        for now in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
            notes = [note(1337 - 600 * now, "red")]
            if now <= 0.2:
                notes.insert(0, note(1100 - 720 * now))
            player.update(notes, now, lead=player.speed * 0.05)
        colors = []
        speed = player.speed
        for _ in range(4):
            due = player.next_deadline(0.05)
            _, hit = player.update([], due + 0.0001, lead=player.speed * 0.05)
            if hit:
                colors.append(hit.color)
            self.assertEqual(player.speed, speed)
        self.assertEqual(colors, ["blue", "red"])

    def test_body_fragment_does_not_advance_release_by_half_a_second(self):
        player = RhythmPlayer()
        for index in range(300):
            now = index / 100
            head = 1250 - 600 * now
            notes = [note(head, "red", 850)]
            if 1.6 <= now <= 2.2:
                notes = [RhythmNote("red", 548.5, 720.5, 535, True)]
            keys, _ = player.update(notes, now, lead=player.speed * 0.05)
            if 1.1 <= now <= 2.4:
                self.assertEqual(keys, ("e",))
        self.assertEqual(player.held, ())

    def test_clipped_tail_waits_for_reliable_visible_tail(self):
        player = RhythmPlayer()
        hits = 0
        for index in range(420):
            now = index / 100
            head = 1250 - 600 * now
            tail = head + 1500
            measured = RhythmNote("blue", max(head, 550), min(tail, 1750), 535, True, tail > 1750)
            keys, hit = player.update([measured] if tail > 590 else [], now, lead=player.speed * 0.05)
            hits += hit is not None
            if 1.1 <= now <= 3.4:
                self.assertEqual(keys, ("q",))
        self.assertEqual(hits, 1)
        self.assertEqual(player.held, ())

    def test_initial_missing_body_can_be_confirmed_before_the_effect_area(self):
        player = RhythmPlayer()
        hits = []
        for index in range(220):
            now = index / 100
            head = 1390 - 600 * now
            _, hit = player.update([note(head, "blue", 0 if index < 5 else 360)], now, lead=30)
            if hit:
                hits.append(hit)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].long)

    def test_next_opposite_note_releases_the_previous_long_note(self):
        player = RhythmPlayer()
        hits = []
        for index in range(300):
            now = index * 0.01
            head = 1250 - 600 * now
            notes = [note(head, "blue", 600), note(head + 600, "red"), note(head + 780, "red")]
            keys, hit = player.update(notes, now, lead=player.speed * 0.05)
            if hit:
                hits.append((now, hit.color))
                if hit.color == "red":
                    self.assertEqual(keys, ("e",))
            if 1.1 <= now <= 2.0:
                self.assertIn("q", keys)
        self.assertEqual([color for _, color in hits], ["blue", "red", "red"])
        self.assertEqual(player.held, ())

    def test_adjacent_long_notes_release_before_the_next_color(self):
        player = RhythmPlayer()
        hits = []
        for index in range(340):
            now = index * 0.01
            head = 1250 - 600 * now
            keys, hit = player.update(
                [note(head, "blue", 600), note(head + 600, "red", 600)], now, lead=player.speed * 0.05
            )
            if hit:
                hits.append(hit.color)
            if 1.4 <= now <= 2.0:
                self.assertEqual(keys, ("q",))
            elif 2.1 <= now <= 2.8:
                self.assertEqual(keys, ("e",))
        self.assertEqual(hits, ["blue", "red"])
        self.assertEqual(player.held, ())

    def test_different_frame_rates_speeds_and_processing_delays(self):
        for fps in (10, 15, 20, 30, 60, 144):
            for speed in (360, 600, 720, 1200):
                for delay in (0.0, 0.025, 0.09):
                    with self.subTest(fps=fps, speed=speed, delay=delay):
                        player = RhythmPlayer()
                        hits = []
                        last_now = 0.0
                        for i in range(round(2.3 * fps)):
                            captured_at = i / fps
                            now = captured_at + delay
                            for _ in range(20):
                                due = player.next_deadline(0.05)
                                if due > now:
                                    break
                                tick = max(due, last_now + 0.000001)
                                _, hit = player.update([], tick, lead=player.speed * 0.05)
                                if hit:
                                    hits.append(tick)
                                last_now = tick
                            notes = [note(1100 + offset - speed * captured_at) for offset in (0, 180)]
                            _, hit = player.update(notes, now, lead=player.speed * 0.05, observed_at=captured_at)
                            if hit:
                                hits.append(now)
                            last_now = now
                        self.assertEqual(len(hits), 2)
                        for at, offset in zip(hits, (0, 180), strict=True):
                            self.assertAlmostEqual(at, (1100 + offset - 587) / speed - 0.05, delta=0.008)

    def test_only_selected_due_note_is_consumed(self):
        player = RhythmPlayer()
        player.update([note(1100), note(1140)], 0)
        player.update([note(1028), note(1068)], 0.1)
        player.update([note(956), note(996)], 0.2)
        _, first = player.update([], 0.7, lead=108)
        self.assertIsNotNone(first)
        self.assertEqual(sum(track.hit for track in player.tracks), 1)
        # 同键尚未松开时，下一个音符的过期截止不能挡住松键。
        self.assertAlmostEqual(player.next_deadline(0.15), player.release_at)
        keys, second = player.update([], player.release_at, lead=108)
        self.assertEqual(keys, ())
        self.assertIsNone(second)
        # 密集输入仍保留下一枚音符，等抬起间隙结束后再消耗。
        _, second = player.update([], player.next_deadline(0.15), lead=108)
        self.assertIsNotNone(second)
        self.assertEqual(sum(track.hit for track in player.tracks), 2)

    def test_unconfirmed_body_fragment_does_not_cut_hold_short(self):
        player = RhythmPlayer()
        for now in (0.0, 0.1, 0.2):
            player.update([note(1100 - 720 * now, "red", 2200)], now, lead=36)
        _, long = player.update([], 0.7, lead=36)
        self.assertTrue(long.long)
        release_at = player.release_at
        keys, hit = player.update([note(1358, "red", 250)], 0.71, lead=36)
        self.assertEqual(keys, ("e",))
        self.assertIsNone(hit)
        self.assertEqual(player.release_at, release_at)
        self.assertEqual(player.next_deadline(0.05), release_at)

    def test_all_colors_and_adjacent_same_color(self):
        for color, keys in (("blue", ("q",)), ("red", ("e",)), ("purple", ("q", "e"))):
            player = RhythmPlayer()
            hits = []
            for i in range(140):
                now = i / 100
                notes = [note(1100 + offset - 720 * now, color) for offset in (0, 90)]
                notes = [item for item in notes if item.head > 540]
                pressed, hit = player.update(notes, now)
                if hit:
                    hits.append(hit)
                    self.assertEqual(pressed, keys)
            self.assertEqual(len(hits), 2)
            self.assertEqual(player.held, ())

    def test_covered_note_is_predicted_without_rehitting_residue(self):
        player = RhythmPlayer()
        hits = []
        for i in range(130):
            now = i / 100
            x = 1100 - 720 * now
            notes = [note(x)] if x > 850 else []
            if 0.9 < now < 1.1:
                notes.append(note(587))  # 命中后的残影不创建新轨迹。
            _, hit = player.update(notes, now)
            if hit:
                hits.append(hit)
        self.assertEqual(len(hits), 1)

    def test_long_notes_hold_until_tail(self):
        for color in ("blue", "red", "purple"):
            player = RhythmPlayer()
            hits = 0
            for i in range(150):
                now = i / 100
                head = 1100 - 720 * now
                tail = head + 360
                notes = [note(head, color, 360)] if head > 540 else []
                if head <= 540 and tail > 640:
                    notes = [RhythmNote(color, 550, tail, 535, True)]
                keys, hit = player.update(notes, now)
                hits += hit is not None
                if 0.78 < now < 1.18:
                    self.assertEqual(keys, note(0, color).keys)
            self.assertEqual(hits, 1)
            self.assertEqual(player.held, ())

    def test_new_track_at_judge_does_not_press(self):
        player = RhythmPlayer()
        self.assertEqual(player.update([note(587)], 0), ((), None))

    def test_long_release_allows_adjacent_same_color_short(self):
        player = RhythmPlayer()
        events = []
        held_states = []
        for i in range(400):
            now = i * 0.005
            head = 1250 - 600 * now
            notes = [note(head, "red", 405), note(head + 450, "red"), note(head + 540, "red")]
            pressed, hit = player.update(notes, now, lead=player.speed * 0.045)
            if hit:
                events.append(now)
            held_states.append((now, pressed))
        self.assertEqual(len(events), 3)
        for at, offset in zip(events, (0, 450, 540), strict=True):
            self.assertAlmostEqual(at, (1250 + offset - 587) / 600 - 0.045, delta=0.006)
        # 验证长条在短音符击打前成功释放按键（长条松手紧跟着按下）
        releases = [at for at, held in held_states if events[0] < at < events[1] and held == ()]
        self.assertTrue(releases)

    def test_touching_red_real_frames_playback(self):
        detector = RhythmDetector()
        frame = cv2.imread(str(FIXTURES / "touching_red.png"))
        h, w = frame.shape[:2]
        player = RhythmPlayer()
        speed = 720.0
        dt = 0.02
        hits = []
        held_states = []
        for i in range(80):
            now = i * dt
            shift = int(-535.5 + speed * now)
            M = np.float32([[1, 0, -shift], [0, 1, 0]])
            shifted = cv2.warpAffine(frame, M, (w, h))
            notes = detector.detect(shifted)
            held, hit = player.update(notes, now, lead=speed * 0.045)
            if hit:
                hits.append((now, hit))
            held_states.append((now, held))
        self.assertEqual(len(hits), 3)
        self.assertTrue(hits[0][1].long)
        self.assertFalse(hits[1][1].long)
        # 确认真实画面推进时长条在短音符击打前成功松手
        released = any(held == () for t, held in held_states if hits[0][0] < t < hits[1][0])
        self.assertTrue(released)

    def test_capture_and_processing_latency_do_not_shift_hit_late(self):
        for processing_delay in (0.0, 0.02, 0.055):
            player = RhythmPlayer()
            hit_times = []
            for i in range(180):
                captured_at = i * 0.005
                now = captured_at + processing_delay
                notes = [note(1100 - 720 * captured_at)]
                _, hit = player.update(notes, now, lead=720 * 0.045, observed_at=captured_at)
                if hit:
                    hit_times.append(now)
            self.assertEqual(len(hit_times), 1)
            ideal = (1100 - 587) / 720 - 0.045
            self.assertAlmostEqual(hit_times[0], ideal, delta=0.006)

    def test_effect_cannot_turn_tracked_short_note_into_hold(self):
        player = RhythmPlayer()
        hits = []
        for i in range(100):
            now = i / 100
            head = 1100 - 720 * now
            _, hit = player.update([note(head, length=200 if head < 950 else 0)], now)
            if hit:
                hits.append(hit)
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0].long)

    def test_one_bad_classification_in_clear_area_keeps_track_observations(self):
        player = RhythmPlayer()
        hits = []
        for index in range(160):
            now = index / 100
            head = 1390 - 600 * now
            # 本机录像中，背景把 x≈1044 的短音符掩膜拉宽了一帧。
            keys, hit = player.update([note(head, length=47 if 1040 < head < 1050 else 0)], now, lead=30)
            if hit:
                hits.append(hit)
            if 1.36 <= now <= 1.5:
                self.assertEqual(keys, ())
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0].long)

    def test_long_classification_survives_a_missing_body(self):
        player = RhythmPlayer()
        hits = []
        for index in range(210):
            now = index / 100
            head = 1390 - 600 * now
            keys, hit = player.update([note(head, "red", 0 if head < 1050 else 360)], now, lead=30)
            if hit:
                hits.append(hit)
            if 1.4 <= now <= 1.7:
                self.assertEqual(keys, ("e",))
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].long)

    def test_long_release_clamped_before_conflicting_note(self):
        # 1. 红色长条紧跟同色短音符，验证长条 release_at 在短音符击打前至少提前 45ms 释放
        player = RhythmPlayer()
        speed = 720.0
        dt = 0.01
        hits, long_release = [], 0.0
        for i in range(110):
            now = i * dt
            h1, h2 = 1200 - speed * now, 1245 - speed * now
            notes = [note(h, "red", 405 if idx == 0 else 0) for idx, h in enumerate((h1, h2)) if h > 820]
            _held, hit = player.update(notes, now, lead=speed * 0.045)
            if hit:
                hits.append((now, hit))
                if hit.long:
                    long_release = player.release_at
        self.assertEqual(len(hits), 2)
        self.assertLessEqual(long_release, hits[1][0] - 0.045 + 0.001)

        # 2. 红色长条紧跟紫色短音符（共享 key 'e'），验证同样受冲突约束提前释放
        player2 = RhythmPlayer()
        hits2, long_release2 = [], 0.0
        for i in range(110):
            now = i * dt
            h1, h2 = 1200 - speed * now, 1245 - speed * now
            notes = [
                note(h, "red" if idx == 0 else "purple", 405 if idx == 0 else 0)
                for idx, h in enumerate((h1, h2))
                if h > 820
            ]
            _held, hit = player2.update(notes, now, lead=speed * 0.045)
            if hit:
                hits2.append((now, hit))
                if hit.long:
                    long_release2 = player2.release_at
        self.assertEqual(len(hits2), 2)
        self.assertLessEqual(long_release2, hits2[1][0] - 0.045 + 0.001)


class TestRhythmTaskLifecycle(unittest.TestCase):
    def test_retrigger_only_releases_the_new_notes_keys(self):
        task = task_harness()
        task._set_keys(("q", "e"))
        task.executor.interaction.reset_mock()
        with patch("src.tasks.trigger.auto_rhythm_task.time.sleep"):
            task._set_keys(("q", "e"), retrigger=("e",))
        calls = [(call[0], call.args[0]) for call in task.executor.interaction.mock_calls]
        self.assertEqual(calls, [("send_key_up", "e"), ("send_key_down", "e")])
        self.assertEqual(task._held_keys, {"q", "e"})

    def test_slow_capture_serves_deadlines_and_reads_visible_latency_config(self):
        for timestamp in (None, float("nan"), 10.05):
            with self.subTest(timestamp=timestamp):
                task = task_harness()
                task.config["输入延迟 (ms)"] = 75
                task.executor.method.frame_timestamp = timestamp
                clock = [10.0]
                task.active_time = lambda clock=clock: clock[0]

                def capture(clock=clock):
                    if clock[0] > 10.01:
                        raise RuntimeError("capture complete")
                    clock[0] += 0.12
                    return np.zeros((1080, 1920, 3), dtype=np.uint8)

                def detect(_, clock=clock):
                    clock[0] += 0.04
                    return []

                def wait(seconds, clock=clock):
                    clock[0] += seconds
                    return False

                task.next_frame = MagicMock(side_effect=capture)
                task.get_game_hwnd = lambda: 123
                task._rhythm_detector = MagicMock()
                task._rhythm_detector.is_active.return_value = True
                task._rhythm_detector.track_band.return_value = np.zeros((3, 3, 3), dtype=np.uint8)
                task._rhythm_detector.detect.side_effect = detect
                player = MagicMock(speed=720)
                player.update.return_value = (("q",), None)
                player.next_deadline.side_effect = [10.24, float("inf")]
                with (
                    patch(
                        "src.tasks.trigger.auto_rhythm_task.time.perf_counter", side_effect=lambda clock=clock: clock[0]
                    ),
                    patch("src.tasks.trigger.auto_rhythm_task.win32gui.GetForegroundWindow", return_value=123),
                    patch("src.tasks.trigger.auto_rhythm_task.threading.Thread"),
                    patch("src.tasks.trigger.auto_rhythm_task.RhythmPlayer", return_value=player),
                    patch.object(task._cancel, "wait", side_effect=wait),
                    self.assertRaisesRegex(RuntimeError, "capture complete"),
                ):
                    task.run()
                self.assertEqual(player.update.call_count, 2)
                first, timed = player.update.call_args_list
                self.assertEqual(first.args[2], 54)
                self.assertAlmostEqual(first.args[3], 10.05 if timestamp == 10.05 else 10.06)
                self.assertAlmostEqual(timed.args[1], 10.24)
                self.assertFalse(task._held_keys)

    def test_construct_and_registered(self):
        task = task_harness()
        self.assertEqual(task.name, "自动音游")
        self.assertIn(["src.tasks.trigger.auto_rhythm_task", "AutoRhythmTask"], config["trigger_tasks"])

    def test_double_down_before_either_up_and_disable_cleanup(self):
        task = task_harness()
        interaction = task.executor.interaction
        task._set_keys(("q", "e"), retrigger=True)
        task.disable()
        actions = [(call[0], call.args[0]) for call in interaction.mock_calls]
        self.assertEqual(actions[:2], [("send_key_down", "q"), ("send_key_down", "e")])
        self.assertEqual(set(actions[2:]), {("send_key_up", "q"), ("send_key_up", "e")})
        self.assertFalse(task._held_keys)
        task._set_keys(("q",))
        self.assertEqual(len(interaction.mock_calls), 4)

    def test_watchdog_releases_on_pause_focus_loss_stop_and_capture_stall(self):
        for reason in ("pause", "focus", "stop", "stall"):
            with self.subTest(reason=reason):
                task = task_harness()
                task._set_keys(("q", "e"))
                task._last_frame_at = time.perf_counter() - (1 if reason == "stall" else 0)
                task.executor.paused = reason == "pause"
                if reason == "stop":
                    task.executor.exit_event.set()
                with patch(
                    "src.tasks.trigger.auto_rhythm_task.win32gui.GetForegroundWindow",
                    return_value=0 if reason == "focus" else 123,
                ):
                    task._watch_input(threading.Event(), 123)
                self.assertTrue(task._cancel.is_set())
                self.assertFalse(task._held_keys)

    def test_exception_after_press_releases_keys(self):
        task = task_harness()
        frame = cv2.imread(str(FIXTURES / "blue_red.png"))
        task.next_frame = MagicMock(side_effect=[frame, RuntimeError("capture failed")])
        task.get_game_hwnd = lambda: 123
        player = MagicMock()
        player.next_deadline.return_value = float("inf")
        player.update.return_value = (("q", "e"), note(587, "purple"))
        with (
            patch("src.tasks.trigger.auto_rhythm_task.win32gui.GetForegroundWindow", return_value=123),
            patch("src.tasks.trigger.auto_rhythm_task.RhythmPlayer", return_value=player),
            self.assertRaisesRegex(RuntimeError, "capture failed"),
        ):
            task.run()
        self.assertFalse(task._held_keys)
        self.assertEqual(task.executor.interaction.send_key_up.call_count, 2)

    @patch("src.tasks.trigger.auto_rhythm_task.time.sleep")
    def test_retrigger_sleeps_between_up_and_down(self, mock_sleep):
        task = task_harness()
        task._held_keys = {"e"}
        task._set_keys(("e",), retrigger=True)
        mock_sleep.assert_called_once_with(0.015)
        calls = [(call[0], call.args[0]) for call in task.executor.interaction.mock_calls]
        self.assertEqual(calls, [("send_key_up", "e"), ("send_key_down", "e")])

    def test_cancel_during_retrigger_does_not_press_again(self):
        task = task_harness()
        task._held_keys = {"e"}
        with patch("src.tasks.trigger.auto_rhythm_task.time.sleep", side_effect=lambda _: task._cancel.set()):
            task._set_keys(("e",), retrigger=True)
        task.executor.interaction.send_key_down.assert_not_called()
        self.assertFalse(task._held_keys)


if __name__ == "__main__":
    unittest.main()
