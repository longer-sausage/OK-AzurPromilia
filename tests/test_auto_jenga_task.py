"""真实截图识别、双向摇摆预测与释放生命周期回归。"""

import json
import math
import threading
import unittest
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from ok import CaptureException

from src.config import config
from src.image.jenga_detector import ASSETS, JengaDetector, JengaObservation, JengaPiece, _detail
from src.tasks.trigger.auto_jenga_task import AutoJengaTask, JengaPlayer, _fit_figure_eight_phase

FIXTURES = Path(__file__).parent / "fixtures" / "jenga"


def observation(x=960, target=960, y=200, top=770, width=100):
    return JengaObservation(
        JengaPiece(x, y, y + 170, width), JengaPiece(target, top, top + 170, 100), JengaPiece(x, y - 80, y - 10, 32)
    )


def fixed_fall_time(gap, scale=228):
    """Integrate native velocity updates, independent of the analytic solver."""
    position = velocity = elapsed = 0.0
    step = 1 / 30
    while position < gap:
        velocity = min(velocity + 49.05 * scale * step, 10 * scale)
        travel = velocity * step
        if position + travel >= gap:
            return elapsed + (gap - position) / velocity
        position += travel
        elapsed += step
    return elapsed


def task_harness():
    executor, app = MagicMock(), MagicMock()
    app.tr.side_effect = lambda value: value
    executor.paused = False
    executor.exit_event = threading.Event()
    task = AutoJengaTask(executor, app)
    task._enabled = True
    task.config = dict(task.default_config)
    return task


class TestJengaVision(unittest.TestCase):
    def test_unconfirmed_release_reacquires_the_actual_receiver(self):
        frame = cv2.imread(str(FIXTURES / "start.png"))
        detector = JengaDetector()
        first = detector.detect(frame)
        self.assertEqual(first.carried.kind, "owl")
        self.assertEqual(first.tower.kind, "monkey")
        # A Miss keeps the monkey receiver; an unread judgment may also have
        # kept it. Neither outcome proves that the owl became the new tower.
        detector.learn(frame, first.carried, placement_confirmed=False)
        recovered = detector.detect(frame)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.tower.kind, "monkey")
        self.assertAlmostEqual(recovered.tower.x, first.tower.x, delta=6)
        self.assertEqual(len(detector.learned), 1)

    def test_new_session_discards_learned_templates_and_height_bound(self):
        detector = JengaDetector()
        high = cv2.imread(str(FIXTURES / "layer15_0.png"))
        first = detector.detect(high)
        detector.learn(high, first.carried)
        learned_ids = {id(template) for group in detector.learned for template in group}
        detector.reset_tracking(clear_learned=True)
        self.assertEqual(detector.learned, [])
        self.assertTrue(learned_ids.isdisjoint(detector._template_kinds))
        restarted = detector.detect(cv2.imread(str(FIXTURES / "start.png")))
        self.assertIsNotNone(restarted)
        self.assertGreater(restarted.tower.top, 780)

    def test_released_identity_rejects_confident_railing_after_sparks(self):
        for size in ((960, 540), (1280, 720), (1920, 1080)):
            detector = JengaDetector()
            detector.pixels_per_unit = 228
            before = cv2.resize(cv2.imread(str(FIXTURES / "verified_monkey_before_release.png")), size)
            after = cv2.resize(cv2.imread(str(FIXTURES / "verified_monkey_railing_rabbit.png")), size)
            released = detector.detect(before)
            self.assertIsNotNone(released, size)
            self.assertEqual(released.carried.kind, "monkey", size)
            detector.learn(before, released.carried)
            for _ in range(3):
                found = detector.detect(after)
                self.assertIsNotNone(found, size)
                self.assertEqual(found.tower.kind, "monkey", size)
                self.assertAlmostEqual(found.tower.x, 974, delta=14)
                self.assertAlmostEqual(found.tower.top, 790, delta=20)

    def test_new_layer_railing_cannot_outrank_or_replace_the_real_top(self):
        for size in ((960, 540), (1280, 720), (1920, 1080)):
            detector = JengaDetector()
            # A release clears the upper texture but keeps the preceding
            # layer's height bound. The next actual frame matched a railing
            # at y=636 ahead of the real sculpture at y=796, then cached it.
            detector._tower_last = JengaPiece(974, 796, 968, 100, kind="monkey")
            for name in ("new_layer_railing", "cached_railing_miss_release"):
                frame = cv2.resize(cv2.imread(str(FIXTURES / f"{name}.png")), size)
                for _ in range(3):
                    found = detector.detect(frame)
                    self.assertIsNotNone(found, (size, name))
                    self.assertEqual(found.tower.kind, "monkey", (size, name))
                    self.assertAlmostEqual(found.tower.x, 966, delta=8)
                    self.assertAlmostEqual(found.tower.top, 796, delta=8)

    def test_confident_torso_tracking_must_reacquire_the_upper_anchor(self):
        frame = JengaDetector.normalize(cv2.imread(str(FIXTURES / "cached_torso_lower_anchor.png")))
        detector = JengaDetector()
        detail = _detail(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (960, 540)))
        torso = JengaPiece(770, 364, 540, 116, kind="monkey")
        face = replace(torso, top=372, bottom=452)
        detector._held_template = detector._piece_template(detail, face)
        detector._held_full_height = 176
        detector._held_full_width = 116
        detector._held_face_offset = 8
        detector._held_kind = "monkey"
        bird = detector._find_birds(frame)[0]
        found, bounds = detector._carried_below(frame, detail, bird, detector.templates)
        self.assertIsNotNone(found)
        found = detector._identify_carried(detail, found, bounds)
        self.assertEqual(found.kind, "monkey")
        self.assertLess(found.top, 315)
        self.assertLess(found.bottom, 490)
        self.assertAlmostEqual(found.x, 768, delta=10)

    def test_known_full_owl_uses_round_scale_but_unknown_contour_does_not(self):
        frame = cv2.imread(str(FIXTURES / "held_warm_owl.png"))
        detector = JengaDetector()
        detector.pixels_per_unit = 228
        found = detector.detect(frame)
        self.assertIsNotNone(found)
        self.assertEqual(found.carried.kind, "owl")
        self.assertAlmostEqual(found.carried.bottom - found.carried.top, 0.758492 * 228)
        self.assertAlmostEqual(found.carried.top, 152, delta=6)
        detector.learn(frame, found.carried)
        self.assertEqual(detector.pixels_per_unit, 228)
        detector.reset_tracking()
        self.assertIsNone(detector.pixels_per_unit)
        detector.pixels_per_unit = 228
        partial = cv2.imread(str(FIXTURES / "backlit_partial_legs.png"))
        detector.templates = [t for t in detector.templates if detector._template_kinds[id(t)] != "owl"]
        found = detector.detect(partial)
        self.assertIsNotNone(found)
        self.assertEqual(found.carried.kind, "")
        self.assertAlmostEqual(found.carried.bottom, 419, delta=8)

    def test_upper_texture_keeps_full_owl_anchor_when_feet_lose_contrast(self):
        frame = JengaDetector.normalize(cv2.imread(str(FIXTURES / "held_warm_owl.png")))
        detector = JengaDetector()
        first = detector.detect(frame)
        for _ in range(6):
            detector.detect(frame)
        self.assertIsNone(detector._held_full_height)
        for shift in (20, 40, 60, 80, 100):
            moved = cv2.warpAffine(frame, np.float32([[1, 0, shift], [0, 1, 0]]), (1920, 1080))
            found = detector.detect(moved)
            self.assertIsNotNone(found)
            self.assertAlmostEqual(found.carried.x, first.carried.x + shift, delta=5)
        self.assertIsNotNone(detector._held_full_height)
        bottom = round(first.carried.bottom)
        center = round(first.carried.x + 100)
        moved[bottom - 24 : bottom + 4, center - 65 : center + 65] = cv2.GaussianBlur(
            moved[bottom - 24 : bottom + 4, center - 65 : center + 65], (0, 0), 12
        )
        found = detector.detect(moved)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found.carried.bottom, first.carried.bottom, delta=5)
        self.assertGreater(found.carried.bottom - found.carried.top, 160)
        detector.learn(moved, found.carried)
        self.assertIsNone(detector._held_full_height)
        self.assertFalse(detector._held_shape_samples)

    def test_five_distinct_backlit_views_confirm_weak_identity(self):
        detector = JengaDetector()
        for index in range(5):
            frame = detector.normalize(cv2.imread(str(FIXTURES / f"backlit_owl_identity_{index}.png")))
            detail = _detail(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (960, 540)))
            bird = detector._find_birds(frame)[0]
            piece, bounds = detector._carried_below(frame, detail, bird, detector.templates)
            piece = replace(piece, kind="")
            for _ in range(5):
                detector._identity_retry = 4
                found = detector._identify_carried(detail, piece, bounds)
                self.assertEqual(detector._identity_votes, index + 1)
                self.assertEqual(detector._identity_strong_votes, 0)
                self.assertEqual(found.kind, "owl" if index == 4 else "")
        detector.learn(frame, found)
        self.assertEqual(detector._identity_votes, 0)
        self.assertIsNone(detector._identity_extent)

    def test_cached_owl_without_feet_recovers_small_missing_extension(self):
        original = cv2.imread(str(FIXTURES / "cached_owl_feet.png"))
        for size in ((960, 540), (1280, 720), (1920, 1080)):
            detector = JengaDetector()
            frame = cv2.resize(original, size)
            normalized = detector.normalize(frame)
            detail = _detail(cv2.resize(cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY), (960, 540)))
            partial = JengaPiece(1160, 152, 296, 108)
            detector._held_template = detector._piece_template(detail, partial)
            bird = min(detector._find_birds(normalized), key=lambda piece: abs(piece.x - 1150))
            found, _ = detector._carried_below(normalized, detail, bird, detector.templates)
            self.assertIsNotNone(found, size)
            self.assertGreater(found.bottom, 305, size)
            self.assertGreater(found.bottom - found.top, 160, size)

    def test_warm_owl_reference_recovers_full_body_at_multiple_resolutions(self):
        original = cv2.imread(str(FIXTURES / "held_warm_owl.png"))
        for size in ((960, 540), (1280, 720), (1920, 1080)):
            found = JengaDetector().detect(cv2.resize(original, size))
            self.assertIsNotNone(found, size)
            self.assertEqual(found.carried.kind, "owl")
            self.assertAlmostEqual(found.carried.x, 828, delta=6)
            self.assertAlmostEqual(found.carried.bottom, 320, delta=6)
            self.assertGreater(found.carried.bottom - found.carried.top, 160)

    def test_distinct_warm_views_confirm_identity_but_frozen_views_do_not(self):
        detector = JengaDetector()
        for index, name in enumerate(("held_warm_owl_right", "held_warm_owl_other_phase", "held_warm_owl_crossing")):
            frame = detector.normalize(cv2.imread(str(FIXTURES / f"{name}.png")))
            detail = _detail(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (960, 540)))
            bird = detector._find_birds(frame)[0]
            piece, bounds = detector._carried_below(frame, detail, bird, detector.templates)
            piece = replace(piece, kind="")
            # Exercise classification with the accurate unknown tracking crop.
            # The recorded views have reference confidence below the single-
            # frame threshold, and no agreeing reference of another species.
            for _ in range(3):
                detector._identity_retry = 4
                found = detector._identify_carried(detail, piece, bounds)
                self.assertEqual(detector._identity_votes, index + 1)
                self.assertEqual(found.kind, "owl" if index == 2 else "")
                self.assertEqual((found.x, found.top, found.bottom), (piece.x, piece.top, piece.bottom))
        detector.learn(frame, found)
        self.assertEqual(detector._identity_votes, 0)
        self.assertEqual(detector._identity_candidate, "")

    def test_sunlit_owl_and_yellow_wing_reacquire_without_background_relaxation(self):
        original = cv2.imread(str(FIXTURES / "sunlit_second_owl.png"))
        # The real frame contains a neck and a similarly shaped yellow wing.
        # The wing has no complete sculpture below it and must not replace the
        # neck. This stage previously waited 23 seconds for the light to change.
        self.assertGreater(len(JengaDetector._find_birds(JengaDetector.normalize(original))), 1)
        for size in ((960, 540), (1280, 720), (1920, 1080)):
            detector = JengaDetector()
            frame = cv2.resize(original, size)
            for _ in range(3):
                found = detector.detect(frame)
                self.assertIsNotNone(found, size)
                self.assertAlmostEqual(found.bird.x, 828, delta=4)
                self.assertAlmostEqual(found.carried.x, 833, delta=8)
                self.assertEqual(found.tower.kind, "owl")
                self.assertAlmostEqual(found.tower.x, 954, delta=6)
                self.assertAlmostEqual(found.tower.top, 776, delta=6)

    def test_sunlit_high_monkey_reacquires_and_tracks_at_multiple_resolutions(self):
        original = cv2.imread(str(FIXTURES / "high_sunlit_monkey.png"))
        for size in ((960, 540), (1280, 720), (1920, 1080)):
            detector = JengaDetector()
            frame = cv2.resize(original, size)
            for _ in range(3):
                found = detector.detect(frame)
                self.assertIsNotNone(found, size)
                self.assertEqual(found.tower.kind, "monkey")
                self.assertAlmostEqual(found.tower.x, 716, delta=8)
                self.assertAlmostEqual(found.tower.top, 718, delta=8)
                self.assertGreater(found.tower.bottom - found.tower.top, 170)

    def test_sunlit_carried_monkey_has_identity_before_becoming_tower(self):
        frame = cv2.imread(str(FIXTURES / "held_sunlit_monkey.png"))
        detector = JengaDetector()
        detail = _detail(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (960, 540)))
        # This preceding owl was already tracked in the live sequence. Test
        # the new hanging appearance against that actual receiving face.
        detector._tower_last = JengaPiece(716, 712, 890, 116, kind="owl")
        detector._tower_template = detector._piece_template(detail, detector._tower_last)
        detector._tower_kind = "owl"
        found = detector.detect(frame)
        self.assertIsNotNone(found)
        self.assertEqual(found.carried.kind, "monkey")
        self.assertAlmostEqual(found.carried.bottom, 467, delta=5)
        detector.learn(frame, found.carried)
        self.assertEqual(detector._tower_kind, "monkey")
        self.assertTrue(all(detector._template_kinds[id(template)] == "monkey" for template in detector.learned[-1]))

    def test_header_and_dim_neck_still_locate_bird_horizontally(self):
        frame = cv2.imread(str(FIXTURES / "header_dim_neck_rabbit.png"))
        birds = JengaDetector._find_birds(frame)
        self.assertEqual(len(birds), 1)
        self.assertAlmostEqual(birds[0].x, 1172, delta=6)
        self.assertLess(birds[0].bottom, 90)

    def test_cached_rabbit_ears_and_face_must_recover_body(self):
        frame = cv2.imread(str(FIXTURES / "header_dim_neck_rabbit.png"))
        detector = JengaDetector()
        detail = _detail(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (960, 540)))
        partial = JengaPiece(1164, 115, 225, 78, kind="rabbit")
        detector._held_template = detector._piece_template(detail, partial)
        detector._held_kind = "rabbit"
        found = detector.detect(frame)
        self.assertIsNotNone(found)
        self.assertEqual(found.carried.kind, "rabbit")
        self.assertGreater(found.carried.bottom, 285)
        self.assertGreater(found.carried.bottom - found.carried.top, 170)

    def test_sunlit_or_occluded_neck_keeps_its_lower_anchor(self):
        frame = cv2.imread(str(FIXTURES / "short_sunlit_neck.png"))
        birds = JengaDetector._find_birds(frame)
        self.assertEqual(len(birds), 1)
        self.assertAlmostEqual(birds[0].x, 854, delta=6)
        self.assertAlmostEqual(birds[0].bottom, 129, delta=6)

    def test_confident_cached_torso_must_recover_complete_hanging_owl(self):
        frame = cv2.imread(str(FIXTURES / "partial_cached_owl.png"))
        detector = JengaDetector()
        detail = _detail(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (960, 540)))
        partial = JengaPiece(922, 258, 362, 108)
        detector._held_template = detector._piece_template(detail, partial)
        detector._tower_last = JengaPiece(688, 730, 916, 100)
        detector._tower_template = detector._piece_template(detail, detector._tower_last)
        found = detector.detect(frame)
        self.assertIsNotNone(found)
        self.assertLess(found.carried.top, 220)
        self.assertGreater(found.carried.bottom - found.carried.top, 145)
        self.assertAlmostEqual(found.carried.bottom, 366, delta=10)

    def test_backlit_dark_legs_extend_partial_face_contour(self):
        frame = cv2.imread(str(FIXTURES / "backlit_partial_legs.png"))
        detector = JengaDetector()
        bird = detector._find_birds(frame)[0]
        piece = detector._unknown_piece(frame, bird)
        self.assertIsNotNone(piece)
        self.assertAlmostEqual(piece.bottom, 419, delta=8)
        self.assertGreater(piece.bottom - piece.top, 160)
        self.assertAlmostEqual(piece.x, 704, delta=10)

    def test_header_can_split_bird_neck_without_losing_its_lower_anchor(self):
        frame = cv2.imread(str(FIXTURES / "hud_occluded_bird.png"))
        detector = JengaDetector()
        birds = detector._find_birds(frame)
        self.assertEqual(len(birds), 1)
        self.assertAlmostEqual(birds[0].x, 1217, delta=6)
        self.assertAlmostEqual(birds[0].bottom, 123, delta=6)
        found = detector.detect(frame)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found.tower.x, 610, delta=12)

    def test_top_remains_visible_beyond_original_horizontal_search_band(self):
        frame = cv2.imread(str(FIXTURES / "start.png"))
        for shift in (-600, 600):
            detector = JengaDetector()
            first = detector.detect(frame)
            moved = frame.copy()
            moved[770:1020, 890:1050] = 0
            moved[770:1020, 890 + shift : 1050 + shift] = frame[770:1020, 890:1050]
            found = detector.detect(moved)
            self.assertIsNotNone(found)
            self.assertAlmostEqual(found.tower.x, first.tower.x + shift, delta=6)

    def test_unknown_cached_crop_recovers_and_retains_identity_until_release(self):
        frame = cv2.imread(str(FIXTURES / "identity_backlit_monkey.png"))
        detector = JengaDetector()
        first = detector.detect(frame)
        detail = _detail(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (960, 540)))
        detector._held_template = detector._piece_template(detail, first.carried)
        detector._held_kind = ""
        found = [detector.detect(frame) for _ in range(6)]
        self.assertEqual(found[-1].carried.kind, "monkey")
        # A lighting change can make localization fall back to an unlabelled
        # contour; it cannot turn this unreleased sculpture into another kind.
        detector.templates = []
        detector._held_template = None
        recovered = detector.detect(frame)
        self.assertEqual(recovered.carried.kind, "monkey")
        detector.learn(frame, recovered.carried)
        self.assertEqual(detector._held_kind, "")

    def test_blurred_railing_cannot_become_a_self_reinforcing_tower_track(self):
        detector = JengaDetector()
        found = []
        for index in (7, 8, 12):
            frame = cv2.imread(str(FIXTURES / f"background_transition_{index}.png"))
            obs = detector.detect(frame)
            if obs:
                # Manually checked sculpture positions in the real release
                # sequence are 970..1002; the background railing is at 1116.
                self.assertLess(obs.tower.x, 1040)
                self.assertEqual(obs.tower.kind, "monkey")
                found.append(obs)
        self.assertTrue(found)

    def test_final_rabbit_and_dim_top_remain_distinct(self):
        detector = JengaDetector()
        frame = cv2.imread(str(FIXTURES / "final_rabbit_night.png"))
        for _ in range(3):
            found = detector.detect(frame)
            self.assertIsNotNone(found)
            self.assertAlmostEqual(found.carried.x, 714, delta=6)
            self.assertAlmostEqual(found.carried.bottom, 321, delta=5)
            self.assertAlmostEqual(found.tower.x, 972, delta=6)
            self.assertAlmostEqual(found.tower.top, 660, delta=6)

    def test_final_rabbit_native_height_preserves_the_actual_feet_in_day_and_night(self):
        # The last piece's ears make it taller and narrower than the ordinary
        # sculptures. A cached ear/face crop previously drifted down the body.
        for name, foot in (("final_rabbit_day.png", 390), ("final_rabbit_night.png", 321)):
            for size in ((1920, 1080), (1280, 720), (2560, 1440)):
                with self.subTest(name=name, size=size):
                    frame = cv2.resize(cv2.imread(str(FIXTURES / name)), size)
                    detector = JengaDetector()
                    detector.pixels_per_unit = 229
                    for _ in range(3):
                        found = detector.detect(frame)
                        self.assertIsNotNone(found)
                        self.assertEqual(found.carried.kind, "rabbit")
                        self.assertAlmostEqual(found.carried.bottom, foot, delta=5)
                        self.assertAlmostEqual(found.carried.bottom - found.carried.top, 0.894245 * 229, delta=1)
                        self.assertEqual(found.tower.kind, "owl")

    def test_similar_lower_piece_cannot_steal_temporarily_dimmed_top(self):
        detector = JengaDetector()
        first = detector.detect(cv2.imread(str(FIXTURES / "layer15_0.png")))
        self.assertLess(first.tower.top, 710)
        self.assertIsNone(detector.detect(cv2.imread(str(FIXTURES / "layer15_1.png"))))
        recovered = detector.detect(cv2.imread(str(FIXTURES / "layer15_2.png")))
        self.assertLess(recovered.tower.top, 710)
        self.assertAlmostEqual(recovered.tower.x, 1226, delta=5)

    def test_learning_reacquires_appearance_without_accepting_previous_layer(self):
        detector = JengaDetector()
        frame = cv2.imread(str(FIXTURES / "layer15_0.png"))
        first = detector.detect(frame)
        detector.learn(frame, first.carried)
        self.assertEqual(detector._tower_last, first.tower)
        self.assertIsNone(detector._tower_template)
        found = detector.detect(cv2.imread(str(FIXTURES / "layer15_1.png")))
        if found is not None:
            self.assertLess(found.tower.top, 760)
        detector.reset_tracking()
        self.assertIsNone(detector._tower_last)

    def test_high_sunlit_owl_tracks_upper_piece_on_both_sides(self):
        for name, x, bird in (("high_17_left", 745, None), ("high_17_right", 1294, JengaPiece(763, 45, 114, 35))):
            detector = JengaDetector()
            frame = cv2.imread(str(FIXTURES / f"{name}.png"))
            if bird is not None:
                # The HUD covers this bird; isolate the tower regression using
                # its manually checked location, without relaxing bird detection.
                detector._find_birds = MagicMock(return_value=[bird])
            for _ in range(3):
                found = detector.detect(frame)
                self.assertIsNotNone(found, name)
                self.assertAlmostEqual(found.tower.x, x, delta=8)
                self.assertLess(found.tower.top, 810)

    def test_night_bird_keeps_complete_neck_component(self):
        frame = JengaDetector.normalize(cv2.imread(str(FIXTURES / "night_bird.png")))
        birds = JengaDetector._find_birds(frame)
        self.assertEqual(len(birds), 1)
        self.assertAlmostEqual(birds[0].x, 994, delta=4)
        self.assertAlmostEqual(birds[0].bottom, 213, delta=4)

    def test_weak_high_background_match_cannot_replace_clear_base(self):
        detector = JengaDetector()
        frame = cv2.imread(str(FIXTURES / "false_top.png"))
        for _ in range(3):
            found = detector.detect(frame)
            self.assertIsNotNone(found)
            self.assertAlmostEqual(found.tower.x, 970, delta=6)
            self.assertAlmostEqual(found.tower.top, 796, delta=6)

    def test_rendered_judgments_are_distinct_from_counter_and_sparkles(self):
        detector = JengaDetector()
        for name in ("perfect", "good", "miss"):
            frame = cv2.imread(str(FIXTURES / f"result_{name}.png"))
            self.assertEqual(detector.placement_result(frame), name)
        for name in ("false_top", "live_night", "complete"):
            self.assertIsNone(detector.placement_result(cv2.imread(str(FIXTURES / f"{name}.png"))))
        for index in (5, 6):
            frame = cv2.imread(str(FIXTURES / f"perfect_sunlit_{index}.png"))
            self.assertEqual(detector.placement_result(frame), "perfect")
            bright = cv2.imread(str(FIXTURES / f"perfect_bright_{index}.png"))
            self.assertEqual(detector.placement_result(bright), "perfect")

    def test_yellow_fragment_at_search_border_does_not_make_empty_roi(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[100:150, 520:550] = (0, 200, 220)
        self.assertEqual(JengaDetector._find_birds(frame), [])

    @classmethod
    def setUpClass(cls):
        cls.detector = JengaDetector()

    def test_real_frames_and_resolutions(self):
        for name, held_x, top_x in (("start", 1226, 972), ("low", 1220, 1008), ("high", 722, 848)):
            frame = cv2.imread(str(FIXTURES / f"{name}.png"))
            for scale in (2 / 3, 1, 4 / 3):
                with self.subTest(name=name, scale=scale):
                    # Independent scenes have no intervening release event.
                    self.detector.reset_tracking()
                    image = cv2.resize(frame, None, fx=scale, fy=scale)
                    self.assertTrue(self.detector.is_active(image))
                    found = self.detector.detect(image)
                    self.assertIsNotNone(found)
                    self.assertAlmostEqual(found.carried.x, held_x, delta=6)
                    self.assertAlmostEqual(found.tower.x, top_x, delta=6)
                    self.assertGreater(found.tower.top - found.carried.bottom, 300)

    def test_missing_hud_or_bird_and_world(self):
        frame = cv2.imread(str(FIXTURES / "low.png"))
        for roi in ((slice(48, 92), slice(826, 920)), (slice(920, 1018), slice(1544, 1644))):
            image = frame.copy()
            image[roi] = 0
            self.assertFalse(self.detector.is_active(image))
        frame[190:280, 1140:1300] = 0
        self.assertIsNone(self.detector.detect(frame))
        world = cv2.imread(str(FIXTURES.parent / "rhythm" / "world.png"))
        self.assertFalse(self.detector.is_active(world))
        complete = cv2.imread(str(FIXTURES / "complete.png"))
        self.assertFalse(self.detector.is_active(complete))
        for image in (None, np.zeros((20, 20, 3), np.uint8), np.zeros((800, 800, 3), np.uint8)):
            self.assertFalse(self.detector.is_active(image))

    def test_live_lighting_animated_star_and_sway(self):
        for name, held_x, tower_x in (
            ("live_night", 1110, 972),
            ("live_five", 1202, 950),
            ("live_nine", 1239, 900),
            ("live_backlight", 890, 973),
        ):
            with self.subTest(name=name):
                detector = JengaDetector()
                frame = cv2.imread(str(FIXTURES / f"{name}.png"))
                self.assertTrue(detector.is_active(frame))
                found = detector.detect(frame)
                self.assertIsNotNone(found)
                self.assertAlmostEqual(found.carried.x, held_x, delta=8)
                self.assertAlmostEqual(found.tower.x, tower_x, delta=8)

    def test_unseen_carried_templates_use_bird_and_contour(self):
        for name, exclude, expected_x in (("start", "owl", 1226), ("low", "stone_air", 1220), ("high", "rabbit", 722)):
            detector = JengaDetector()
            detector.templates = []
            for path in ASSETS.glob("*.png"):
                if path.stem.startswith(("bird", "controls", "score", exclude)):
                    continue
                detector.templates.extend(detector._variants(cv2.imread(str(path), 0)))
            found = detector.detect(cv2.imread(str(FIXTURES / f"{name}.png")))
            self.assertIsNotNone(found)
            self.assertAlmostEqual(found.carried.x, expected_x, delta=7)

    def test_learning_is_bounded(self):
        detector = JengaDetector()
        detector.pixels_per_unit = 228
        frame = cv2.imread(str(FIXTURES / "verified_monkey_before_release.png"))
        found = detector.detect(frame)
        for _ in range(6):
            detector.learn(frame, found.carried)
        self.assertEqual(len(detector.learned), 3)
        # A release expects the carried body as the next receiving face.
        after = cv2.imread(str(FIXTURES / "verified_monkey_railing_rabbit.png"))
        self.assertIsNotNone(detector.detect(after))

    def test_fast_tower_motion_stays_in_cached_search(self):
        frame = cv2.imread(str(FIXTURES / "start.png"))
        detector = JengaDetector()
        first = detector.detect(frame)
        self.assertIsNotNone(first)
        moved = frame.copy()
        moved[770:1020, 890:1050] = 0
        moved[770:1020, 1010:1170] = frame[770:1020, 890:1050]
        # Force use of the cached appearance: an expensive full search would
        # make the next frame too old at the final level's lateral speed.
        detector.templates = []
        found = detector.detect(moved)
        self.assertIsNotNone(found)
        self.assertAlmostEqual(found.tower.x, first.tower.x + 120, delta=4)
        self.assertAlmostEqual(found.tower.bottom - found.tower.top, first.tower.bottom - first.tower.top, delta=2)


class TestJengaPrediction(unittest.TestCase):
    def test_high_sway_short_handoff_does_not_strand_the_next_layer(self):
        data = json.loads((FIXTURES / "high_sway_short_handoff.json").read_text())
        player = JengaPlayer(max_delay=0.4)
        player.horizontal_geometry = tuple(data["horizontal_geometry"])
        player.handoff_direction = data["handoff_direction"]
        player.mark_released(data["released_at"], JengaPiece(**data["released_piece"]))
        player.confirm_placement()
        # This real short-arc Good was followed by a failed 120-second round.
        # Its current contact alone cannot justify skipping the handoff check.
        for row in data["rows"]:
            parts = row["obs"]
            obs = JengaObservation(*(JengaPiece(**parts[k]) for k in ("held", "tower", "bird"))) if parts else None
            self.assertFalse(player.update(obs, row["t"] - row["age"], row["t"]))

    def test_horizontal_phase_keeps_native_branch_across_turns_and_missing_frames(self):
        times = np.concatenate((np.arange(0.0, 1.3, 0.08), np.arange(3.6, 5.1, 0.08)))
        for direction in (-1, 1):
            for prior in (None, (973.0, 285.0)):
                with self.subTest(direction=direction, prior=prior):
                    phase = 1.6 * times + (math.pi if direction < 0 else 0.0)
                    points = np.column_stack((times, 973 + 285 * np.sin(phase), 400 + 35 * np.cos(times)))
                    phases, geometry, _ = _fit_figure_eight_phase(points, prior, use_vertical=False)
                    self.assertAlmostEqual(geometry[0], 973.0, delta=0.05)
                    self.assertAlmostEqual(geometry[1], 285.0, delta=0.05)
                    # Independent native motion must retain the direction and
                    # clock through the unobserved turning point, even when
                    # vertical texture movement follows an unrelated curve.
                    for delay in (0.0, 0.25, 0.5):
                        native = 973 + direction * 285 * math.sin(1.6 * (times[-1] + delay))
                        predicted = geometry[0] + geometry[1] * math.sin(phases[-1] + 1.6 * delay)
                        self.assertAlmostEqual(predicted, native, delta=0.1)

    def test_fresh_fit_tracks_the_same_handoff_window_without_rolling_the_wait(self):
        data = json.loads((FIXTURES / "handoff_contact_window.json").read_text())
        player = JengaPlayer(max_delay=0.25)
        player.horizontal_geometry = tuple(data["horizontal_geometry"])
        player.handoff_direction = data["handoff_direction"]
        player.mark_released(data["released_at"], JengaPiece(**data["released_piece"]))
        player.confirm_placement()
        first_target = None
        for row in data["rows"]:
            parts = row["obs"]
            obs = JengaObservation(*(JengaPiece(**parts[k]) for k in ("held", "tower", "bird"))) if parts else None
            ready = player.update(obs, row["t"] - row["age"], row["t"])
            if player.handoff_target_at is not None and first_target is None:
                first_target = player.handoff_target_at
            self.assertFalse(ready)
            self.assertIsNone(player.prediction)
        # The recorded immediate release led to a 45-second next-piece wait.
        # Its later contact remains supported after fresh fits, within the
        # original 250 ms neighborhood and under the full contact budget.
        self.assertIsNotNone(first_target)
        self.assertIsNotNone(player.handoff_target_at)
        self.assertLessEqual(abs(player.handoff_target_at - first_target), 0.125)
        self.assertAlmostEqual(player.handoff_window[0], first_target - 0.125)
        self.assertAlmostEqual(player.handoff_window[1], first_target + 0.125)

    def test_waiting_handoff_rechecks_its_contact_before_discarding_a_safe_stroke(self):
        data = json.loads((FIXTURES / "handoff_plan_loses_contact.json").read_text())
        player = JengaPlayer(max_delay=0.25)
        player.horizontal_geometry = tuple(data["horizontal_geometry"])
        player.handoff_direction = data["handoff_direction"]
        player.mark_released(data["released_at"], JengaPiece(**data["released_piece"]))
        player.confirm_placement()
        target = None
        ready_at = None
        for row in data["rows"]:
            parts = row["obs"]
            obs = JengaObservation(*(JengaPiece(**parts[k]) for k in ("held", "tower", "bird"))) if parts else None
            ready = player.update(obs, row["t"] - row["age"], row["t"])
            if player.handoff_target_at is not None:
                target = player.handoff_target_at
            if ready:
                ready_at = row["t"] + player.release_delay
                self.assertLessEqual(player.prediction["landing_error"], player.prediction["landing_margin"])
                break
        self.assertIsNotNone(target)
        self.assertIsNotNone(ready_at)
        self.assertLess(ready_at, target - 1.0)
        self.assertIsNone(player.handoff_target_at)
        self.assertTrue(player.handoff_waited)

    def test_real_handoff_forecast_can_schedule_a_fresh_guarded_opportunity(self):
        for name in ("handoff_routing_extra_reserve", "handoff_uncertain_next_face"):
            with self.subTest(name=name):
                data = json.loads((FIXTURES / f"{name}.json").read_text())
                player = JengaPlayer(max_delay=0.25)
                player.horizontal_geometry = tuple(data["horizontal_geometry"])
                player.handoff_direction = data["handoff_direction"]
                player.mark_released(data["released_at"], JengaPiece(**data["released_piece"]))
                player.confirm_placement()
                planned = None
                for row in data["rows"]:
                    parts = row["obs"]
                    obs = (
                        JengaObservation(*(JengaPiece(**parts[k]) for k in ("held", "tower", "bird")))
                        if parts
                        else None
                    )
                    player.update(obs, row["t"] - row["age"], row["t"])
                    if player.handoff_target_at is not None:
                        planned = player.handoff_target_at - row["t"]
                        self.assertIsNone(player.prediction)
                        break
                # Each actual next layer waited the remaining minute after
                # the original release. A guarded opposite-stroke handoff is
                # useful even when the next face cannot yet be classified.
                self.assertIsNotNone(planned)
                self.assertGreater(planned, 1.5)
                self.assertLess(planned, 2.5)

    def test_handoff_routes_one_wait_to_a_native_verified_good_in_both_directions(self):
        for mirror in (1, -1):
            player = JengaPlayer(max_delay=0.25)
            player.handoff_direction = -mirror
            plan = None
            release = None
            for t in np.arange(-2.4, 8, 0.08):
                phase = 1.6 * t + 2.035
                x = 973 + 285 * math.sin(phase)
                bottom = 378 - 91.2 * math.sin(2 * phase)
                target = 948.6 + 168 * math.sin(math.pi / 2 * t - 0.175)
                obs = JengaObservation(
                    JengaPiece(960 + mirror * (x + 5 - 960), bottom - 170, bottom, 114),
                    JengaPiece(960 + mirror * (target - 960), 796, 968, 100),
                    JengaPiece(960 + mirror * (x - 960), bottom - 230, bottom - 170, 32),
                )
                hit = player.update(obs, t, t + 0.05)
                if plan is None and player.handoff_target_at is not None:
                    plan = (t + 0.05, player.handoff_target_at)
                if hit:
                    release = t + 0.05 + 0.045 + player.release_delay
                    break
            self.assertIsNotNone(plan)
            self.assertLessEqual(plan[1] - plan[0], 2 * math.pi / 1.6)
            self.assertIsNotNone(release)
            self.assertGreater(release, 1.0)
            self.assertLess(release, 3.0)
            self.assertLessEqual(player.prediction["landing_error"], player.prediction["landing_margin"])
            for jitter in (-1 / 60, 0.0, 1 / 60):
                when = release + jitter
                phi = 1.6 * when + 2.035
                fall = fixed_fall_time(796 - (378 - 91.2 * math.sin(2 * phi)))
                landed = 978 + 285 * math.sin(phi) + 456 * math.cos(phi) * fall
                for contact_jitter in (-1 / 60, 0.0, 1 / 60):
                    target = 948.6 + 168 * math.sin(math.pi / 2 * (when + fall + contact_jitter) - 0.175)
                    self.assertGreaterEqual(1 - abs(landed - target) / (0.4 * 228), 0.5)

    def test_handoff_wait_expires_and_clears_after_release_or_stall(self):
        player = JengaPlayer(max_delay=0.25)
        player.handoff_target_at = 3.0
        player.handoff_waited = True
        player.update(None, 3.4, 3.4)
        self.assertIsNone(player.handoff_target_at)
        self.assertTrue(player.handoff_waited)
        player.mark_released(3.5, observation().carried)
        self.assertFalse(player.handoff_waited)
        player.handoff_target_at = 6.0
        player.handoff_waited = True
        player.reset_motion()
        self.assertIsNone(player.handoff_target_at)
        self.assertFalse(player.handoff_waited)

    def test_handoff_direction_requires_an_early_confirmed_new_stroke(self):
        for direction in (-1, 1):
            player = JengaPlayer()
            player.horizontal_geometry = (960, 285)
            player.mark_released(2.0, JengaPiece(960, 220, 390, 120))
            player.confirm_placement()
            obs = JengaObservation(
                JengaPiece(960 + 200 * direction, 220, 390, 120),
                JengaPiece(960, 770, 940, 100),
                JengaPiece(960 + 200 * direction, 160, 220, 32),
            )
            player.update(obs, 3.1, 3.15)
            self.assertEqual(player.handoff_direction, direction)
            player.reset_motion()
            self.assertIsNone(player.handoff_direction)
            player.horizontal_geometry = (960, 285)
            player.mark_released(4.0, obs.carried)
            player.confirm_placement()
            player.update(obs, 6.0, 6.05)
            self.assertIsNone(player.handoff_direction)

    def test_handoff_choice_preserves_the_full_good_contact_budget(self):
        for direction in (-1, 1):
            player = JengaPlayer(max_delay=0.25)
            player.handoff_direction = direction
            hit = False
            for t in np.arange(0, 12, 0.08):
                x = 960 + 285 * math.sin(1.6 * t)
                bottom = 390 - 91.2 * math.sin(3.2 * t)
                obs = JengaObservation(
                    JengaPiece(x, bottom - 170, bottom, 120, kind="monkey"),
                    JengaPiece(960 + 80 * math.sin(math.pi / 2 * t + 0.4), 770, 940, 100, kind="monkey"),
                    JengaPiece(x, bottom - 230, bottom - 170, 32),
                )
                if player.update(obs, t, t + 0.07):
                    prediction = player.prediction
                    self.assertLessEqual(prediction["landing_error"], prediction["landing_margin"])
                    release = t + 0.07 + 0.045 + player.release_delay
                    for jitter in (-1 / 60, 0.0, 1 / 60):
                        when = release + jitter
                        fall = fixed_fall_time(770 - (390 - 91.2 * math.sin(3.2 * when)))
                        landed = 960 + 285 * math.sin(1.6 * when) + 456 * math.cos(1.6 * when) * fall
                        target = 960 + 80 * math.sin(math.pi / 2 * (when + fall) + 0.4)
                        self.assertGreaterEqual(1 - abs(landed - target) / (0.4 * 228), 0.5)
                    hit = True
                    break
            self.assertTrue(hit, direction)

    def test_handoff_preference_cannot_veto_a_verified_good_for_many_orbits(self):
        recorded = json.loads((FIXTURES / "handoff_rejected_good.json").read_text(encoding="utf-8"))
        player = JengaPlayer(max_delay=0.25)
        player.horizontal_geometry = recorded["horizontal_geometry"]
        player.handoff_direction = recorded["handoff_direction"]
        accepted = None
        for row in recorded["samples"]:
            obs = JengaObservation(*(JengaPiece(**row[key]) for key in ("held", "tower", "bird")))
            if player.update(obs, row["observed_at"], row["now"]):
                accepted = player.prediction
                break
        self.assertIsNotNone(accepted)
        self.assertLess(accepted["now"] + accepted["delay"], 4.0)
        self.assertLessEqual(accepted["landing_error"], accepted["landing_margin"])
        amplitude = np.hypot(*accepted["coefficients"][2][1:])
        self.assertLess(accepted["target_velocity"] * player.handoff_direction, 0.55 * math.pi / 2 * amplitude)

    def test_low_sway_waits_for_the_nearby_inward_contact(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                player = JengaPlayer(max_delay=0.25)
                release = None
                for t in np.arange(0, 5, 0.06):
                    x = 960 + direction * 285 * math.sin(1.6 * t)
                    bottom = 390 - 91.2 * math.sin(3.2 * t)
                    obs = JengaObservation(
                        JengaPiece(x, bottom - 170, bottom, 120, kind="monkey"),
                        JengaPiece(960 + direction * 30, 770, 940, 100, kind="monkey"),
                        JengaPiece(x, bottom - 230, bottom - 170, 32),
                    )
                    if player.update(obs, t, t + 0.05):
                        release = t + 0.095 + player.release_delay
                        break
                self.assertIsNotNone(release)
                self.assertLess(release, 4.2)
                fall = fixed_fall_time(770 - (390 - 91.2 * math.sin(3.2 * release))) + 1 / 60
                landed = 960 + direction * (285 * math.sin(1.6 * release) + 456 * math.cos(1.6 * release) * fall)
                self.assertLess((landed - (960 + direction * 30)) * direction, 0)
                self.assertGreaterEqual(1 - abs(landed - (960 + direction * 30)) / (0.4 * 228), 0.5)

    def test_shifted_tower_prefers_corrective_offset_with_full_contact_guard(self):
        for shift in (-90, 90):
            player = JengaPlayer(max_delay=0.25)
            pressed_at = None
            for t in np.arange(0, 8, 0.08):
                x = 960 + 285 * math.sin(1.6 * t)
                bottom = 390 - 91.2 * math.sin(3.2 * t)
                target = 960 + shift + 40 * math.sin(math.pi / 2 * t + 0.4)
                obs = JengaObservation(
                    JengaPiece(x, bottom - 170, bottom, 100, kind="monkey"),
                    JengaPiece(target, 770, 940, 100, kind="monkey"),
                    JengaPiece(x, bottom - 230, bottom - 170, 32),
                )
                # Keep observing without dispatching until a corrective
                # candidate exists; earlier safe opposite offsets are allowed.
                if player.update(obs, t, t + 0.07) and player.prediction["nominal_offset"] * shift < 0:
                    pressed_at = t + 0.07 + player.release_delay
                    break
            self.assertIsNotNone(pressed_at, shift)
            self.assertLess(player.prediction["desired_offset"] * shift, 0)
            self.assertLess(player.prediction["nominal_offset"] * shift, 0)
            self.assertLessEqual(player.prediction["landing_error"], player.prediction["landing_margin"])
            for latency in (0.0225, 0.045, 0.0675):
                when = pressed_at + latency
                fall = fixed_fall_time(770 - (390 - 91.2 * math.sin(3.2 * when)))
                for flight_error in (-1 / 30, 0, 1 / 30):
                    duration = fall + flight_error
                    landed = 960 + 285 * math.sin(1.6 * when) + 456 * math.cos(1.6 * when) * duration
                    for contact_tick in (0, 1 / 60, 1 / 30):
                        target = 960 + shift + 40 * math.sin(math.pi / 2 * (when + duration + contact_tick) + 0.4)
                        self.assertGreaterEqual(max(0, 1 - abs(landed - target) / (0.4 * 228)), 0.5)

    def test_flight_duration_drift_does_not_repeat_live_miss(self):
        # Layer ten had smooth observations and no sudden contour outlier. Its
        # real Miss exposed a budget that moved only the tower when contact
        # was early/late, leaving the falling sculpture's drift unchanged.
        rows = json.loads((FIXTURES / "fall_contact_drift_miss.json").read_text(encoding="utf-8"))
        for kind in ("", "owl"):
            player = JengaPlayer(max_delay=0.25)
            for row in rows:
                data = row["obs"]
                obs = JengaObservation(*(JengaPiece(**data[k]) for k in ("held", "tower", "bird"))) if data else None
                if obs and kind:
                    obs = replace(obs, carried=replace(obs.carried, kind=kind))
                safe = player.update(obs, row["t"] - row["age"], row["t"])
            if safe:
                # This scene may now be used at a demonstrably earlier time;
                # never repeat the recorded failing dispatch. Its measured
                # first-contact offset was about -52 normalized pixels. The
                # local speed gives a conservative correction for advancing
                # this leftward release; leave 6px for that visual measurement.
                advance = rows[-1]["delay"] - player.release_delay
                self.assertGreaterEqual(advance, 0.03, kind)
                prediction = player.prediction
                corrected = -52 - prediction["relative_velocity"] * advance
                self.assertLessEqual(abs(corrected) + 6, prediction["landing_margin"], kind)
                self.assertLessEqual(prediction["landing_error"], prediction["landing_margin"])
            else:
                self.assertIsNone(player.prediction)

    def test_rendered_motion_compensation_range_preserves_good_contact(self):
        # Independent motion and fixed-step integration verify a range around
        # different effective capture/input/interpolation compensations.
        for input_lead in (1 / 30, 0.045, 0.09):
            player = JengaPlayer(max_delay=0.25)
            pressed_at = None
            for t in np.arange(0, 15, 0.08):
                x = 960 + 285 * math.sin(1.6 * t)
                bottom = 390 - 91.2 * math.sin(3.2 * t)
                obs = JengaObservation(
                    JengaPiece(x, bottom - 170, bottom, 100, kind="monkey"),
                    JengaPiece(960 + 280 * math.sin(math.pi / 2 * t + 0.7), 770, 940, 100, kind="monkey"),
                    JengaPiece(x, bottom - 230, bottom - 170, 32),
                )
                if player.update(obs, t, t + 0.07, input_lead=input_lead):
                    pressed_at = t + 0.07 + player.release_delay
                    break
            self.assertIsNotNone(pressed_at, input_lead)
            uncertainty = max(input_lead / 2, 1 / 60)
            for latency in (input_lead - uncertainty, input_lead, input_lead + uncertainty):
                when = pressed_at + latency
                fall = fixed_fall_time(770 - (390 - 91.2 * math.sin(3.2 * when)))
                landed = 960 + 285 * math.sin(1.6 * when) + 456 * math.cos(1.6 * when) * fall
                for flight_error in (-1 / 30, 0.0, 1 / 30):
                    duration = fall + flight_error
                    landed = 960 + 285 * math.sin(1.6 * when) + 456 * math.cos(1.6 * when) * duration
                    for contact_tick in (0.0, 1 / 60, 1 / 30):
                        target = 960 + 280 * math.sin(math.pi / 2 * (when + duration + contact_tick) + 0.7)
                        self.assertGreaterEqual(max(0.0, 1 - abs(landed - target) / (0.4 * 228)), 0.5)

    def test_fast_short_arc_does_not_repeat_720p_live_miss(self):
        # Real BitBlt observations before the nineteenth throw at 1280x720.
        # This crossing looked centered, but the piece missed while a one-frame
        # increase past 1.2 s had removed the short-arc reserve. The previous
        # full orbit supplies the measured horizontal geometry.
        rows = json.loads((FIXTURES / "fast_crossing_720_miss.json").read_text(encoding="utf-8"))
        player = JengaPlayer(max_delay=0.25)
        player.horizontal_geometry = (972.8008845790715, 287.80017405507675)
        for row in rows:
            data = row["obs"]
            obs = JengaObservation(*(JengaPiece(**data[k]) for k in ("held", "tower", "bird")))
            safe = player.update(obs, row["t"] - row["age"], row["t"], input_lead=0.045)
        self.assertFalse(safe)
        self.assertIsNone(player.prediction)

    def test_release_frame_contour_outlier_does_not_repeat_live_miss(self):
        # Real WGC observations immediately before the 2026-09-28 layer-13
        # Miss. The historical attachment remains smooth while the last
        # contour jumps sideways. Earlier clean observations stay usable.
        rows = json.loads((FIXTURES / "sway_miss_attachment_outlier.json").read_text(encoding="utf-8"))
        player = JengaPlayer(max_delay=0.25)
        earlier_safe = False
        for row in rows:
            data = row["obs"]
            obs = (
                JengaObservation(*(JengaPiece(**data[k]) for k in ("held", "tower", "bird")))
                if data is not None
                else None
            )
            safe = player.update(obs, row["t"] - row["age"], row["t"])
            if row is not rows[-1]:
                earlier_safe |= safe
        self.assertTrue(earlier_safe)
        self.assertFalse(safe)
        self.assertIsNone(player.prediction)

    def test_good_contact_can_release_when_centers_never_cross(self):
        player = JengaPlayer()
        release = None
        target = 1290
        for t in np.arange(0, 6, 0.08):
            x = 960 + 285 * math.sin(1.6 * t)
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            obs = JengaObservation(
                JengaPiece(x, bottom - 170, bottom, 100, kind="monkey"),
                JengaPiece(target, 770, 940, 100, kind="monkey"),
                JengaPiece(x, bottom - 230, bottom - 170, 32),
            )
            if player.update(obs, t, t + 0.07):
                release = t + 0.07 + 0.045 + player.release_delay
                break
        self.assertIsNotNone(release)
        projected = []
        for t in np.arange(0, 2 * math.pi / 1.6, 0.005):
            gap = 770 - (390 - 91.2 * math.sin(3.2 * t))
            duration = fixed_fall_time(gap)
            projected.append(960 + 285 * math.sin(1.6 * t) + 456 * math.cos(1.6 * t) * duration)
        self.assertLess(max(projected), target)  # No exact contact-center intersection exists.
        gap = 770 - (390 - 91.2 * math.sin(3.2 * release))
        duration = fixed_fall_time(gap)
        landed = 960 + 285 * math.sin(1.6 * release) + 456 * math.cos(1.6 * release) * duration
        self.assertGreater(1 - abs(landed - target) / (0.4 * 228), 0.5)

    def test_confirmed_placement_rearms_new_piece_without_hidden_absence_frame(self):
        player = JengaPlayer()
        released = observation(1180).carried
        player.mark_released(0.0, released)
        nearby = observation(1200)
        self.assertFalse(player.update(nearby, 0.9, 0.9))
        self.assertEqual(player.history, [])
        player.confirm_placement()
        self.assertFalse(player.update(nearby, 1.0, 1.0))
        self.assertEqual(len(player.history), 1)
        player.mark_released(1.1, nearby.carried)
        self.assertFalse(player.placement_confirmed)
        player.confirm_placement()
        self.assertFalse(player.update(observation(900), 1.5, 1.5))
        self.assertEqual(player.history, [])  # Contact evidence cannot bypass the dwell.

    def test_horizontal_prior_refits_vertical_camera_center_for_new_piece(self):
        for cy in (200, 360):
            times = np.arange(0.2, 1.5, 0.09)
            x = 960 + 285 * np.sin(1.6 * times + 0.7)
            y = cy - 91.2 * np.sin(3.2 * times + 1.4)
            _, geometry, residual = _fit_figure_eight_phase(np.column_stack((times, x, y)), (960, 285))
            self.assertAlmostEqual(geometry[2], cy, delta=0.2)
            self.assertLess(np.max(residual), 0.3)

    def test_short_observation_uses_prior_and_independent_contact_check(self):
        player = JengaPlayer(max_delay=0.25)
        player.horizontal_geometry = (960, 285)
        release = None
        for t in np.arange(0, 2, 0.08):
            x = 960 + 285 * math.sin(1.6 * t)
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            target = 960 + 60 * math.sin(math.pi / 2 * t + 0.4)
            obs = JengaObservation(
                JengaPiece(x, bottom - 150, bottom, 100, kind="monkey"),
                JengaPiece(target, 770, 930, 100, kind="monkey"),
                JengaPiece(x, bottom - 230, bottom - 170, 32),
            )
            if player.update(obs, t, t + 0.07):
                release = t + 0.07 + 0.045 + player.release_delay
                break
        self.assertIsNotNone(release)
        self.assertTrue(player.prediction["short_observation"])
        # Good is permitted at the earlier crossing. Independently integrate
        # every release/flight tick corner and check actual face overlap, rather
        # than requiring the nominal centers to coincide to two pixels.
        for release_jitter in (-1 / 60, 0, 1 / 60):
            when = release + release_jitter
            gap = 770 - (390 - 91.2 * math.sin(3.2 * when))
            fall = fixed_fall_time(gap)
            for flight_jitter in (-1 / 60, 0, 1 / 60):
                landed = 960 + 285 * math.sin(1.6 * when) + 456 * math.cos(1.6 * when) * fall
                target = 960 + 60 * math.sin(math.pi / 2 * (when + fall + 1 / 60 + flight_jitter) + 0.4)
                overlap_ratio = max(0, 0.4 - abs(landed - target) / 228) / 0.4
                self.assertGreaterEqual(overlap_ratio, 0.5)
        player.mark_released(release, obs.carried)
        self.assertEqual(player.horizontal_geometry, (960, 285))
        player.reset_motion()
        self.assertIsNone(player.horizontal_geometry)

    def test_missing_detections_refit_clock_without_discarding_measured_orbit(self):
        player = JengaPlayer(max_delay=0.25)
        player.horizontal_geometry = (960, 285)

        def sample(t):
            x = 960 + 285 * math.sin(1.6 * t)
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            target = 960 + 60 * math.sin(math.pi / 2 * t + 0.4)
            return JengaObservation(
                JengaPiece(x, bottom - 150, bottom, 100, kind="monkey"),
                JengaPiece(target, 770, 930, 100, kind="monkey"),
                JengaPiece(x, bottom - 230, bottom - 170, 32),
            )

        player.update(sample(-4), -4, -3.93)
        self.assertFalse(player.update(sample(0), 0, 0.07))
        self.assertEqual(len(player.history), 1)
        self.assertEqual(player.horizontal_geometry, (960, 285))
        for t in np.arange(0.08, 2, 0.08):
            if player.update(sample(t), t, t + 0.07):
                break
        self.assertIsNotNone(player.prediction)
        self.assertTrue(player.prediction["short_observation"])
        release = t + 0.07 + 0.045 + player.release_delay
        for release_jitter in (-1 / 60, 0, 1 / 60):
            when = release + release_jitter
            fall = fixed_fall_time(770 - sample(when).carried.bottom)
            landed = sample(when).carried.x + 456 * math.cos(1.6 * when) * fall
            for contact_jitter in (-1 / 60, 0, 1 / 60):
                target = sample(when + fall + 1 / 60 + contact_jitter).tower.x
                self.assertGreaterEqual(max(0, 0.4 - abs(landed - target) / 228) / 0.4, 0.5)
        player.reset_motion()
        self.assertIsNone(player.horizontal_geometry)

    def test_occlusion_within_one_orbit_keeps_verified_contact_available(self):
        recorded = json.loads((FIXTURES / "occluded_clock_reset.json").read_text(encoding="utf-8"))
        player = JengaPlayer(max_delay=0.25)
        accepted = None
        for row in recorded["samples"]:
            data = row["obs"]
            obs = JengaObservation(*(JengaPiece(**data[key]) for key in ("held", "tower", "bird"))) if data else None
            if player.update(obs, row["observed_at"], row["now"]):
                accepted = player.prediction
                break
        self.assertIsNotNone(accepted)
        self.assertLess(accepted["now"] + accepted["delay"], 25)
        self.assertFalse(accepted["short_observation"])
        self.assertLessEqual(accepted["landing_error"], accepted["landing_margin"])

    def test_contact_budget_preserves_native_good_face_area(self):
        # Use polygon intersection independently of the predictor's boundary
        # algebra, including unequal faces and their extracted depth offsets.
        faces = {
            "monkey": (0.40, 0.43, -0.01),
            "owl": (0.40, 0.42, -0.041),
            "rabbit": (0.36, 0.40, -0.01),
            "": (0.36, 0.40, 0.0),
        }

        def polygon(face, x, z):
            width, depth, _ = face
            return np.float32(
                [
                    (x - width / 2, z - depth / 2),
                    (x + width / 2, z - depth / 2),
                    (x + width / 2, z + depth / 2),
                    (x - width / 2, z + depth / 2),
                ]
            )

        for carried, a in faces.items():
            for tower, b in faces.items():
                for scale in (150, 228, 300):
                    with self.subTest(carried=carried, tower=tower, scale=scale):
                        distance = JengaPlayer.good_margin(carried, tower, scale) / scale
                        z_a, z_b = (0.0, 0.045) if "" in (carried, tower) else (a[2], b[2])
                        overlap, _ = cv2.intersectConvexConvex(polygon(a, distance, z_a), polygon(b, 0, z_b))
                        self.assertGreaterEqual(overlap / min(a[0] * a[1], b[0] * b[1]), 0.5)

    def test_vertical_template_jitter_is_bounded_at_contact(self):
        player = JengaPlayer()
        release = None
        for index, t in enumerate(np.arange(0, 4.2, 0.08)):
            x = 960 + 285 * math.sin(1.6 * t)
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            obs = JengaObservation(
                JengaPiece(x, bottom - 150, bottom + (12 if index % 2 else -12), 100, kind="monkey"),
                JengaPiece(960, 770, 930, 100, kind="monkey"),
                JengaPiece(x, bottom - 230, bottom - 170, 32),
            )
            if player.update(obs, t, t + 0.07):
                release = t + 0.07 + 0.045 + player.release_delay
                break
        self.assertIsNotNone(release)
        self.assertGreater(player.prediction["fit_errors"][1], 8)
        gap = 770 - (390 - 91.2 * math.sin(3.2 * release))
        fall = fixed_fall_time(gap)
        landed = 960 + 285 * math.sin(1.6 * release) + 456 * math.cos(1.6 * release) * fall
        self.assertLess(abs(landed - 960), 0.18 * 228)

    def test_large_vertical_disagreement_cannot_press_through_contact_guard(self):
        player = JengaPlayer()
        for index, t in enumerate(np.arange(0, 6, 0.08)):
            x = 960 + 285 * math.sin(1.6 * t)
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            obs = JengaObservation(
                JengaPiece(x, bottom - 150, bottom + (150 if index % 2 else -150), 100, kind="monkey"),
                JengaPiece(960, 770, 930, 100, kind="monkey"),
                JengaPiece(x, bottom - 230, bottom - 170, 32),
            )
            self.assertFalse(player.update(obs, t, t + 0.07))

    def test_isolated_partial_edges_cannot_supply_release_height(self):
        player = JengaPlayer()
        release = None
        for index, t in enumerate(np.arange(0, 4.2, 0.08)):
            x = 960 + 285 * math.sin(1.6 * t)
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            partial = index % 4 == 0
            obs = JengaObservation(
                JengaPiece(x, bottom - 170, bottom - (70 if partial else 0), 100, kind="monkey"),
                JengaPiece(960, 770, 940, 100, kind="monkey"),
                JengaPiece(x, bottom - 230, bottom - 170, 32),
            )
            hit = player.update(obs, t, t + 0.07)
            if partial:
                self.assertFalse(hit)
            elif hit:
                release = t + 0.07 + 0.045 + player.release_delay
                break
        self.assertIsNotNone(release)
        gap = 770 - (390 - 91.2 * math.sin(3.2 * release))
        duration = fixed_fall_time(gap)
        landed = 960 + 285 * math.sin(1.6 * release) + 456 * math.cos(1.6 * release) * duration
        self.assertLess(abs(landed - 960), 0.08 * 228)

    def test_half_period_observation_reaches_first_verified_crossing(self):
        player = JengaPlayer()
        release = None
        for t in np.arange(0, 4.2, 0.08):
            x = 960 + 285 * math.sin(1.6 * t)
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            obs = JengaObservation(
                JengaPiece(x, bottom - 150, bottom, 100),
                JengaPiece(960, 770, 930, 100),
                JengaPiece(x, bottom - 230, bottom - 170, 32),
            )
            if player.update(obs, t, t + 0.07):
                release = t + 0.07 + 0.045 + player.release_delay
                break
        self.assertIsNotNone(release)
        self.assertLess(release, 4.2)
        gap = 770 - (390 - 91.2 * math.sin(3.2 * release))
        fall = fixed_fall_time(gap)
        landed = 960 + 285 * math.sin(1.6 * release) + 456 * math.cos(1.6 * release) * fall
        self.assertLess(abs(landed - 960), 0.08 * 228)

    def test_hidden_turning_point_does_not_shrink_native_radius(self):
        times = np.arange(0, 6, 0.09)
        x = 960 + 285 * np.sin(1.6 * times)
        y = 200 - 91.2 * np.sin(3.2 * times)
        visible = x < 1100
        _, geometry, residual = _fit_figure_eight_phase(np.column_stack((times, x, y))[visible])
        self.assertAlmostEqual(geometry[1], 285, delta=1)
        self.assertLess(np.quantile(residual, 0.9), 0.2)

    def test_fall_time_matches_independent_fixed_step_velocity_integration(self):
        for scale in (150, 228, 300):
            for gap in (0, 20, 150, 350, 600):
                self.assertAlmostEqual(JengaPlayer.fall_duration(gap, scale), fixed_fall_time(gap, scale), delta=1e-10)

    def test_repeated_native_handoffs_keep_good_contact_as_sway_grows(self):
        # BeginMovement resets the new bird, but the tower keeps its clock.
        # Integrate contact independently and feed each actual landing back
        # into the next layer, including all three independent timing axes.
        for direction, max_delay in ((-1, 0.25), (1, 0.25), (-1, 0.4), (1, 0.4)):
            with self.subTest(direction=direction, max_delay=max_delay):
                player = JengaPlayer(max_delay=max_delay)
                player.horizontal_geometry = (973, 285)
                player.handoff_direction = direction
                birth, center, amplitude = 0.0, 978.0, 40.0
                phase = 0.0 if direction < 0 else math.pi
                pending = None
                releases = []
                for t in np.arange(0.0, 40.0, 0.06):
                    if pending is not None and t >= pending[0]:
                        contact, offset, birth = pending
                        center += offset
                        amplitude += 35.0
                        player.confirm_placement()
                        pending = None
                    obs = None
                    if pending is None and t >= birth:
                        tau = t - birth
                        x = 978 + direction * 285 * math.sin(1.6 * tau)
                        bottom = 400 - 91.2 * math.sin(3.2 * tau)
                        target = center + amplitude * math.sin(math.pi / 2 * t + phase)
                        obs = JengaObservation(
                            JengaPiece(x, bottom - 173, bottom, 118, kind="owl"),
                            JengaPiece(target, 770, 943, 118, kind="owl"),
                            JengaPiece(x - 5, bottom - 230, bottom - 173, 32),
                        )
                    if not player.update(obs, t, t + 0.05):
                        continue
                    key = t + 0.05 + player.release_delay
                    when = key + 0.045
                    nominal = None
                    for release_jitter in (-1 / 60, 0.0, 1 / 60):
                        release = when + release_jitter
                        tau = release - birth
                        bottom = 400 - 91.2 * math.sin(3.2 * tau)
                        fall = fixed_fall_time(770 - bottom, 228) + 1 / 60
                        x = 978 + direction * 285 * math.sin(1.6 * tau)
                        velocity = direction * 456 * math.cos(1.6 * tau)
                        for flight_jitter in (-(1 / 60 + 0.008), 0.0, 1 / 60 + 0.008):
                            duration = fall + flight_jitter
                            landed = x + velocity * duration
                            for receiver_jitter in (-1 / 60, 0.0, 1 / 60):
                                contact = release + duration
                                target = center + amplitude * math.sin(
                                    math.pi / 2 * (contact + 1 / 60 + receiver_jitter) + phase
                                )
                                self.assertGreaterEqual(1 - abs(landed - target) / (0.4 * 228), 0.5)
                                if release_jitter == flight_jitter == receiver_jitter == 0:
                                    nominal = (contact, landed - target, contact + 1 / 60 + 1 / 30)
                    releases.append(key)
                    player.mark_released(key + 0.04, obs.carried)
                    pending = nominal
                    if len(releases) == 8:
                        break
                self.assertEqual(len(releases), 8)
                self.assertLess(releases[-1], 35)

    def test_twenty_contacts_keep_good_area_and_finish_before_timeout(self):
        # A whole sequence exposes a last-piece lock that isolated contacts
        # cannot. The tower clock continues while BeginMovement resets each
        # new bird; mixed native faces and three independent clocks are checked
        # by fixed-step integration rather than the player's contact bound.
        amplitudes = (0, 0, 0, 0, 13, 26, 42, 59, 82, 103, 129, 155, 191, 220, 253, 290, 329, 369, 411, 455)
        faces = {"monkey": (0.4, 0.43, -0.01), "owl": (0.4, 0.42, -0.041), "rabbit": (0.36, 0.4, -0.01)}
        heights = {"monkey": 0.754531, "owl": 0.758492, "rabbit": 0.894245}
        kinds = ("owl", "monkey", "owl", "owl", "monkey", "owl")
        for direction, phase in ((-1, 0.0), (1, 0.7)):
            with self.subTest(direction=direction, phase=phase):
                player = JengaPlayer(max_delay=0.4)
                center, birth, stage = 973.0, 0.0, 0
                tower_kind, pending = "monkey", None
                for t in np.arange(0, 120, 0.06):
                    if pending is not None and t >= pending[0]:
                        _, offset, birth, tower_kind = pending
                        center += offset
                        stage += 1
                        player.confirm_placement()
                        pending = None
                    if stage == 20:
                        break
                    kind = "rabbit" if stage == 19 else kinds[stage % len(kinds)]
                    height = heights[kind] * 228
                    bottom_center = 400 + height - heights["owl"] * 228
                    top, amplitude = 796 - 6 * stage, amplitudes[stage]
                    obs = None
                    if pending is None and t >= birth:
                        tau = t - birth
                        x = 973 + direction * 285 * math.sin(1.6 * tau)
                        bottom = bottom_center - 91.2 * math.sin(3.2 * tau)
                        target = center + amplitude * math.sin(math.pi / 2 * t + phase)
                        obs = JengaObservation(
                            JengaPiece(x, bottom - height, bottom, 118, kind=kind),
                            JengaPiece(target, top, top + 173, 118, kind=tower_kind),
                            JengaPiece(x, bottom - height - 60, bottom - height, 32),
                        )
                    if not player.update(obs, t, t + 0.05):
                        continue
                    key = t + 0.05 + player.release_delay
                    for r in (-1 / 60, 0.0, 1 / 60):
                        release = key + 0.045 + r
                        tau = release - birth
                        bottom = bottom_center - 91.2 * math.sin(3.2 * tau)
                        fall = fixed_fall_time(top - bottom, 228) + 1 / 60
                        x = 973 + direction * 285 * math.sin(1.6 * tau)
                        velocity = direction * 456 * math.cos(1.6 * tau)
                        for f in (-(1 / 60 + 0.008), 0.0, 1 / 60 + 0.008):
                            duration = fall + f
                            landed = x + velocity * duration
                            for tick in (-1 / 60, 0.0, 1 / 60):
                                contact = release + duration
                                target = center + amplitude * math.sin(math.pi / 2 * (contact + 1 / 60 + tick) + phase)
                                a, b = faces[kind], faces[tower_kind]
                                width = max(0.0, min(a[0], b[0], (a[0] + b[0]) / 2 - abs(landed - target) / 228))
                                depth = min(a[1], b[1], (a[1] + b[1]) / 2 - abs(a[2] - b[2]))
                                self.assertGreaterEqual(width * depth / min(a[0] * a[1], b[0] * b[1]), 0.5)
                                if r == f == tick == 0:
                                    next_birth = contact + 1 / 60 + 1 / 30 + (-1 / 60, 0.0, 1 / 60)[stage % 3]
                                    pending = contact, landed - target, next_birth, kind
                    player.mark_released(key + 0.04, obs.carried)
                self.assertEqual(stage, 20)
                self.assertLess(t, 120)

    def test_uncertain_future_route_does_not_displace_verified_current_good(self):
        trace = json.loads((FIXTURES / "uncertain_future_route.json").read_text())
        player = JengaPlayer(max_delay=0.4)
        player.horizontal_geometry = trace["horizontal_geometry"]
        player.handoff_direction = trace["direction"]
        player.mark_released(0.04, JengaPiece(**trace["released_piece"]))
        ready = None
        for row in trace["rows"]:
            if row["t"] >= trace["confirmation_at"]:
                player.confirm_placement()
            data = row["obs"]
            obs = JengaObservation(*(JengaPiece(**data[key]) for key in ("held", "tower", "bird"))) if data else None
            if player.update(obs, row["t"] - row["age"], row["t"]):
                ready = row["t"] + player.release_delay
                self.assertLessEqual(player.prediction["landing_error"], player.prediction["landing_margin"])
                break
        self.assertIsNotNone(ready)
        self.assertLess(ready, 3.55)

    def test_greedy_high_sway_releases_first_good_without_handoff_wait(self):
        trace = json.loads((FIXTURES / "uncertain_future_route.json").read_text())
        player = JengaPlayer(max_delay=0.4, greedy_high_sway=True)
        player.horizontal_geometry = trace["horizontal_geometry"]
        player.handoff_direction = trace["direction"]
        player.mark_released(0.04, JengaPiece(**trace["released_piece"]))
        ready = None
        for row in trace["rows"]:
            if row["t"] >= trace["confirmation_at"]:
                player.confirm_placement()
            data = row["obs"]
            obs = JengaObservation(*(JengaPiece(**data[key]) for key in ("held", "tower", "bird"))) if data else None
            if player.update(obs, row["t"] - row["age"], row["t"]):
                ready = row["t"] + player.release_delay
                self.assertTrue(player.prediction["greedy"])
                self.assertLessEqual(player.prediction["landing_error"], player.prediction["landing_margin"])
                self.assertIsNone(player.handoff_target_at)
                self.assertFalse(player.handoff_waited)
                break
        self.assertIsNotNone(ready)
        self.assertLess(ready, 3.55)

    def test_eighteenth_complete_monkey_receiver_uses_current_contact_window(self):
        # The real piece was recognized for 47 seconds without a release. The
        # receiver's full motion fit and the fall-step envelope already cover
        # contact; a second independent step discarded its first Good window.
        rows = json.loads((FIXTURES / "eighteenth_current_contact.json").read_text())["rows"]
        player = JengaPlayer(max_delay=0.4, greedy_high_sway=True)
        first = None
        for row in rows:
            parts = row["obs"]
            obs = JengaObservation(*(JengaPiece(**parts[key]) for key in ("held", "tower", "bird"))) if parts else None
            if player.update(obs, row["t"] - row["age"], row["t"]):
                first = player.prediction.copy()
                break
        self.assertIsNotNone(first)
        self.assertTrue(first["measured_contact"])
        self.assertLess(first["now"] + first["delay"], 4.1)
        self.assertLessEqual(first["landing_error"], first["landing_margin"])
        # Compare the forecast with actual later receiver positions from the
        # original no-input trace, independently of the current harmonic fit.
        later = np.asarray(
            [[row["t"] - row["age"], row["obs"]["tower"]["x"]] for row in rows if row["obs"]]
        )
        release = first["now"] + first["delay"] + 0.045
        for flight_jitter in (-first["flight_uncertainty"], 0, first["flight_uncertainty"]):
            contact = release + first["flight_time"] + flight_jitter
            predicted = JengaPlayer._value(np.asarray(first["coefficients"][2]), math.pi / 2, contact - first["at"])
            measured = float(np.interp(contact, later[:, 0], later[:, 1]))
            self.assertLess(abs(predicted - measured), 8)

    def test_owl_receiving_plane_releases_before_the_current_window_closes(self):
        rows = json.loads((FIXTURES / "owl_receiving_plane.json").read_text())["rows"]
        player = JengaPlayer(max_delay=0.4, greedy_high_sway=True)
        first = None
        for row in rows:
            parts = row["obs"]
            obs = JengaObservation(*(JengaPiece(**parts[key]) for key in ("held", "tower", "bird"))) if parts else None
            if player.update(obs, row["t"] - row["age"], row["t"]):
                first = player.prediction
                break
        self.assertIsNotNone(first)
        self.assertTrue(first["measured_contact"])
        self.assertEqual(first["target"]["kind"], "owl")
        self.assertLess(first["now"] + first["delay"], 4.1)
        # Native box top is .615; visible feet are at -.064 in root space.
        # The varied reference crop must not move this physical receiving plane.
        plane = first["target"]["bottom"] - (0.615 - -0.064) * first["scale"]
        self.assertAlmostEqual(first["contact_top"], plane)
        self.assertLessEqual(first["landing_error"], first["landing_margin"])

    def test_eighteenth_partial_or_weak_body_keeps_original_contact_reserve(self):
        rows = json.loads((FIXTURES / "eighteenth_current_contact.json").read_text())["rows"]
        for incomplete in (True, False):
            player = JengaPlayer(max_delay=0.4, greedy_high_sway=True)
            for row in rows:
                parts = row["obs"]
                obs = None
                if parts:
                    held = JengaPiece(**parts["held"])
                    held = replace(held, top=held.bottom - 144, kind="") if incomplete else replace(held, confidence=0.74)
                    obs = JengaObservation(held, JengaPiece(**parts["tower"]), JengaPiece(**parts["bird"]))
                self.assertFalse(player.update(obs, row["t"] - row["age"], row["t"]))

    def test_native_short_arc_releases_first_good_and_preserves_actual_face_area(self):
        player = JengaPlayer(max_delay=0.4, greedy_release=True)
        player.horizontal_geometry = (960, 285)
        scale = 228
        contact_top = 770
        ready = None
        for t in np.arange(0, 4, 0.08):
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            x = 960 + 285 * math.sin(1.6 * t)
            obs = JengaObservation(
                JengaPiece(x, bottom - 0.754531 * scale, bottom, 120, kind="monkey"),
                JengaPiece(
                    960 + 40 * math.sin(math.pi / 2 * t + 0.7),
                    contact_top - 0.079492 * scale,
                    contact_top + 0.679 * scale,
                    118,
                    kind="owl",
                ),
                JengaPiece(x, bottom - 230, bottom - 170, 32),
            )
            if player.update(obs, t, t + 0.05):
                ready = player.prediction
                break
        self.assertIsNotNone(ready)
        self.assertTrue(ready["short_observation"])
        self.assertTrue(ready["measured_contact"])
        self.assertTrue(ready["greedy"])
        self.assertLess(t + player.release_delay, 2)
        # Integrate gravity independently and move both bodies through the
        # same contact interval. The owl's visible crown is above its face.
        for release_jitter in (-ready["timing_uncertainty"], 0, ready["timing_uncertainty"]):
            release = t + 0.05 + player.release_delay + 0.045 + release_jitter
            y = 390 - 91.2 * math.sin(3.2 * release)
            first_contact = fixed_fall_time(contact_top - y + 0.005 * scale, scale)
            substep = min(1 / 30, 0.15 / min(10, 49.05 * first_contact))
            for completion in (0, substep / 2, substep):
                fall = first_contact + completion
                landed = 960 + 285 * math.sin(1.6 * release) + 456 * math.cos(1.6 * release) * fall
                receiver = 960 + 40 * math.sin(math.pi / 2 * (release + fall) + 0.7)
                overlap_x = max(0, 0.4 - abs(landed - receiver) / scale)
                overlap_z = (0.43 + 0.42) / 2 - 0.031
                self.assertGreaterEqual(overlap_x * overlap_z / (0.4 * 0.42), 0.5)

    def test_figure_eight_release_accounts_for_momentum_and_moving_target(self):
        for amplitude, phase, interval in ((0, 0, 0.11), (90, 0.7, 0.11), (0, 0, 0.32)):
            player = JengaPlayer()
            hit = None
            for t in np.arange(0, 28, interval):
                angle = 1.6 * t
                x = 960 + 285 * math.sin(angle)
                bottom = 390 - 91.2 * math.sin(2 * angle)
                tower = 960 + amplitude * math.sin(math.pi / 2 * t + phase)
                obs = JengaObservation(
                    JengaPiece(x, bottom - 150, bottom, 100),
                    JengaPiece(tower, 770, 930, 100),
                    JengaPiece(x, bottom - 230, bottom - 170, 32),
                )
                if player.update(obs, t, t + 0.07):
                    release = t + 0.07 + 0.045 + player.release_delay
                    # Solve the independent vertical equation by integration.
                    gap = 770 - (390 - 91.2 * math.sin(3.2 * release))
                    fall = fixed_fall_time(gap)
                    landed = 960 + 285 * math.sin(1.6 * release) + 456 * math.cos(1.6 * release) * fall
                    target = 960 + amplitude * math.sin(math.pi / 2 * (release + fall + 1 / 60) + phase)
                    hit = abs(landed - target)
                    break
            self.assertIsNotNone(hit)
            # Good is allowed before the exact center crossing. This boundary
            # also fits the narrow face with the conservative depth separation.
            self.assertLess(hit, 0.15 * 228)

    def test_actual_capture_releases_preserve_good_room_or_wait(self):
        accepted = 0
        for trace in json.loads((FIXTURES / "physics_traces.json").read_text()):
            player = JengaPlayer()
            for row in trace["rows"]:
                data = row["obs"]
                obs = JengaObservation(*(JengaPiece(**data[k]) for k in ("held", "tower", "bird"))) if data else None
                result = player.update(obs, row["t"] - row["age"], row["t"])
            if not result:
                # A historical Perfect does not prove that a neighboring
                # physics tick is safe. The complete contact budget may wait.
                self.assertIsNone(player.prediction)
                continue
            accepted += 1
            # Estimate motion directly from the saved pixel positions, using
            # neither the player's geometric phase nor its attachment model.
            # A time change at a slow crossing has a different contact effect
            # from the same change at a fast crossing.
            measured = np.asarray(
                [[r["t"] - r["age"], r["obs"]["held"]["x"], r["obs"]["tower"]["x"]] for r in trace["rows"] if r["obs"]]
            )
            measured[:, 0] -= measured[-1, 0]
            coefficients = []
            for column, omega in ((1, 1.6), (2, math.pi / 2)):
                design = np.column_stack(
                    (np.ones(len(measured)), np.sin(omega * measured[:, 0]), np.cos(omega * measured[:, 0]))
                )
                coefficients.append(np.linalg.lstsq(design, measured[:, column], rcond=None)[0])
            held, target = coefficients
            row = trace["rows"][-1]
            old = row["age"] + 0.045 + row["delay"]
            new = row["age"] + 0.045 + player.release_delay

            def relative_contact(t, flight, held=held, target=target):
                x = held @ [1, math.sin(1.6 * t), math.cos(1.6 * t)]
                x += 1.6 * (held[1] * math.cos(1.6 * t) - held[2] * math.sin(1.6 * t)) * flight
                return x - target @ [1, math.sin(math.pi / 2 * (t + flight)), math.cos(math.pi / 2 * (t + flight))]

            for flight in (0.18, 0.25, 0.33):
                # Perfect >=80%, Good >=50%. Keep substantially less than the
                # extra 30% of the narrowest 0.36-unit face as shift allowance.
                self.assertLess(abs(relative_contact(new, flight) - relative_contact(old, flight)), 0.08 * 228)
        self.assertGreater(accepted, 0)

    def test_periodic_attachment_predicts_contact_with_inherited_velocity(self):
        for attachment in (-15, 15):
            player = JengaPlayer(max_delay=0.25)
            release = None
            for t in np.arange(0, 6, 0.08):
                bird = 960 + 285 * math.sin(1.6 * t)
                held = bird + attachment * math.cos(1.6 * t)
                bottom = 390 - 91.2 * math.sin(3.2 * t)
                obs = JengaObservation(
                    JengaPiece(held, bottom - 170, bottom, 100, kind="monkey"),
                    JengaPiece(960, 770, 940, 100, kind="monkey"),
                    JengaPiece(bird, bottom - 230, bottom - 170, 32),
                )
                if player.update(obs, t, t + 0.07):
                    release = t + 0.07 + 0.045 + player.release_delay
                    break
            self.assertIsNotNone(release)
            for release_jitter in (-1 / 60, 0, 1 / 60):
                when = release + release_jitter
                gap = 770 - (390 - 91.2 * math.sin(3.2 * when))
                fall = fixed_fall_time(gap)
                x = 960 + 285 * math.sin(1.6 * when) + attachment * math.cos(1.6 * when)
                velocity = 1.6 * (285 * math.cos(1.6 * when) - attachment * math.sin(1.6 * when))
                landed = x + velocity * fall
                self.assertGreaterEqual(1 - abs(landed - 960) / (0.4 * 228), 0.5)

    def test_centering_preference_keeps_first_safe_good_contact(self):
        for tower in (800, 1120):
            player = JengaPlayer(max_delay=0.25)
            release = None
            for t in np.arange(0, 6, 0.08):
                x = 960 + 285 * math.sin(1.6 * t)
                bottom = 390 - 91.2 * math.sin(3.2 * t)
                obs = JengaObservation(
                    JengaPiece(x, bottom - 170, bottom, 100, kind="monkey"),
                    JengaPiece(tower, 770, 940, 100, kind="monkey"),
                    JengaPiece(x, bottom - 230, bottom - 170, 32),
                )
                if player.update(obs, t, t + 0.07):
                    release = t + 0.115 + player.release_delay
                    break
            self.assertIsNotNone(release)
            nominal = None
            for jitter in (-1 / 60, 0, 1 / 60):
                when = release + jitter
                gap = 770 - (390 - 91.2 * math.sin(3.2 * when))
                fall = fixed_fall_time(gap)
                for flight_jitter in (-1 / 60, 0, 1 / 60):
                    landed = 960 + 285 * math.sin(1.6 * when) + 456 * math.cos(1.6 * when) * fall
                    self.assertGreaterEqual(max(0, 0.4 - abs(landed - tower) / 228) / 0.4, 0.5)
                    if jitter == 0 and flight_jitter == 0:
                        nominal = landed
            self.assertLess(player.prediction["desired_offset"] * (tower - 960), 0)
            # An outward Good may be the only safe candidate in this window.
            # Centering is a preference; it must not veto that contact.
            self.assertLess(abs(nominal - tower), 0.2 * 228)

    def test_neck_vertical_edge_changes_do_not_change_release(self):
        players = [JengaPlayer(max_delay=0.25) for _ in range(2)]
        found = False
        for t in np.arange(0, 6, 0.08):
            x = 960 + 285 * math.sin(1.6 * t)
            bottom = 390 - 91.2 * math.sin(3.2 * t)
            results = []
            for index, player in enumerate(players):
                edge = bottom - 170 + (45 * math.sin(11 * t) if index else 0)
                obs = JengaObservation(
                    JengaPiece(x, bottom - 170, bottom, 100, kind="rabbit"),
                    JengaPiece(960, 770, 940, 100, kind="monkey"),
                    JengaPiece(x, edge - 50, edge, 32),
                )
                results.append(player.update(obs, t, t + 0.07))
            self.assertEqual(results[0], results[1])
            if results[0]:
                self.assertAlmostEqual(players[0].release_delay, players[1].release_delay, delta=1e-9)
                found = True
                break
        self.assertTrue(found)

    def test_observation_rates_and_processing_ages_preserve_good_contact(self):
        for interval, age in ((1 / 60, 0.01), (1 / 30, 0.07), (1 / 15, 0.14), (0.12, 0.07), (0.25, 0.14)):
            with self.subTest(interval=interval, age=age):
                player = JengaPlayer(max_delay=0.25)
                release = None
                for t in np.arange(0, 15, interval):
                    x = 960 + 285 * math.sin(1.6 * t)
                    bottom = 390 - 91.2 * math.sin(3.2 * t)
                    target = 960 + 300 * math.sin(math.pi / 2 * t + 0.4)
                    obs = JengaObservation(
                        JengaPiece(x, bottom - 170, bottom, 100, kind="monkey"),
                        JengaPiece(target, 770, 940, 100, kind="monkey"),
                        JengaPiece(x, bottom - 230, bottom - 170, 32),
                    )
                    if player.update(obs, t, t + age):
                        release = t + age + 0.045 + player.release_delay
                        break
                self.assertIsNotNone(release)
                for release_jitter in (-1 / 60, 0, 1 / 60):
                    when = release + release_jitter
                    fall = fixed_fall_time(770 - (390 - 91.2 * math.sin(3.2 * when)))
                    landed = 960 + 285 * math.sin(1.6 * when) + 456 * math.cos(1.6 * when) * fall
                    for contact_jitter in (-1 / 60, 0, 1 / 60):
                        target = 960 + 300 * math.sin(math.pi / 2 * (when + fall + 1 / 60 + contact_jitter) + 0.4)
                        self.assertGreaterEqual(max(0, 1 - abs(landed - target) / (0.4 * 228)), 0.5)

    def test_duplicate_stale_stationary_and_unknown_frames_do_not_release(self):
        for kind in ("stationary", "stale", "duplicate", "missing"):
            player = JengaPlayer()
            for t in np.arange(0, 7, 0.1):
                obs = None if kind == "missing" else observation()
                self.assertFalse(
                    player.update(obs, 0 if kind == "duplicate" else t, t + (0.3 if kind == "stale" else 0))
                )

    def test_drop_latch_requires_visual_rearming_and_new_history(self):
        player = JengaPlayer()
        player.mark_released(1, observation().carried)
        for t in np.arange(1.1, 3, 0.05):
            self.assertFalse(player.update(observation(), t, t))
        self.assertIsNotNone(player.released_at)
        player.update(None, 3.1, 3.1)
        player.update(None, 3.3, 3.3)
        self.assertFalse(player.update(observation(1100), 3.4, 3.4))
        self.assertIsNone(player.released_at)
        self.assertEqual(len(player.history), 1)

    def test_short_occlusion_preserved_but_camera_jump_and_stall_reset(self):
        player = JengaPlayer()
        for t in np.arange(0, 0.3, 0.04):
            player.update(observation(800 + t * 300), t, t)
        count = len(player.history)
        player.update(None, 0.32, 0.32)
        self.assertEqual(len(player.history), count)
        player.update(observation(960, top=650), 0.4, 0.4)
        self.assertEqual(len(player.history), 1)
        player.reset_motion()
        self.assertEqual(player.history, [])
        self.assertIsNone(player.prediction)


class TestJengaTask(unittest.TestCase):
    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    def test_reentry_uses_current_scene_even_with_a_previous_high_tower(self, player_type):
        task = task_harness()
        task._can_input = MagicMock(return_value=True)
        task.get_game_hwnd = MagicMock(return_value=42)
        task.executor.method.frame_timestamp = None
        task.loop = MagicMock(side_effect=lambda **kwargs: iter([None]))
        player = player_type.return_value
        player.horizontal_geometry = None
        player.update.return_value = False
        high = cv2.imread(str(FIXTURES / "layer15_0.png"))
        first = task._jenga_detector.detect(high)
        task._jenga_detector.learn(high, first.carried)
        for name in ("start", "low", "high", "live_five", "start"):
            with self.subTest(scene=name):
                frame = cv2.imread(str(FIXTURES / f"{name}.png"))
                task.next_frame = MagicMock(return_value=frame)
                task.run()
                self.assertIsNotNone(player.update.call_args.args[0])
                self.assertEqual(task._jenga_detector.learned, [])
                self.assertTrue(task.enabled)
        task.executor.interaction.send_key_down.assert_not_called()

    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    def test_miss_and_unknown_result_can_drop_again_on_the_unchanged_receiver(self, player_type):
        for result in ("miss", None):
            with self.subTest(result=result):
                player_type.reset_mock()
                player = player_type.return_value
                player.horizontal_geometry = None
                player.release_delay = 0
                player.update.side_effect = lambda found, *args: found is not None
                task = task_harness()
                task._can_input = MagicMock(return_value=True)
                task.get_game_hwnd = MagicMock(return_value=42)
                task.executor.method.frame_timestamp = None
                task.loop = MagicMock(return_value=range(2))
                original = cv2.imread(str(FIXTURES / "start.png"))
                frames = [original.copy() for _ in range(3)]
                for index, frame in enumerate(frames):
                    frame[0, 0] = index
                task.next_frame = MagicMock(side_effect=frames)
                task._drop = MagicMock(return_value=True)
                task._wait_placement = MagicMock(return_value=result)
                task.run()
                self.assertEqual(task._drop.call_count, 2)
                self.assertEqual(task._wait_placement.call_count, 2)
                for call in player.update.call_args_list:
                    self.assertEqual(call.args[0].tower.kind, "monkey")
                self.assertTrue(task.enabled)

    def test_missing_capture_during_judgment_is_recoverable(self):
        task = task_harness()
        task._can_input = MagicMock(return_value=True)
        task.loop = MagicMock(return_value=[None])
        self.assertIsNone(task._wait_placement(42))
        self.assertTrue(task.enabled)

    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    def test_capture_exception_keeps_task_enabled_for_the_next_trigger(self, player_type):
        task = task_harness()
        task.next_frame = MagicMock(side_effect=CaptureException("capture disconnected"))
        task.run()
        self.assertTrue(task.enabled)
        task._can_input = MagicMock(return_value=True)
        task.get_game_hwnd = MagicMock(return_value=84)
        task.executor.method.frame_timestamp = None
        frame = cv2.imread(str(FIXTURES / "high.png"))
        task.next_frame = MagicMock(return_value=frame)
        task.loop = MagicMock(return_value=[None])
        player_type.return_value.horizontal_geometry = None
        player_type.return_value.update.return_value = False
        task.run()
        self.assertIsNotNone(player_type.return_value.update.call_args.args[0])
        task.executor.interaction.send_key_down.assert_not_called()

    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    def test_interruptions_during_capture_yield_and_reenter_with_a_new_window(self, player_type):
        for reason in ("complete", "pause", "capture_method", "resize", "missing", "focus", "disable_enable"):
            with self.subTest(reason=reason):
                player_type.reset_mock()
                task = task_harness()
                frame = cv2.imread(str(FIXTURES / "start.png"))
                task.get_game_hwnd = MagicMock(return_value=42)
                task._can_input = MagicMock(return_value=True)
                task.executor.method.frame_timestamp = None
                task.executor.pause_start = 0
                task.loop = MagicMock(side_effect=lambda **kwargs: iter(range(2)))
                player = player_type.return_value
                player.horizontal_geometry = None
                player.update.return_value = True
                player.release_delay = 0
                task._drop = MagicMock(return_value=True)
                task._wait_placement = MagicMock(return_value="good")

                def interrupted_frame(reason=reason, task=task, frame=frame):
                    if reason == "complete":
                        return cv2.imread(str(FIXTURES / "complete.png"))
                    if reason == "pause":
                        task.executor.pause_start = 1
                    elif reason == "capture_method":
                        task.executor.method = MagicMock(frame_timestamp=None)
                    elif reason == "resize":
                        return cv2.resize(frame, (1280, 720))
                    elif reason == "missing":
                        return None
                    elif reason == "focus":
                        task._can_input.return_value = False
                    elif reason == "disable_enable":
                        task.disable()
                        task.enable()
                    return frame

                task.next_frame = MagicMock()
                task.next_frame.side_effect = lambda task=task, frame=frame, interrupted_frame=interrupted_frame: (
                    frame if task.next_frame.call_count == 1 else interrupted_frame()
                )
                task.run()
                task._drop.assert_not_called()
                self.assertTrue(task.enabled)
                self.assertIsNone(task._jenga_detector._tower_last)
                task._can_input.return_value = True
                task.get_game_hwnd.return_value = 84
                changed = frame.copy()
                changed[0, 0] = 1
                task.next_frame = MagicMock(side_effect=[frame, changed])
                task.loop = MagicMock(return_value=[None])
                player.update.side_effect = lambda found, *args: found is not None
                task.run()
                task._drop.assert_called_once()
                self.assertEqual(task._drop.call_args.args[0], 84)

    @patch("src.tasks.trigger.auto_jenga_task.time.perf_counter")
    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    def test_unobserved_new_round_reacquires_after_old_height_blocks_detection(self, player_type, clock):
        task = task_harness()
        task._can_input = MagicMock(return_value=True)
        task.get_game_hwnd = MagicMock(return_value=42)
        task.loop = MagicMock(return_value=range(19))
        player_type.return_value.update.return_value = False
        player_type.return_value.horizontal_geometry = None
        high = cv2.imread(str(FIXTURES / "layer15_0.png"))
        start = cv2.imread(str(FIXTURES / "start.png"))
        elapsed = 0.0
        index = 0
        clock.side_effect = lambda: elapsed

        def capture():
            nonlocal elapsed, index
            elapsed += 0.3
            image = (high if index < 2 else start).copy()
            image[0, 0] = index
            index += 1
            task.executor.method.frame_timestamp = elapsed
            return image

        task.next_frame = MagicMock(side_effect=capture)
        task.run()
        observations = [call.args[0] for call in player_type.return_value.update.call_args_list]
        self.assertTrue(any(found is None for found in observations))
        self.assertIsNotNone(observations[-1])
        self.assertGreater(observations[-1].tower.top, 780)
        task.executor.interaction.send_key_down.assert_not_called()

    @patch("src.tasks.trigger.auto_jenga_task.time.sleep")
    @patch("src.tasks.trigger.auto_jenga_task.time.perf_counter")
    def test_real_predictor_resumes_multiple_drops_after_miss_and_unknown(self, clock, sleep):
        for fps, phase in ((10, 2.6), (30, 0.7)):
            with self.subTest(fps=fps, phase=phase):
                task = task_harness()
                task._can_input = MagicMock(return_value=True)
                task.get_game_hwnd = MagicMock(return_value=42)
                task._jenga_detector = MagicMock()
                task._jenga_detector.is_active.return_value = True
                task.loop = MagicMock(return_value=range(fps * 16))
                state = {"time": 0.0, "birth": -phase / 1.6, "frame": 0}
                releases = []
                clock.side_effect = lambda state=state: state["time"]

                def advance(duration, state=state):
                    state["time"] += duration

                sleep.side_effect = advance

                def capture(fps=fps, state=state, task=task, advance=advance):
                    advance(1 / fps)
                    state["frame"] += 1
                    task.executor.method.frame_timestamp = state["time"]
                    return np.full((20, 20, 3), state["frame"] % 256, np.uint8)

                def detect(frame, state=state):
                    t = state["time"] - state["birth"]
                    x = 960 + 285 * math.sin(1.6 * t)
                    bottom = 390 - 91.2 * math.sin(3.2 * t)
                    top = bottom - 0.754531 * 228
                    receiver = 960 + 35 * math.sin(math.pi / 2 * state["time"] + 0.7)
                    return JengaObservation(
                        JengaPiece(x, top, bottom, 112, kind="monkey"),
                        JengaPiece(receiver, 770, 770 + 0.755 * 228, 112, kind="monkey"),
                        JengaPiece(x, top - 70, top - 5, 35),
                    )

                def drop(*args, state=state, releases=releases, **kwargs):
                    releases.append(state["time"])
                    return True

                def judgment(hwnd, state=state, releases=releases, advance=advance):
                    result = "miss" if len(releases) % 2 else None
                    advance(0.9 if result else 2.5)
                    state["birth"] = state["time"] - 0.1
                    return result

                task.next_frame = MagicMock(side_effect=capture)
                task._jenga_detector.detect.side_effect = detect
                task._drop = MagicMock(side_effect=drop)
                task._wait_placement = MagicMock(side_effect=judgment)
                task.run()
                self.assertGreaterEqual(len(releases), 3)
                # Each failed/unread placement requires a fresh first-piece
                # fit, including the two-second observation gate.
                for earlier, later in pairwise(releases):
                    self.assertGreater(later - earlier, 2.8)
                self.assertTrue(task.enabled)

    @patch("src.tasks.trigger.auto_jenga_task.win32gui.GetForegroundWindow", return_value=42)
    def test_prepared_drop_is_cancelled_by_pause_or_rapid_disable_enable(self, foreground):
        for reason in ("pause", "disable_enable", "capture_method", "interaction"):
            with self.subTest(reason=reason):
                task = task_harness()
                task.executor.pause_start = 0
                context = task._capture_context()
                if reason == "pause":
                    task.executor.pause_start = 1
                elif reason == "disable_enable":
                    task.disable()
                    task.enable()
                elif reason == "capture_method":
                    task.executor.method = MagicMock()
                else:
                    task.executor.interaction = MagicMock()
                self.assertFalse(task._drop(42, capture_context=context))
                task.executor.interaction.send_key_down.assert_not_called()

    @patch("src.tasks.trigger.auto_jenga_task.win32gui.GetForegroundWindow", return_value=42)
    @patch("src.tasks.trigger.auto_jenga_task.time.sleep")
    def test_window_reconnection_releases_key_through_the_original_interaction(self, sleep, foreground):
        task = task_harness()
        original = task.executor.interaction
        replacement = MagicMock()
        sleep.side_effect = lambda duration: setattr(task.executor, "interaction", replacement)
        self.assertTrue(task._drop(42))
        original.send_key_down.assert_called_once_with("f", activate=False)
        original.send_key_up.assert_called_once_with("f")
        replacement.send_key_up.assert_not_called()
        self.assertIsNone(task._held_key)

    @patch("src.tasks.trigger.auto_jenga_task.time.perf_counter", return_value=10)
    def test_judgment_rejects_repeated_stale_or_invalid_capture_timestamps(self, clock):
        for timestamp in (10, 9, math.nan, math.inf):
            with self.subTest(timestamp=timestamp):
                task = task_harness()
                task._can_input = MagicMock(return_value=True)
                task._jenga_detector = MagicMock()
                task._jenga_detector.placement_result.return_value = "perfect"
                task.executor.method.frame_timestamp = timestamp
                task.loop = MagicMock(return_value=[np.full((20, 20, 3), i, np.uint8) for i in range(4)])
                self.assertIsNone(task._wait_placement(42))

    @patch("src.tasks.trigger.auto_jenga_task.time.perf_counter")
    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    def test_bad_timestamp_stream_yields_and_fresh_capture_reenters(self, player_type, clock):
        for timestamp in (0.05, -1, math.nan, math.inf):
            with self.subTest(timestamp=timestamp):
                task = task_harness()
                task._can_input = MagicMock(return_value=True)
                task.get_game_hwnd = MagicMock(return_value=42)
                task._jenga_detector = MagicMock()
                task._jenga_detector.is_active.return_value = True
                task._jenga_detector.detect.return_value = observation()
                task.loop = MagicMock(return_value=range(20))
                player_type.reset_mock()
                player = player_type.return_value
                player.horizontal_geometry = None
                player.update.return_value = False
                elapsed = [0.0]
                clock.side_effect = lambda elapsed=elapsed: elapsed[0]

                def capture(timestamp=timestamp, elapsed=elapsed, task=task):
                    elapsed[0] += 0.05
                    task.executor.method.frame_timestamp = timestamp
                    return np.full((20, 20, 3), round(elapsed[0] * 20), np.uint8)

                task.next_frame = MagicMock(side_effect=capture)
                task.run()
                self.assertLess(task.next_frame.call_count, 10)
                self.assertTrue(task.enabled)
                before = player.update.call_count

                def fresh_capture(elapsed=elapsed, task=task):
                    frame = capture()
                    task.executor.method.frame_timestamp = elapsed[0]
                    return frame

                task.next_frame = MagicMock(side_effect=fresh_capture)
                task.loop = MagicMock(return_value=[None])
                task.run()
                self.assertEqual(player.update.call_count, before + 1)
                task.executor.interaction.send_key_down.assert_not_called()


    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    @patch("src.tasks.trigger.auto_jenga_task.time.perf_counter")
    def test_round_runs_past_120_seconds_until_manual_disable(self, clock, player_type):
        clock.side_effect = (i * 0.001 for i in range(100))
        task = task_harness()
        task._can_input = MagicMock(return_value=True)
        task.get_game_hwnd = MagicMock(return_value=42)
        task.active_time = MagicMock(side_effect=[0, 0, 121, 242])
        task.executor.method.frame_timestamp = None
        task.next_frame = MagicMock(side_effect=[np.full((20, 20, 3), i, np.uint8) for i in range(3)])
        task._jenga_detector = MagicMock()
        task._jenga_detector.is_active.return_value = True
        player = player_type.return_value
        player.horizontal_geometry = None

        def observe(*args):
            if player.update.call_count == 2:
                task.disable()
            return False

        player.update.side_effect = observe
        task.run()
        self.assertEqual(player.update.call_count, 2)
        self.assertEqual(task.next_frame.call_count, 3)
        self.assertFalse(task.enabled)
        task.executor.interaction.send_key_down.assert_not_called()

    def test_placement_requires_two_changing_frames_and_preserves_miss(self):
        for result in ("perfect", "good", "miss", None):
            task = task_harness()
            task._can_input = MagicMock(return_value=True)
            task.loop = MagicMock(return_value=[np.full((20, 20, 3), i, np.uint8) for i in range(5)])
            task._jenga_detector = MagicMock()
            task._jenga_detector.placement_result.side_effect = [None, result, result, None, None]
            self.assertEqual(task._wait_placement(42), result)
        task.loop = MagicMock(return_value=[np.zeros((20, 20, 3), np.uint8)] * 5)
        task._jenga_detector.placement_result = MagicMock(return_value="perfect")
        self.assertIsNone(task._wait_placement(42))

    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    @patch("src.tasks.trigger.auto_jenga_task.time.perf_counter")
    def test_all_judgments_keep_placement_wait_and_only_success_confirms_new_top(self, clock, player_type):
        for result in ("perfect", "good", "miss", None):
            with self.subTest(result=result):
                clock.side_effect = (i * 0.001 for i in range(100))
                player_type.reset_mock()
                player = player_type.return_value
                player.release_delay = 0
                player.horizontal_geometry = None
                player.update.return_value = True
                task = task_harness()
                task._can_input = MagicMock(return_value=True)
                task.get_game_hwnd = MagicMock(return_value=42)
                task.executor.method.frame_timestamp = None
                task.loop = MagicMock(return_value=range(2))
                task.next_frame = MagicMock(side_effect=[np.full((20, 20, 3), i, np.uint8) for i in range(3)])
                task._jenga_detector = MagicMock()
                task._jenga_detector.is_active.return_value = True
                task._jenga_detector.detect.return_value = observation()
                task._drop = MagicMock(return_value=True)
                task._wait_placement = MagicMock(return_value=result)
                task.run()
                self.assertEqual(task._drop.call_count, 2)
                self.assertEqual(task._wait_placement.call_count, 2)
                self.assertEqual(player.confirm_placement.call_count, 2 * int(result in ("perfect", "good")))
                self.assertEqual(
                    [call.kwargs["placement_confirmed"] for call in task._jenga_detector.learn.call_args_list],
                    [result in ("perfect", "good")] * 2,
                )
                self.assertTrue(task.enabled)

    @patch("src.tasks.trigger.auto_jenga_task.JengaPlayer")
    @patch("src.tasks.trigger.auto_jenga_task.time.perf_counter")
    def test_renderer_stall_yields_then_next_trigger_reacquires(self, clock, player_type):
        clock.side_effect = (i * 0.1 for i in range(200))
        task = task_harness()
        task._can_input = MagicMock(return_value=True)
        task.get_game_hwnd = MagicMock(return_value=42)
        task._jenga_detector = MagicMock()
        task._jenga_detector.is_active.return_value = True
        first = np.zeros((1080, 1920, 3), np.uint8)
        changed = first.copy()
        changed[0, 0] = 255
        task.next_frame = MagicMock(side_effect=[first] * 5 + [changed])
        task.loop = MagicMock(return_value=range(5))
        player = player_type.return_value
        player.update.return_value = False
        task.run()
        self.assertEqual(player.update.call_count, 1)
        self.assertTrue(task.enabled)
        task.next_frame = MagicMock(return_value=changed)
        task.loop = MagicMock(return_value=range(1))
        task.run()
        self.assertEqual(player.update.call_count, 2)
        self.assertEqual(player_type.call_count, 2)
        task.executor.interaction.send_key_down.assert_not_called()

    def test_registered(self):
        self.assertIn(["src.tasks.trigger.auto_jenga_task", "AutoJengaTask"], config["trigger_tasks"])

    @patch("src.tasks.trigger.auto_jenga_task.win32gui.GetForegroundWindow", return_value=42)
    @patch("src.tasks.trigger.auto_jenga_task.time.sleep")
    def test_input_uses_interact_key_and_always_releases(self, sleep, foreground):
        task = task_harness()
        self.assertTrue(task._drop(42))
        task.executor.interaction.send_key_down.assert_called_once_with("f", activate=False)
        task.executor.interaction.send_key_up.assert_called_once_with("f")
        sleep.side_effect = RuntimeError("interrupted")
        with self.assertRaises(RuntimeError):
            task._drop(42)
        self.assertIsNone(task._held_key)
        self.assertEqual(task.executor.interaction.send_key_up.call_count, 2)

    @patch("src.tasks.trigger.auto_jenga_task.win32gui.GetForegroundWindow", return_value=42)
    @patch("src.tasks.trigger.auto_jenga_task.time.sleep")
    def test_remapped_interact_key_on_trigger_task(self, sleep, foreground):
        task = task_harness()
        with patch.object(task.key_manager, "resolve_key", return_value="g") as resolve:
            self.assertTrue(task._drop(42))
        resolve.assert_called_once_with("f", "common")
        task.executor.interaction.send_key_down.assert_called_once_with("g", activate=False)
        task.executor.interaction.send_key_up.assert_called_once_with("g")

    @patch("src.tasks.trigger.auto_jenga_task.win32gui.GetForegroundWindow", return_value=42)
    @patch("src.tasks.trigger.auto_jenga_task.time.perf_counter", return_value=10.02)
    def test_delayed_dispatch_never_sends_expired_prediction(self, clock, foreground):
        task = task_harness()
        self.assertFalse(task._drop(42, deadline=10.015))
        task.executor.interaction.send_key_down.assert_not_called()
        task.executor.interaction.send_key_up.assert_not_called()
        self.assertIsNone(task._held_key)

    @patch("src.tasks.trigger.auto_jenga_task.win32gui.GetForegroundWindow", return_value=42)
    def test_pause_disable_focus_exit_never_press(self, foreground):
        for reason in ("pause", "disable", "focus", "exit", "destroy"):
            task = task_harness()
            if reason == "pause":
                task.executor.paused = True
            elif reason == "disable":
                task._enabled = False
            elif reason == "exit":
                task.executor.exit_event.set()
            elif reason == "destroy":
                task.on_destroy()
            self.assertFalse(task._drop(43 if reason == "focus" else 42))
            task.executor.interaction.send_key_down.assert_not_called()

    def test_outside_game_has_no_input(self):
        task = task_harness()
        task.next_frame = MagicMock(return_value=np.zeros((1080, 1920, 3), np.uint8))
        task.run()
        task.executor.interaction.send_key_down.assert_not_called()


if __name__ == "__main__":
    unittest.main()
