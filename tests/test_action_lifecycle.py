"""Action 生命周期与识别层测试。

识别层适配器不依赖真实截图：用假任务对象提供 ``find_feature`` / ``find_one`` /
``yolo_detect`` / ``ocr`` / ``find_button``，验证参数是否正确转发、返回值是否
归一成 ``Hit``。

生命周期用假时钟 + 假 frame 驱动，覆盖三阶段的各条分支：
前置条件超时、settle、A≠C、动作重试、持续阶段的 B 未命中即停 / C 命中即成功。
"""

import unittest

from ok import Box

from src.core.detector import (
    SOURCE_BUTTON,
    SOURCE_OCR,
    SOURCE_TEMPLATE,
    SOURCE_YOLO,
    BlindPointDetector,
    ButtonDetectorAdapter,
    Hit,
    InvertedDetector,
    MultiBoxDetector,
    OcrDetector,
    PredicateDetector,
    TemplateDetector,
    YoloDetector,
)
from src.core.base_game_task import BaseGameTask
from src.core.base_mixin.framework_override_mixin import FrameworkOverrideMixin
from src.core.base_mixin.runtime_mixin import RuntimeMixin


class FakeTask(RuntimeMixin):
    """假任务：记录调用参数，返回预设结果。

    继承 RuntimeMixin 以复用真实的 ``_resolve_detector`` / ``wait_expectation`` /
    ``wait_action_result`` 实现——只替换框架交互（截图 / 识别 / 计时）。
    """

    def __init__(self):
        self.now = 0.0
        self.frames = []            # 依次返回的帧；耗尽后重复最后一帧
        self.frame_index = 0
        self.calls = []             # 记录 (方法名, 参数)
        self.logs = []

        self.find_feature_result = None
        self.find_one_result = None
        self.yolo_result = []
        self.ocr_result = []
        self.find_button_result = None

    # ── 框架交互 stub ──

    loop = BaseGameTask.loop
    detect_with_scroll = BaseGameTask.detect_with_scroll
    _detect_with_scroll = BaseGameTask._detect_with_scroll

    def scroll(self, x, y, count):
        self.calls.append(("scroll", (x, y, count)))

    def next_frame(self):
        if not self.frames:
            return None
        index = min(self.frame_index, len(self.frames) - 1)
        frame = self.frames[index]
        self.frame_index += 1
        return frame

    def active_time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def wait_until(self, condition, time_out=0, settle_time=-1, pre_action=None,
                   post_action=None, raise_if_not_found=False):
        """简化 settle：条件成立后需连续两次成立（对应 settle_time > 0）。"""
        result = condition()
        if not result:
            return None
        if settle_time and settle_time > 0:
            self.now += settle_time
            if not condition():
                return None
        return result

    def log_info(self, message, notify=False):
        self.logs.append(str(message))

    def log_warning(self, message, notify=False):
        self.logs.append(str(message))

    def draw_boxes(self, *args, **kwargs):
        pass

    def width(self):
        return 1920

    # ── 识别 stub ──

    def find_feature(self, **kwargs):
        self.calls.append(("find_feature", kwargs))
        return self.find_feature_result

    def find_one(self, **kwargs):
        self.calls.append(("find_one", kwargs))
        return self.find_one_result

    def yolo_detect(self, **kwargs):
        self.calls.append(("yolo_detect", kwargs))
        return list(self.yolo_result)

    def ocr(self, **kwargs):
        self.calls.append(("ocr", kwargs))
        return list(self.ocr_result)

    def find_button(self, box, **kwargs):
        self.calls.append(("find_button", {"box": box, **kwargs}))
        return self.find_button_result


def box(x, y, w=10, h=10, conf=1.0, name=None):
    return Box(x, y, w, h, confidence=conf, name=name)


# ── 识别层 ──────────────────────────────────────────────────

class TestHit(unittest.TestCase):
    def test_truthy_and_fields(self):
        hit = Hit(box=box(1, 2), source=SOURCE_TEMPLATE)
        self.assertTrue(hit)
        self.assertEqual(hit.source, SOURCE_TEMPLATE)
        self.assertEqual(hit.confidence, 1.0)
        self.assertEqual(hit.text, "")

    def test_from_box_reads_confidence(self):
        source_box = box(0, 0, conf=0.75)
        hit = Hit.from_box(source_box, source=SOURCE_YOLO)
        self.assertAlmostEqual(hit.confidence, 0.75)
        self.assertIs(hit.raw, source_box)

    def test_from_box_explicit_confidence_wins(self):
        hit = Hit.from_box(box(0, 0, conf=0.2), confidence=0.9)
        self.assertAlmostEqual(hit.confidence, 0.9)

    def test_from_box_tolerates_missing_confidence(self):
        class Bare:
            x, y, width, height = 0, 0, 1, 1

        hit = Hit.from_box(Bare())
        self.assertEqual(hit.confidence, 1.0)


class TestTemplateDetector(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()

    def test_miss_returns_none(self):
        self.task.find_feature_result = None
        detector = TemplateDetector("f")
        self.task._resolve_detector(detector)
        self.assertIsNone(detector.detect(object()))

    def test_hit_normalized(self):
        self.task.find_feature_result = box(5, 5, conf=0.8)
        detector = TemplateDetector("f")
        self.task._resolve_detector(detector)
        hit = detector.detect(object())
        self.assertIsNotNone(hit)
        self.assertEqual(hit.source, SOURCE_TEMPLATE)
        self.assertAlmostEqual(hit.confidence, 0.8)

    def test_frame_is_forwarded_not_reacquired(self):
        """判据必须用传入的 frame，不得自行取帧。"""
        sentinel = object()
        detector = TemplateDetector("f", box=box(0, 0))
        self.task._resolve_detector(detector)
        detector.detect(sentinel)
        call = [c for c in self.task.calls if c[0] == "find_feature"][0]
        self.assertIs(call[1]["frame"], sentinel)

    def test_variance_defaults_applied_when_zero(self):
        """find_feature 不传 box 时默认 variance 要生效，否则搜不到移动图标。"""
        detector = TemplateDetector("f")
        self.task._resolve_detector(detector)
        detector.detect(object())
        call = [c for c in self.task.calls if c[0] == "find_feature"][0]
        self.assertGreater(call[1]["horizontal_variance"], 0)
        self.assertGreater(call[1]["vertical_variance"], 0)

    def test_find_one_mode(self):
        self.task.find_one_result = box(1, 1)
        detector = TemplateDetector("f", use_find_one=True)
        self.task._resolve_detector(detector)
        detector.detect(object())
        self.assertEqual(self.task.calls[-1][0], "find_one")

    def test_attach_is_idempotent(self):
        detector = TemplateDetector("f")
        self.task._resolve_detector(detector)
        first = detector._task
        self.task._resolve_detector(detector)
        self.assertIs(detector._task, first)


class TestYoloDetector(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()

    def test_picks_max_conf_by_default(self):
        self.task.yolo_result = [box(0, 0, conf=0.5), box(50, 50, conf=0.9)]
        detector = YoloDetector("t")
        self.task._resolve_detector(detector)
        hit = detector.detect(object())
        self.assertEqual(hit.box.x, 50)
        self.assertEqual(hit.source, SOURCE_YOLO)

    def test_pick_first(self):
        self.task.yolo_result = [box(0, 0, conf=0.5), box(50, 50, conf=0.9)]
        detector = YoloDetector("t", pick="first")
        self.task._resolve_detector(detector)
        self.assertEqual(detector.detect(object()).box.x, 0)

    def test_pick_leftmost(self):
        self.task.yolo_result = [box(80, 0), box(20, 0), box(50, 0)]
        detector = YoloDetector("t", pick="leftmost")
        self.task._resolve_detector(detector)
        self.assertEqual(detector.detect(object()).box.x, 20)

    def test_pick_topmost(self):
        self.task.yolo_result = [box(0, 80), box(0, 20), box(0, 50)]
        detector = YoloDetector("t", pick="topmost")
        self.task._resolve_detector(detector)
        self.assertEqual(detector.detect(object()).box.y, 20)

    def test_pick_center_uses_box_anchor(self):
        # 搜索区域中心在 (500,500)；候选里 (495,495) 比 (10,10) 更近
        self.task.yolo_result = [box(10, 10, 10, 10), box(490, 490, 10, 10)]
        detector = YoloDetector("t", box=box(0, 0, 1000, 1000), pick="center")
        self.task._resolve_detector(detector)
        self.assertEqual(detector.detect(object()).box.x, 490)

    def test_empty_result(self):
        self.task.yolo_result = []
        detector = YoloDetector("t")
        self.task._resolve_detector(detector)
        self.assertIsNone(detector.detect(object()))

    def test_invalid_pick_rejected(self):
        with self.assertRaises(ValueError):
            YoloDetector("t", pick="nonsense")


class TestOcrDetector(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()

    def test_hit_carries_text_and_raw(self):
        result_box = box(3, 4)
        result_box.name = "确认"
        self.task.ocr_result = [result_box]
        detector = OcrDetector("确认")
        self.task._resolve_detector(detector)
        hit = detector.detect(object())
        self.assertEqual(hit.source, SOURCE_OCR)
        self.assertEqual(hit.text, "确认")
        self.assertEqual(hit.raw, [result_box])

    def test_miss_on_empty_list(self):
        self.task.ocr_result = []
        detector = OcrDetector("确认")
        self.task._resolve_detector(detector)
        self.assertIsNone(detector.detect(object()))

    def test_first_pick_is_default(self):
        self.task.ocr_result = [box(1, 1, conf=0.99), box(9, 9, conf=0.1)]
        detector = OcrDetector("x")
        self.task._resolve_detector(detector)
        self.assertEqual(detector.detect(object()).box.x, 1)

    def test_region_params_forwarded(self):
        detector = OcrDetector("x", x=0.1, y=0.2, to_x=0.3, to_y=0.4, lib="paddleocr")
        self.task._resolve_detector(detector)
        detector.detect(object())
        call = [c for c in self.task.calls if c[0] == "ocr"][0][1]
        self.assertEqual(call["lib"], "paddleocr")
        self.assertAlmostEqual(call["to_y"], 0.4)

    def test_name_from_regex(self):
        import re

        self.assertEqual(OcrDetector(re.compile(r"第\d+页")).name, r"第\d+页")

    def test_invalid_pick_rejected(self):
        """非法 pick 应在构造时立刻报错，而不是等到运行时静默取错候选。"""
        with self.assertRaises(ValueError):
            OcrDetector("x", pick="last")   # 曾有 docstring 误写 PICK_LAST，实际不存在

    def test_topmost_pick(self):
        from src.core.detector.ocr_detector import PICK_TOPMOST

        self.task.ocr_result = [box(1, 90), box(9, 20)]
        detector = OcrDetector("x", pick=PICK_TOPMOST)
        self.task._resolve_detector(detector)
        self.assertEqual(detector.detect(object()).box.y, 20)


class TestButtonDetectorAdapter(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()

    def test_hit(self):
        self.task.find_button_result = box(7, 7)
        adapter = ButtonDetectorAdapter(box=box(0, 0, 100, 40))
        self.task._resolve_detector(adapter)
        hit = adapter.detect(object())
        self.assertEqual(hit.source, SOURCE_BUTTON)

    def test_thresholds_forwarded(self):
        sentinel = object()
        adapter = ButtonDetectorAdapter(box=box(0, 0), thresholds=sentinel)
        self.task._resolve_detector(adapter)
        adapter.detect(object())
        call = [c for c in self.task.calls if c[0] == "find_button"][0][1]
        self.assertIs(call["thresholds"], sentinel)

    def test_miss(self):
        self.task.find_button_result = None
        adapter = ButtonDetectorAdapter(box=box(0, 0))
        self.task._resolve_detector(adapter)
        self.assertIsNone(adapter.detect(object()))


class TestCombinators(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()

    def test_multibox_returns_first_hit(self):
        hits = []

        def make(value):
            def predicate(frame):
                hits.append(value)
                return box(value, value) if value else None

            return PredicateDetector(predicate, name=str(value))

        combo = MultiBoxDetector([make(None), make(5), make(9)])
        self.task._resolve_detector(combo)
        hit = combo.detect(object())
        self.assertEqual(hit.box.x, 5)
        self.assertEqual(hits, [None, 5])          # 命中即停，第三项未执行

    def test_multibox_all_miss(self):
        combo = MultiBoxDetector([
            PredicateDetector(lambda f: None, name="a"),
            PredicateDetector(lambda f: None, name="b"),
        ])
        self.task._resolve_detector(combo)
        self.assertIsNone(combo.detect(object()))

    def test_multibox_rejects_empty(self):
        with self.assertRaises(ValueError):
            MultiBoxDetector([])

    def test_multibox_attaches_inner_detectors(self):
        inner = TemplateDetector("f")
        combo = MultiBoxDetector([inner])
        self.task._resolve_detector(combo)
        self.assertIs(inner._task, self.task)

    def test_inverted_detector(self):
        inner = PredicateDetector(lambda f: box(1, 1) if f == "present" else None)
        inverted = InvertedDetector(inner, box=box(2, 2))
        self.task._resolve_detector(inverted)
        self.assertIsNone(inverted.detect("present"))
        self.assertIsNotNone(inverted.detect("absent"))

    def test_inverted_hit_source_reflects_inner_name(self):
        """取反命中的 source 应带原判据名，而不是裸的前缀。

        detector 上没有 ``source`` 属性（那是 Hit 的字段），早期实现
        ``getattr(self._detector, 'source', '')`` 恒为空 → source 永远是 'not'。
        """
        inner = PredicateDetector(lambda f: None, name="band_gone")
        inverted = InvertedDetector(inner, box=box(2, 2))
        hit = inverted.detect(object())
        self.assertIsNotNone(hit)
        self.assertEqual(hit.source, "not_band_gone")

    def test_inverted_without_box_never_hits(self):
        """取反判据无法推出点击目标，未给 box 时不应命中。"""
        inner = PredicateDetector(lambda f: None)
        inverted = InvertedDetector(inner)
        self.task._resolve_detector(inverted)
        self.assertIsNone(inverted.detect(object()))

    def test_blind_point_always_hits(self):
        blind = BlindPointDetector(900, 540)
        hit = blind.detect(object())
        self.assertEqual((hit.box.x, hit.box.y), (900, 540))

    def test_predicate_bool_with_box(self):
        target = box(3, 3)
        detector = PredicateDetector(lambda f: True, box=target)
        self.task._resolve_detector(detector)
        self.assertEqual(detector.detect(object()).box, target)

    def test_predicate_bool_without_box_misses(self):
        detector = PredicateDetector(lambda f: True)
        self.task._resolve_detector(detector)
        self.assertIsNone(detector.detect(object()))


# ── 生命周期 ────────────────────────────────────────────────

def _always(value):
    """构造一个恒返回 value 的判据。"""
    return PredicateDetector(lambda frame: value, name=f"always_{value}")


def _sequence(values):
    """构造按调用次序返回的判据；耗尽后重复最后一个值。"""
    state = {"i": 0}

    def predicate(frame):
        index = min(state["i"], len(values) - 1)
        state["i"] += 1
        return values[index]

    return PredicateDetector(predicate, name="sequence")


class TestWaitExpectation(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()
        self.task.frames = [object()]

    def test_hit(self):
        self.assertIsNotNone(self.task.wait_expectation(_always(box(1, 1)), time_out=1))

    def test_miss(self):
        self.assertIsNone(self.task.wait_expectation(_always(None), time_out=1))


class TestWaitActionResult(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()
        self.task.frames = [object()]
        self.actions = []

    def _record(self, name):
        def action(hit):
            self.actions.append((name, hit.box.x if hit.box else None))

        return action

    # ── 阶段一：前置条件 ──

    def test_condition_miss_returns_false_without_action(self):
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(None),
            time_out=0.5,
        )
        self.assertFalse(result)
        self.assertEqual(self.actions, [])

    def test_condition_miss_raises_when_requested(self):
        from ok import WaitFailedException

        with self.assertRaises(WaitFailedException):
            self.task.wait_action_result(
                action=self._record("main"),
                condition=_always(None),
                time_out=0.3,
                raise_if_not_found=True,
            )

    def test_condition_timeout_elapsed(self):
        start = self.task.now
        self.task.wait_action_result(action=self._record("a"), condition=_always(None), time_out=1.0)
        self.assertGreater(self.task.now - start, 0.5)

    # ── 阶段二：动作 + 验证 ──

    def test_action_runs_when_condition_hits(self):
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(4, 4)),
            time_out=1,
        )
        self.assertTrue(result)
        self.assertEqual(self.actions, [("main", 4)])

    def test_expect_none_returns_immediately(self):
        """expect=None 的契约是「动作成功即返回」。"""
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            expect=None,
            time_out=1,
        )
        self.assertTrue(result)
        self.assertEqual(len(self.actions), 1)

    def test_expect_hit_returns_true(self):
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            expect=_always(box(9, 9)),
            time_out=1,
        )
        self.assertTrue(result)
        self.assertEqual(result.box, box(9, 9))
        self.assertEqual(len(self.actions), 1)

    def test_expect_miss_retries_up_to_max_attempts(self):
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            expect=_always(None),
            time_out=1,
            max_attempts=3,
        )
        self.assertFalse(result)
        self.assertEqual(len(self.actions), 3)

    def test_a_differs_from_c(self):
        """核心能力：A 与 C 可用不同判据。"""
        condition = _always(box(1, 1))          # A：元素在左上
        expect = _always(box(500, 500))         # C：但期待右下出现结果
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=condition,
            expect=expect,
            time_out=1,
        )
        self.assertTrue(result)

    def test_action_delay_and_after_sleep_respected(self):
        self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            time_out=1,
            action_delay=0.25,
            after_sleep=0.5,
        )
        self.assertAlmostEqual(self.task.now, 0.75, places=2)

    def test_fresh_frame_used_for_action_target(self):
        """条件命中后要重取帧拿最新 box（防移动图标位移）。"""
        condition = _sequence([box(1, 1), box(99, 99)])
        self.task.wait_action_result(
            action=self._record("main"),
            condition=condition,
            time_out=1,
        )
        self.assertEqual(self.actions, [("main", 99)])

    def test_falls_back_to_original_hit_when_refresh_misses(self):
        condition = _sequence([box(7, 7), None])
        self.task.wait_action_result(
            action=self._record("main"),
            condition=condition,
            time_out=1,
        )
        self.assertEqual(self.actions, [("main", 7)])

    # ── 阶段三：持续阶段 ──

    def test_while_condition_repeats_until_miss(self):
        """B 持续命中则重复附加动作；B 未命中立即停止。"""
        # 首次（阶段一）命中，之后 B 连续三次命中，第四次未命中
        while_hits = _sequence([box(1, 1), box(1, 1), box(1, 1), None])
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            expect=_always(None),
            while_condition=while_hits,
            repeat_action=self._record("extra"),
            max_attempts=1,
            time_out=5,
        )
        self.assertFalse(result)
        self.assertEqual([a[0] for a in self.actions], ["main", "extra", "extra", "extra"])

    def test_while_condition_c_hit_ends_success(self):
        """C 在持续阶段命中 → 结束并判定成功。"""
        time_holder = {}

        call_count = {"n": 0}

        def expect_predicate(frame):
            call_count["n"] += 1
            return box(9, 9) if call_count["n"] > 2 else None

        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            expect=PredicateDetector(expect_predicate, name="expect"),
            while_condition=_always(box(1, 1)),
            repeat_action=self._record("extra"),
            max_attempts=1,
            time_out=5,
        )
        self.assertTrue(result)

    def test_max_repeat_limits_extra_actions(self):
        while_hits = _sequence([box(1, 1), box(1, 1), box(1, 1), box(1, 1), box(1, 1)])
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            expect=_always(None),
            while_condition=while_hits,
            repeat_action=self._record("extra"),
            max_attempts=1,
            max_repeat=2,
            time_out=5,
        )
        self.assertFalse(result)
        self.assertEqual([a[0] for a in self.actions], ["main", "extra", "extra"])

    def test_while_condition_none_skips_stage_three(self):
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            expect=_always(None),
            max_attempts=1,
            time_out=5,
        )
        self.assertFalse(result)
        self.assertEqual([a[0] for a in self.actions], ["main"])

    def test_expect_none_returns_before_stage_three(self):
        """expect=None 时不会进入持续阶段（动作成功即返回）。"""
        result = self.task.wait_action_result(
            action=self._record("main"),
            condition=_always(box(1, 1)),
            expect=None,
            while_condition=_always(box(1, 1)),
            repeat_action=self._record("extra"),
            time_out=5,
        )
        self.assertTrue(result)
        self.assertEqual([a[0] for a in self.actions], ["main"])

    # ── condition 为 None 时跳过等待直接执行 ──

    def test_condition_none_executes_action_directly(self):
        """condition 默认为 None，且不等待直接执行。"""
        executed = []
        result = self.task.wait_action_result(action=lambda hit: executed.append(hit))
        self.assertTrue(result)
        self.assertEqual(executed, [None])
        self.assertEqual(self.task.now, 0.0)  # 没有发生 time_out 等待

    def test_condition_none_with_zero_arg_action(self):
        """action 可以是 0 参数可调用对象。"""
        executed = []
        result = self.task.wait_action_result(action=lambda: executed.append("run"))
        self.assertTrue(result)
        self.assertEqual(executed, ["run"])

    def test_condition_none_with_expect(self):
        """condition=None 时，expect 仍然正常工作并返回 expect 的 Hit。"""
        count = []
        result = self.task.wait_action_result(
            action=lambda: count.append(1),
            expect=_always(box(10, 10)),
        )
        self.assertTrue(result)
        self.assertEqual(result.box, box(10, 10))
        self.assertEqual(len(count), 1)

    def test_returns_expect_hit_object(self):
        """命中 expect 时返回 expect 的完整 Hit 对象。"""
        expected_hit = Hit(box=box(12, 34), confidence=0.95, text="confirm")
        detector = PredicateDetector(lambda frame: expected_hit)
        result = self.task.wait_action_result(
            action=lambda: None,
            expect=detector,
        )
        self.assertIs(result, expected_hit)
        self.assertEqual(result.box, box(12, 34))
        self.assertEqual(result.text, "confirm")

    def test_expect_miss_returns_none(self):
        """expect 未命中返回 None。"""
        result = self.task.wait_action_result(
            action=lambda: None,
            expect=_always(None),
            max_attempts=1,
        )
        self.assertIsNone(result)

    def test_condition_none_expect_miss_retries(self):
        """condition=None 时，expect 未命中会按 max_attempts 重试。"""
        count = []
        result = self.task.wait_action_result(
            action=lambda: count.append(1),
            expect=_always(None),
            max_attempts=3,
        )
        self.assertFalse(result)
        self.assertEqual(len(count), 3)

    def test_condition_none_with_draw_does_not_crash(self):
        """condition=None 时开启 draw 不会报错。"""
        result = self.task.wait_action_result(
            action=lambda: None,
            draw=True,
        )
        self.assertTrue(result)

    def test_condition_none_raise_if_not_found_not_triggered(self):
        """condition=None 时 raise_if_not_found 不会抛出异常，直接执行。"""
        executed = []
        result = self.task.wait_action_result(
            action=lambda: executed.append("ok"),
            raise_if_not_found=True,
        )
        self.assertTrue(result)
        self.assertEqual(executed, ["ok"])

    def test_zero_arg_action_with_condition(self):
        """即便指定了 condition，0 参数 action 也能正常执行。"""
        executed = []
        result = self.task.wait_action_result(
            condition=_always(box(1, 1)),
            action=lambda: executed.append("run"),
        )
        self.assertTrue(result)
        self.assertEqual(executed, ["run"])

    def test_zero_arg_repeat_action(self):
        """repeat_action 也支持 0 参数。"""
        executed = []
        while_hits = _sequence([box(1, 1), box(1, 1), None])
        result = self.task.wait_action_result(
            action=lambda: None,
            expect=_always(None),
            while_condition=while_hits,
            repeat_action=lambda: executed.append("repeat"),
            time_out=5,
        )
        self.assertFalse(result)
        self.assertEqual(executed, ["repeat", "repeat"])

    def test_zero_arg_action_raising_type_error_not_wrapped(self):
        """0 参数动作内部抛出 TypeError 时，不应被转为 'takes 0 positional arguments but 1 was given'。"""
        def bad_action():
            return 1 + "a"

        with self.assertRaises(TypeError) as ctx:
            self.task.wait_action_result(action=bad_action)
        self.assertIn("unsupported operand type", str(ctx.exception))


class TestFrameworkOverrideClick(unittest.TestCase):
    def setUp(self):
        class MockBaseTask:
            def __init__(self):
                self.calls = []

            def click(self, x=-1, y=-1, *args, **kwargs):
                self.calls.append(("click", x, y, kwargs))
                if isinstance(x, Box):
                    return self.click_box(x, *args, **kwargs)
                return True

            def click_box(self, box=None, *args, **kwargs):
                self.calls.append(("click_box", box, kwargs))
                return True

        class Task(FrameworkOverrideMixin, MockBaseTask):
            def __init__(self):
                super().__init__()
                self.logger = None

        self.task = Task()

    def test_click_hit_unpacks_box(self):
        hit = Hit(box=box(10, 20))
        self.task.click(hit)
        self.assertEqual(len(self.task.calls), 2)
        self.assertEqual(self.task.calls[0][0], "click")
        self.assertEqual(self.task.calls[0][1], hit.box)
        self.assertEqual(self.task.calls[1][0], "click_box")
        self.assertEqual(self.task.calls[1][1], hit.box)

    def test_click_none_safe(self):
        result = self.task.click(None)
        self.assertFalse(result)
        self.assertEqual(self.task.calls, [])

    def test_click_box_hit_unpacks_box(self):
        hit = Hit(box=box(30, 40))
        self.task.click_box(hit)
        self.assertEqual(self.task.calls[0][0], "click_box")
        self.assertEqual(self.task.calls[0][1], hit.box)

    def test_click_keyword_box_hit_unpacks_box(self):
        hit = Hit(box=box(50, 60))
        self.task.click(box=hit)
        self.assertEqual(self.task.calls[0][3]["box"], hit.box)


class TestDetectorResolution(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()

    def test_non_detector_rejected(self):
        with self.assertRaises(TypeError):
            self.task._resolve_detector(object())

    def test_none_passes_through(self):
        self.assertIsNone(self.task._resolve_detector(None))

    def test_plain_callable_detector_accepted(self):
        class Custom:
            name = "custom"

            def detect(self, frame):
                return None

        self.assertIsInstance(self.task._resolve_detector(Custom()), Custom)

    def test_unattached_detectors_raise_runtime_error(self):
        """未 attach 时直接 detect 应报明确的 RuntimeError 而非 AttributeError。"""
        dummy_frame = object()
        with self.assertRaises(RuntimeError) as ctx:
            OcrDetector("test").detect(dummy_frame)
        self.assertIn("未绑定任务宿主", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            TemplateDetector("feat").detect(dummy_frame)
        self.assertIn("未绑定任务宿主", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            YoloDetector("target").detect(dummy_frame)
        self.assertIn("未绑定任务宿主", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            ButtonDetectorAdapter(box=Box(0, 0, 10, 10)).detect(dummy_frame)
        self.assertIn("未绑定任务宿主", str(ctx.exception))

    def test_inverted_detector_attaches_inner(self):
        """InvertedDetector 应能递归 attach 内部判据。"""
        inner = TemplateDetector("f")
        inv = InvertedDetector(inner, box=Box(1, 1, 10, 10))
        self.task._resolve_detector(inv)
        self.assertIs(inner._task, self.task)

    def test_detect_with_scroll_auto_attaches_ocr_detector(self):
        """detect_with_scroll 必须自动调用 _resolve_detector 绑定宿主。"""
        self.task.frames = [object()]
        self.task.ocr_result = [Box(10, 20, 30, 40, confidence=0.9, name="委托")]

        detector = OcrDetector("委托")
        self.assertIsNone(detector._task)

        hit = self.task.detect_with_scroll(
            detector=detector,
            box=Box(0, 0, 100, 100),
            scroll_count=3,
        )
        self.assertIsNotNone(hit)
        self.assertIs(detector._task, self.task)
        self.assertEqual(hit.box.x, 10)
        self.assertEqual(hit.box.y, 20)

    def test_detect_with_scroll_auto_attaches_template_detector(self):
        """detect_with_scroll 传入 TemplateDetector 同样应自动绑定宿主。"""
        self.task.frames = [object()]
        self.task.find_feature_result = Box(15, 25, 30, 40, confidence=0.85)

        detector = TemplateDetector("icon")
        self.assertIsNone(detector._task)

        hit = self.task.detect_with_scroll(
            detector=detector,
            box=Box(0, 0, 100, 100),
            scroll_count=-3,
        )
        self.assertIsNotNone(hit)
        self.assertIs(detector._task, self.task)
        self.assertEqual(hit.box.x, 15)
        self.assertEqual(hit.box.y, 25)


if __name__ == "__main__":
    unittest.main()
