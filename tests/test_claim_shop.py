import itertools
import unittest
from unittest.mock import Mock

import cv2
import numpy as np
from ok.feature.Box import relative_box
from ok.feature.FeatureSet import FeatureSet

from src.config import config
from src.data.feature_list import FeatureList
from src.tasks.onetime.claim_daily_task import ClaimDailyTask

SHOP = {FeatureList.shop_check, FeatureList.shop_click_1_check}
CARD = (981, 366, 1182, 410)
CONFIRM = (1004, 652, 1385, 704)
YELLOW = (100, 211, 244)
BROWN = (28, 53, 77)


def draw_button(frame, rect, face, ink, glyphs):
    left, top, right, bottom = rect
    cv2.rectangle(frame, (left, top), (right, bottom), YELLOW, -1)
    cv2.rectangle(frame, (left + 2, top + 2), (right - 2, bottom - 2), face, -1)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    for i in range(glyphs):
        x = cx - (19 * glyphs - 4) // 2 + 19 * i
        cv2.rectangle(frame, (x, cy - 8), (x + 14, cy + 8), ink, 2)
        cv2.line(frame, (x, cy), (x + 14, cy), ink, 2)


class TestClaimShop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tm = config["template_matching"]
        cls.templates = FeatureSet(False, tm["coco_feature_json"], tm["default_horizontal_variance"],
                                   tm["default_vertical_variance"], tm["default_threshold"])

    def setUp(self):
        self.empty = np.full((1080, 1920, 3), 240, np.uint8)
        self.shop = self.listing(icon_x=1387, marker_x=1459)

    def listing(self, icon_x=None, marker_x=None):
        frame = self.empty.copy()
        for feature, x, y in ((FeatureList.shop_limited_tab_icon, icon_x, 131),
                              (FeatureList.shop_click_2_check, marker_x, 166)):
            if x is not None:
                patch = self.templates.get_feature_by_name(frame, feature).mat
                height, width = patch.shape[:2]
                frame[y:y + height, x:x + width] = patch[:, :, :3]
        return frame

    def replay(self, scenes, click_results=None):
        task = object.__new__(ClaimDailyTask)
        task.ui_ensure = Mock(return_value=True)
        task.box_of_screen = lambda *coords: relative_box(1920, 1080, *coords)
        task.active_time = itertools.count(step=0.25).__next__
        task.sleep = Mock()
        task.click = Mock(return_value=True, side_effect=click_results)
        task.scroll = Mock()
        frames = itertools.chain(scenes, itertools.repeat(scenes[-1]))
        features = set()

        def next_frame():
            nonlocal features
            features, frame = next(frames)
            return frame

        def find_one(feature, **kwargs):
            if feature in (FeatureList.shop_limited_tab_icon, FeatureList.shop_click_2_check):
                hits = self.templates.find_one_feature(kwargs.pop("frame"), feature, limit=1, **kwargs)
                return hits[0] if hits else None
            return True if feature in features else None

        def close_detail(_key):
            nonlocal frames
            frames = itertools.repeat((SHOP, self.shop))

        task.next_frame = next_frame
        task.find_one = find_one
        task.send_key = Mock(side_effect=close_detail)
        task.claim_shop()
        return task

    def test_free_card_then_free_detail_clicked_once_each(self):
        for glyphs in (4, 6):
            with self.subTest(glyphs=glyphs):
                listing, detail = self.shop.copy(), self.empty.copy()
                draw_button(listing, CARD, YELLOW, BROWN, glyphs)
                draw_button(listing, CONFIRM, YELLOW, BROWN, glyphs)
                draw_button(detail, CONFIRM, YELLOW, BROWN, glyphs)
                task = self.replay([
                    (SHOP, listing), (SHOP, listing), (SHOP, listing),
                    (set(), detail), (set(), detail),
                    ({FeatureList.claim_popup}, self.empty), (SHOP, self.shop),
                ])
                self.assertEqual(task.click.call_count, 3)
                card, confirm, reward = (call.args[0] for call in task.click.call_args_list)
                for button, (left, top, right, bottom) in ((card, CARD), (confirm, CONFIRM)):
                    x, y = button.center()
                    self.assertTrue(left < x < right and top < y < bottom)
                self.assertEqual(reward.center(), task.safe_box.center())
                task.send_key.assert_not_called()

    def test_paid_locked_and_unlabelled_cards_skip_the_confirmation(self):
        for face, ink, glyphs in (
            ((49, 47, 47), (212, 243, 255), 4),  # 黑底金价、金边
            ((49, 49, 49), (100, 119, 130), 4),  # 灰色锁定
            ((49, 47, 47), BROWN, 6),             # 字色和字形都符合，底色不符
            (YELLOW, BROWN, 0),
        ):
            with self.subTest(face=face, ink=ink):
                listing = self.shop.copy()
                draw_button(listing, CARD, face, ink, glyphs)
                draw_button(listing, CONFIRM, YELLOW, BROWN, 6)
                task = self.replay([(SHOP, listing)])
                task.click.assert_not_called()
                task.send_key.assert_not_called()

    def test_black_detail_button_is_never_confirmed(self):
        listing, detail = self.shop.copy(), self.empty.copy()
        draw_button(listing, CARD, YELLOW, BROWN, 6)
        draw_button(detail, CONFIRM, (49, 47, 47), BROWN, 6)
        task = self.replay([(SHOP, listing), (SHOP, listing), (set(), detail)])
        task.click.assert_called_once()
        task.send_key.assert_called_once_with("esc")

    def test_rejected_card_click_cannot_confirm_an_unrelated_popup(self):
        listing, detail = self.shop.copy(), self.empty.copy()
        draw_button(listing, CARD, YELLOW, BROWN, 4)
        draw_button(detail, CONFIRM, YELLOW, BROWN, 4)
        task = self.replay([(SHOP, listing), (SHOP, listing), (set(), detail)], click_results=[False])
        task.click.assert_called_once()
        task.send_key.assert_not_called()

    def test_clicks_the_detected_tab_across_the_bar(self):
        for icon_x in (645, 1070, 1800):
            with self.subTest(icon_x=icon_x):
                other = self.listing(icon_x=icon_x, marker_x=1459)
                draw_button(other, CARD, YELLOW, BROWN, 4)
                selected = self.listing(icon_x=icon_x, marker_x=icon_x + 56)
                task = self.replay([(SHOP, other), (SHOP, selected)])
                task.click.assert_called_once()
                self.assertEqual(task.click.call_args.args[0].x, icon_x)
                task.scroll.assert_not_called()

    def test_finds_hidden_tab_after_reversing_at_the_edge(self):
        hidden = self.listing(marker_x=1459)
        other = self.listing(icon_x=860, marker_x=1459)
        selected = self.listing(icon_x=908, marker_x=980)  # 点击后标签栏仍可移动
        task = self.replay([(SHOP, hidden)] * 3 + [(SHOP, other), (SHOP, selected)])
        scrolls = [call.args for call in task.scroll.call_args_list]
        self.assertEqual(len(scrolls), 2)
        self.assertTrue(scrolls[0][2] * scrolls[1][2] < 0)
        self.assertTrue(all(640 < x < 1910 and 125 < y < 175 for x, y, _ in scrolls))
        task.click.assert_called_once()
        self.assertEqual(task.click.call_args.args[0].x, 860)

    def test_unavailable_tab_or_lost_selection_never_clicks_a_product(self):
        hidden = self.listing(marker_x=1459)
        other = self.listing(icon_x=830, marker_x=1459)
        for frame in (hidden, other):
            draw_button(frame, CARD, YELLOW, BROWN, 4)
        missing = self.replay([(SHOP, hidden)])
        missing.click.assert_not_called()
        self.assertEqual(missing.scroll.call_count, 2)
        changed = self.replay([(SHOP, self.shop), (SHOP, other)])
        changed.click.assert_not_called()
