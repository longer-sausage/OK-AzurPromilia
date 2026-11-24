"""录制转流程测试：down/up 配对、空档补 wait、跳过不支持的输入、上限截断。"""

import unittest

from src.core.flow.recording import (
    MAX_RECORDING_STEPS,
    RecordingConversion,
    convert_recording,
    suggest_flow_name,
)


def mouse_down(x, y, t, button="left"):
    return {"type": "mouse_down", "x": x, "y": y, "button": button, "time": t}


def mouse_up(x, y, t, button="left"):
    return {"type": "mouse_up", "x": x, "y": y, "button": button, "time": t}


def key_down(key, t):
    return {"type": "key_down", "key": key, "time": t}


def key_up(key, t):
    return {"type": "key_up", "key": key, "time": t}


class TestRecordingConversion(unittest.TestCase):
    def test_empty(self):
        result = convert_recording([])
        self.assertEqual(result.steps, [])
        self.assertIn("没有录制事件", result.notes)

    def test_click_pair(self):
        result = convert_recording([mouse_down(0.5, 0.25, 1.0), mouse_up(0.5, 0.25, 1.2)])
        self.assertEqual(len(result.steps), 1)
        step = result.steps[0]
        self.assertEqual(step["type"], "click")
        self.assertAlmostEqual(step["x"], 0.5)
        self.assertAlmostEqual(step["y"], 0.25)
        self.assertAlmostEqual(step["duration"], 0.2)

    def test_key_pair(self):
        result = convert_recording([key_down("w", 1.0), key_up("w", 1.5)])
        self.assertEqual(result.steps, [{"type": "key", "key": "w", "duration": 0.5}])

    def test_gap_becomes_wait(self):
        result = convert_recording(
            [mouse_down(0.1, 0.1, 1.0), mouse_up(0.1, 0.1, 1.0),
             mouse_down(0.2, 0.2, 2.0), mouse_up(0.2, 0.2, 2.0)]
        )
        kinds = [step["type"] for step in result.steps]
        self.assertEqual(kinds, ["click", "wait", "click"])
        self.assertAlmostEqual(result.steps[1]["seconds"], 1.0)

    def test_small_gap_is_ignored(self):
        result = convert_recording(
            [mouse_down(0.1, 0.1, 1.0), mouse_up(0.1, 0.1, 1.0),
             mouse_down(0.2, 0.2, 1.01), mouse_up(0.2, 0.2, 1.01)]
        )
        self.assertEqual([step["type"] for step in result.steps], ["click", "click"])

    def test_events_are_sorted_by_time(self):
        result = convert_recording(
            [key_down("e", 2.0), key_up("e", 2.1), key_down("q", 1.0), key_up("q", 1.1)]
        )
        keys = [step["key"] for step in result.steps if step["type"] == "key"]
        self.assertEqual(keys, ["q", "e"])
        # 两个动作之间 0.9s 的空档会被补成 wait
        self.assertEqual([step["type"] for step in result.steps], ["key", "wait", "key"])

    def test_right_click_is_skipped(self):
        result = convert_recording(
            [mouse_down(0.5, 0.5, 1.0, "right"), mouse_up(0.5, 0.5, 1.1, "right")]
        )
        self.assertEqual(result.steps, [])
        self.assertEqual(result.skipped, 1)
        self.assertTrue(any("right" in note for note in result.notes))

    def test_mouse_move_and_wheel_are_skipped(self):
        result = convert_recording(
            [
                {"type": "mouse_move", "x": 0.3, "y": 0.3, "time": 1.0},
                {"type": "scroll", "x": 0, "y": 0, "dy": 1, "time": 2.0},
                key_down("f", 3.0),
                key_up("f", 3.1),
            ]
        )
        self.assertEqual(result.skipped, 2)
        self.assertEqual(len(result.steps), 1)

    def test_key_without_release_uses_short_press(self):
        result = convert_recording([key_down("space", 1.0)])
        self.assertEqual(result.steps, [{"type": "key", "key": "space", "duration": 0.05}])
        self.assertTrue(any("没有抬起" in note for note in result.notes))

    def test_key_up_without_down(self):
        result = convert_recording([key_up("a", 2.0)])
        self.assertEqual(result.steps[0]["type"], "key")
        self.assertGreater(result.steps[0]["duration"], 0)

    def test_click_without_release(self):
        result = convert_recording([mouse_down(0.4, 0.4, 1.0)])
        self.assertEqual(result.steps[0]["type"], "click")
        self.assertNotIn("duration", result.steps[0])  # 零时长点击不写冗余字段
        self.assertNotIn("hold", result.steps[0])
        self.assertTrue(any("没有抬起" in note for note in result.notes))

    def test_truncation(self):
        events = []
        for index in range(10):
            events.append(mouse_down(0.1, 0.1, float(index)))
            events.append(mouse_up(0.1, 0.1, float(index)))
        result = convert_recording(events, max_steps=4)
        self.assertTrue(result.truncated)
        self.assertEqual(result.count, 4)
        self.assertIn("超过上限", result.summary())

    def test_step_cap_matches_documented_limit(self):
        self.assertEqual(MAX_RECORDING_STEPS, 400)

    def test_summary_reports_skipped(self):
        conversion = RecordingConversion(steps=[{"type": "wait", "seconds": 1}], skipped=3)
        self.assertIn("跳过 3", conversion.summary())

    def test_non_mapping_events_are_ignored(self):
        result = convert_recording([None, "x", 42, key_down("f", 1.0), key_up("f", 1.1)])
        self.assertEqual(len(result.steps), 1)

    def test_to_flow_shape(self):
        result = convert_recording([key_down("f", 1.0), key_up("f", 1.1)])
        flow = result.to_flow("我的流程")
        self.assertEqual(flow["name"], "我的流程")
        self.assertEqual(flow["version"], 1)
        self.assertEqual(flow["steps"], result.steps)
        self.assertIn("1 步", flow["description"])

    def test_suggest_flow_name(self):
        self.assertIn("3", suggest_flow_name([{}, {}, {}]))
        self.assertEqual(suggest_flow_name([]), "录制流程")


class TestLongPressAndCoordinates(unittest.TestCase):
    def test_long_press_becomes_hold(self):
        result = convert_recording([mouse_down(0.2, 0.2, 1.0), mouse_up(0.2, 0.2, 2.5)])
        step = result.steps[0]
        self.assertAlmostEqual(step["hold"], 1.5)   # 真的按住
        self.assertNotIn("duration", step)          # 不是「点一下再等」

    def test_short_press_stays_duration(self):
        result = convert_recording([mouse_down(0.2, 0.2, 1.0), mouse_up(0.2, 0.2, 1.05)])
        step = result.steps[0]
        self.assertNotIn("hold", step)
        self.assertAlmostEqual(step["duration"], 0.05)

    def test_raw_pixel_coordinates_are_skipped(self):
        """录制器拿不到窗口尺寸时会退回原始像素，那种事件不能当比例坐标用。"""
        result = convert_recording([mouse_down(1200, 700, 1.0), mouse_up(1200, 700, 1.05)])
        self.assertEqual(result.steps, [])
        self.assertEqual(result.bad_coordinates, 1)
        self.assertIn("坐标异常", result.summary())
        self.assertTrue(any("坐标异常" in note for note in result.notes))

    def test_slightly_over_one_is_clamped(self):
        result = convert_recording([mouse_down(1.001, 0.999, 1.0), mouse_up(1.001, 0.999, 1.02)])
        self.assertEqual(result.bad_coordinates, 0)
        self.assertAlmostEqual(result.steps[0]["x"], 1.0)

    def test_unreleased_press_with_bad_coordinates(self):
        result = convert_recording([mouse_down(800, 600, 1.0)])
        self.assertEqual(result.steps, [])
        self.assertEqual(result.bad_coordinates, 1)


if __name__ == "__main__":
    unittest.main()
