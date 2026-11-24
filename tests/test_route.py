"""导航路线测试：模型校验、存取、执行顺序、导航/模板/点击、断点续跑。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.core.flow.executor import CheckpointStore
from src.core.route import (
    ROUTE_FOLDER,
    Route,
    RouteContext,
    RouteFormatError,
    RouteRunner,
    Waypoint,
    list_routes,
    load_route,
    route_fingerprint,
    route_path,
    save_route,
)


class FakeRouteTask:
    """记录导航/识别/点击，并自己推进时钟。"""

    width = 1920
    height = 1080

    def __init__(self, reachable=(), visible=()):
        self.now = 0.0
        self.reachable = set(reachable)
        self.visible = set(visible)
        self.calls: list[tuple] = []

    def active_time(self):
        return self.now

    def sleep(self, seconds):
        self.calls.append(("sleep", round(float(seconds), 3)))
        self.now += float(seconds)

    def ui_ensure(self, page, time_out=30.0):
        self.calls.append(("goto", page, round(float(time_out), 3)))
        return page in self.reachable

    def ui_page_appear(self, page):
        self.calls.append(("appear", page))
        return page in self.reachable

    def find_one(self, feature=None, **kwargs):
        name = getattr(feature, "value", feature)
        self.calls.append(("find", name))
        return object() if name in self.visible else None

    def click_relative(self, x, y):
        self.calls.append(("click", round(float(x), 3), round(float(y), 3)))

    def kinds(self):
        return [call[0] for call in self.calls]

    def named(self, kind):
        return [call for call in self.calls if call[0] == kind]


def route_dict(**overrides):
    data = {
        "name": "测试路线",
        "description": "说明",
        "waypoints": [
            {"page": "page_main"},
            {"template": "commission_icon", "timeout": 2},
            {"click": [0.5, 0.4], "wait": 1.0},
        ],
    }
    data.update(overrides)
    return data


class TestRouteModel(unittest.TestCase):
    def test_parse(self):
        route = Route.from_dict(route_dict())
        self.assertEqual(route.name, "测试路线")
        self.assertEqual(len(route.waypoints), 3)
        self.assertEqual(route.waypoints[1].template, "commission_icon")
        self.assertEqual(route.waypoints[2].click, (0.5, 0.4))

    def test_missing_waypoints(self):
        with self.assertRaises(RouteFormatError):
            Route.from_dict({"name": "x"})

    def test_unknown_field(self):
        with self.assertRaises(RouteFormatError):
            Route.from_dict({"waypoints": [{"jump": 1}]})

    def test_bad_click(self):
        with self.assertRaises(RouteFormatError):
            Route.from_dict({"waypoints": [{"click": [0.5]}]})

    def test_roundtrip(self):
        route = Route.from_dict(route_dict())
        again = Route.from_json(route.to_json())
        self.assertEqual(again.to_dict(), route.to_dict())

    def test_validate_ok(self):
        self.assertEqual(Route.from_dict(route_dict()).validate(), [])

    def test_validate_empty(self):
        self.assertTrue(Route.from_dict({"waypoints": []}).validate())

    def test_validate_empty_waypoint(self):
        issues = Route.from_dict({"waypoints": [{}]}).validate()
        self.assertTrue(any("空航点" in issue for issue in issues))

    def test_validate_ratio(self):
        issues = Route.from_dict({"waypoints": [{"click": [1.5, 0.2]}]}).validate()
        self.assertTrue(any("0..1" in issue for issue in issues))

    def test_validate_known_pages(self):
        route = Route.from_dict({"waypoints": [{"page": "page_ghost"}]})
        self.assertTrue(route.validate(known_pages={"page_main"}))
        self.assertEqual(route.validate(known_pages={"page_ghost"}), [])

    def test_labels(self):
        labels = Route.from_dict(route_dict()).labels()
        self.assertEqual(len(labels), 3)
        self.assertIn("页面 page_main", labels[0])


class TestRouteStorage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "routes"

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_load_list(self):
        route = Route.from_dict(route_dict())
        path = save_route(route, self.folder)
        self.assertTrue(path.exists())
        loaded = load_route("测试路线", self.folder)
        self.assertEqual(loaded.to_dict(), route.to_dict())
        self.assertEqual(list_routes(self.folder), ["测试路线"])

    def test_load_missing(self):
        with self.assertRaises(RouteFormatError):
            load_route("nope", self.folder)

    def test_unsafe_name(self):
        path = route_path("../evil", self.folder)
        self.assertEqual(path.parent, self.folder)

    def test_empty_name(self):
        with self.assertRaises(RouteFormatError):
            route_path("   ", self.folder)

    def test_default_folder_constant(self):
        self.assertEqual(ROUTE_FOLDER.as_posix(), "configs/routes")


class TestRouteRunner(unittest.TestCase):
    def _runner(self, task, route, checkpoint=None):
        context = RouteContext(task, poll_interval=0.5)
        return RouteRunner(route, context, checkpoint=checkpoint, log=lambda message: None)

    def test_full_route(self):
        task = FakeRouteTask(reachable={"page_main"}, visible={"commission_icon"})
        result = self._runner(task, Route.from_dict(route_dict())).run()
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.completed, 3)
        self.assertEqual(task.named("goto")[0][1], "page_main")
        self.assertEqual(task.named("click"), [("click", 0.5, 0.4)])
        self.assertIn(("sleep", 1.0), task.calls)
        self.assertIn("路线完成", result.summary())

    def test_unreachable_page_fails(self):
        task = FakeRouteTask(reachable=set())
        result = self._runner(task, Route.from_dict(route_dict())).run()
        self.assertFalse(result.ok)
        self.assertIn("无法到达界面", result.error)
        self.assertIn("page_main", result.failed_at)

    def test_missing_template_fails(self):
        task = FakeRouteTask(reachable={"page_main"}, visible=set())
        result = self._runner(task, Route.from_dict(route_dict())).run()
        self.assertFalse(result.ok)
        self.assertIn("没有出现", result.error)
        self.assertEqual(result.completed, 1)

    def test_invalid_route_raises(self):
        task = FakeRouteTask()
        with self.assertRaises(RouteFormatError):
            self._runner(task, Route.from_dict({"waypoints": []})).run()
        self.assertEqual(task.calls, [])

    def test_checkpoint_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(Path(tmp))
            route = Route.from_dict(route_dict())
            checkpoint = store.for_key(route.name)

            first_task = FakeRouteTask(reachable={"page_main"}, visible=set())
            first = self._runner(first_task, route, checkpoint=checkpoint).run()
            self.assertFalse(first.ok)
            self.assertEqual(checkpoint.load(), 1)

            second_task = FakeRouteTask(reachable={"page_main"}, visible={"commission_icon"})
            second = self._runner(second_task, route, checkpoint=checkpoint).run(resume=True)
            self.assertTrue(second.ok, second.summary())
            self.assertEqual(second.resumed_from, 1)
            # 第 1 个航点不再重复导航
            self.assertEqual(second_task.named("goto"), [])

    def test_success_clears_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(Path(tmp))
            route = Route.from_dict(route_dict())
            checkpoint = store.for_key(route.name)
            task = FakeRouteTask(reachable={"page_main"}, visible={"commission_icon"})
            self.assertTrue(self._runner(task, route, checkpoint=checkpoint).run().ok)
            self.assertEqual(checkpoint.load(), 0)

    def test_edited_route_ignores_stale_checkpoint(self):
        """改了路线，旧断点不该再跳过航点（与流程同一套 fingerprint 判断）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = CheckpointStore(Path(tmp))
            route = Route.from_dict(route_dict())
            checkpoint = store.for_key(route.name, fingerprint=route_fingerprint(route))
            first_task = FakeRouteTask(reachable={"page_main"}, visible=set())
            self._runner(first_task, route, checkpoint=checkpoint).run()
            self.assertEqual(checkpoint.load(), 1)

            edited = Route(
                name=route.name,
                waypoints=(Waypoint(wait=0.1), *route.waypoints),
            )
            fresh = store.for_key(route.name, fingerprint=route_fingerprint(edited))
            self.assertEqual(fresh.load(), 0, "路线改过就应忽略旧断点")

    def test_route_file_roundtrip_through_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            route = Route.from_dict(route_dict())
            save_route(route, folder)
            loaded = load_route("测试路线", folder)
            self.assertEqual(json.loads(loaded.to_json())["name"], "测试路线")


if __name__ == "__main__":
    unittest.main()
