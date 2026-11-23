import time
import unittest
from unittest.mock import MagicMock

from ok import WaitFailedException

from src.core.base_game_task import BaseGameTask
from src.core.base_mixin.ui_mixin import UIMixin
from src.data.page import Page


class MockTask(UIMixin):
    """Mock task for testing UI mixin."""

    def __init__(self):
        self.executor = MagicMock()
        self.executor.exit_event = MagicMock()
        self.executor.exit_event.is_set.return_value = False
        self.disabled = False
        self.once_sleep_time = 0.0
        self.clicks = []
        self.current_features = set()

    def active_time(self):
        """模拟 BaseGameTask 的暂停感知时钟（ui_goto 超时预算使用）。"""
        return time.monotonic()

    def is_task_disabled(self):
        return self.disabled

    def next_frame(self):
        pass

    def sleep(self, seconds):
        pass

    def find_one(self, feature):
        if feature in self.current_features:
            return {"box": [10, 10, 50, 50]}
        return None

    def click(self, button):
        self.clicks.append(button)

    loop = BaseGameTask.loop


class TestPage(unittest.TestCase):

    def setUp(self):
        Page.clear_all_pages()

    def tearDown(self):
        Page.clear_all_pages()

    def test_page_explicit_name(self):
        p = Page(check_feature="feat_main", name="page_main")
        self.assertEqual(p.name, "page_main")
        self.assertEqual(p.check_feature, "feat_main")
        self.assertIn("page_main", Page.all_pages)
        self.assertIs(Page.all_pages["page_main"], p)

    def test_page_caller_name_inference(self):
        page_inferred = Page(check_feature="feat_inferred")
        self.assertEqual(page_inferred.name, "page_inferred")
        self.assertIn("page_inferred", Page.all_pages)

    def test_page_equality_and_safety(self):
        p1 = Page(check_feature="feat1", name="page_a")
        p2 = Page(check_feature="feat2", name="page_a")
        p3 = Page(check_feature="feat1", name="page_b")

        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)
        # Type safety checks
        self.assertFalse(p1 == None)  # noqa: E711
        self.assertFalse(p1 == "page_a")
        self.assertFalse(p1 == 123)
        self.assertEqual(hash(p1), hash(p2))

    def test_page_get_lookup(self):
        p = Page("feat_home", name="home")
        self.assertIs(Page.get("home"), p)
        self.assertIs(Page.get(p), p)

        with self.assertRaises(KeyError):
            Page.get("nonexistent")

    def test_iter_pages_and_features(self):
        p1 = Page("feat_1", name="p1")
        p2 = Page("feat_2", name="p2")
        p3 = Page(None, name="p3")

        pages = list(Page.iter_pages())
        self.assertIn(p1, pages)
        self.assertIn(p2, pages)
        self.assertIn(p3, pages)

        features = list(Page.iter_check_features())
        self.assertIn("feat_1", features)
        self.assertIn("feat_2", features)
        self.assertNotIn(None, features)

    def test_link_records_and_build_with_forward_reference(self):
        # 验证 destination 尚未被实例化/注册时的延迟建图能力
        p_a = Page("feat_a", name="page_a")
        # 引用此时尚未实例化的 page_b
        p_a.link("btn_to_b", "page_b", back_button="btn_back_to_a")

        # build 前，links 应为空，仅记录在 _pending_links 中
        self.assertEqual(p_a.links, {})
        self.assertEqual(len(p_a._pending_links), 1)

        # 随后实例化 page_b
        p_b = Page("feat_b", name="page_b")

        # 此时执行 build()
        Page.build()

        # 验证边已正确双向解析
        self.assertEqual(p_a.links[p_b], "btn_to_b")
        self.assertEqual(p_b.links[p_a], "btn_back_to_a")

    def test_build_missing_destination_error(self):
        p_a = Page("feat_a", name="page_a")
        p_a.link("btn_to_missing", "unregistered_dest")

        with self.assertRaises(KeyError) as ctx:
            Page.build()
        self.assertIn("unregistered_dest", str(ctx.exception))

    def test_fluent_link_chaining(self):
        p_main = Page("feat_main", name="main")
        p_bag = Page("feat_bag", name="bag")
        p_map = Page("feat_map", name="map")

        # 链式调用
        p_main.link("btn_bag", p_bag, back_button="btn_close_bag") \
              .link("btn_map", "map", back_button="btn_close_map")

        Page.build()

        self.assertEqual(p_main.links[p_bag], "btn_bag")
        self.assertEqual(p_bag.links[p_main], "btn_close_bag")
        self.assertEqual(p_main.links[p_map], "btn_map")
        self.assertEqual(p_map.links[p_main], "btn_close_map")

    def test_dump_and_validate_graph(self):
        p_a = Page("feat_a", name="a")
        p_b = Page("feat_b", name="b")
        Page(None, name="c")  # missing check_feature and no links

        p_a.link("btn_a_b", p_b)
        Page.build()

        graph = Page.dump_graph()
        self.assertEqual(graph["a"], {"b": "btn_a_b"})
        self.assertEqual(graph["b"], {})
        self.assertEqual(graph["c"], {})

        issues = Page.validate_graph()
        self.assertTrue(any("Page 'c' has no check_feature" in issue for issue in issues))
        self.assertTrue(any("Page 'b' has no outgoing links" in issue for issue in issues))
        self.assertTrue(any("Page 'c' has no outgoing links" in issue for issue in issues))

    def test_bfs_shortest_path(self):
        # A -> B -> Target (length 2)
        # A -> C -> D -> Target (length 3)
        p_target = Page("feat_target", name="target")
        p_a = Page("feat_a", name="a")
        p_b = Page("feat_b", name="b")
        p_c = Page("feat_c", name="c")
        p_d = Page("feat_d", name="d")
        p_unreachable = Page("feat_u", name="u")

        p_a.link("btn_a_b", p_b)
        p_b.link("btn_b_target", p_target)

        p_a.link("btn_a_c", p_c)
        p_c.link("btn_c_d", p_d)
        p_d.link("btn_d_target", p_target)

        Page.build()
        Page.init_connection("target")

        # BFS should select the shortest path A -> B -> target
        self.assertEqual(p_a.parent, p_b)
        self.assertEqual(p_b.parent, p_target)
        self.assertIsNone(p_target.parent)
        self.assertIsNone(p_unreachable.parent)

        Page.clear_connection()
        self.assertIsNone(p_a.parent)
        self.assertIsNone(p_b.parent)


class TestUIMixin(unittest.TestCase):

    def setUp(self):
        Page.clear_all_pages()
        self.task = MockTask()

    def tearDown(self):
        Page.clear_all_pages()

    def test_ui_page_appear(self):
        p = Page("feat_home", name="home")
        self.assertFalse(self.task.ui_page_appear(p))
        self.assertFalse(self.task.ui_page_appear("home"))

        self.task.current_features.add("feat_home")
        self.assertTrue(self.task.ui_page_appear(p))
        self.assertTrue(self.task.ui_page_appear("home"))

        Page(None, name="none_feature")
        self.assertFalse(self.task.ui_page_appear("none_feature"))

    def test_ui_goto_already_arrived(self):
        Page("feat_dest", name="dest")
        self.task.current_features.add("feat_dest")

        result = self.task.ui_goto("dest")
        self.assertTrue(result)
        self.assertEqual(self.task.clicks, [])

    def test_ui_goto_multi_hop_navigation(self):
        p_a = Page("feat_a", name="page_a")
        p_b = Page("feat_b", name="page_b")
        p_dest = Page("feat_dest", name="page_dest")

        p_a.link("click_btn_b", p_b)
        p_b.link("click_btn_dest", p_dest)
        Page.build()

        self.task.current_features = {"feat_a"}

        def mock_click(button):
            self.task.clicks.append(button)
            if button == "click_btn_b":
                self.task.current_features = {"feat_b"}
            elif button == "click_btn_dest":
                self.task.current_features = {"feat_dest"}

        self.task.click = mock_click

        success = self.task.ui_goto("page_dest", time_out=5.0)
        self.assertTrue(success)
        self.assertEqual(self.task.clicks, ["click_btn_b", "click_btn_dest"])
        # Ensure parent connections were cleaned up
        self.assertIsNone(p_a.parent)
        self.assertIsNone(p_b.parent)

    def test_ui_goto_callable_action(self):
        p_start = Page("feat_start", name="start")
        p_dest = Page("feat_dest", name="dest")

        callback_called = []
        received_tasks = []

        def custom_action(task):
            callback_called.append(True)
            received_tasks.append(task)
            self.task.current_features = {"feat_dest"}

        p_start.link(custom_action, p_dest)
        Page.build()
        self.task.current_features = {"feat_start"}

        success = self.task.ui_goto("dest", time_out=5.0)
        self.assertTrue(success)
        self.assertEqual(len(callback_called), 1)
        self.assertIs(received_tasks[0], self.task)

    def test_ui_goto_unknown_destination(self):
        with self.assertRaises(KeyError):
            self.task.ui_goto("unregistered")

    def test_ui_goto_timeout(self):
        p_start = Page("feat_start", name="start")
        p_dest = Page("feat_dest", name="dest")
        p_start.link("btn_go", p_dest)
        Page.build()

        # Stay on start without reaching destination
        self.task.current_features = {"feat_start"}

        with self.assertRaises(WaitFailedException):
            self.task.ui_goto("dest", time_out=0.05, interval=0.0)

        # Ensure cleanup occurred despite timeout exception
        self.assertIsNone(p_start.parent)

    def test_ui_goto_aborted_by_exit_event(self):
        p_start = Page("feat_start", name="start")
        p_dest = Page("feat_dest", name="dest")
        p_start.link("btn_go", p_dest)
        Page.build()

        self.task.current_features = {"feat_start"}
        self.task.executor.exit_event.is_set.return_value = True

        result = self.task.ui_goto("dest", time_out=5.0)
        self.assertFalse(result)

    def test_ui_ensure(self):
        Page("feat_dest", name="dest")

        # Already at destination
        self.task.current_features = {"feat_dest"}
        self.assertTrue(self.task.ui_ensure("dest"))
        self.assertEqual(self.task.clicks, [])

        # Not at destination -> should navigate
        p_start = Page("feat_start", name="start")
        p_start.link("go_btn", "dest")
        Page.build()
        self.task.current_features = {"feat_start"}

        def mock_click(button):
            self.task.clicks.append(button)
            self.task.current_features = {"feat_dest"}

        self.task.click = mock_click
        self.assertTrue(self.task.ui_ensure("dest"))
        self.assertEqual(self.task.clicks, ["go_btn"])


if __name__ == "__main__":
    unittest.main()
