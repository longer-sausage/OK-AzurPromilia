"""「流程与队列」页的动作录制测试。

录制用的是框架的 ``Recorder``（pynput 钩子），这里替换成假录像器，
验证接线正确：开始/停止、事件转流程并落盘、空录制不生成文件、任务运行中拒绝录制。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() or QApplication([])
    except Exception:
        return None


class FakeRecorder:
    def __init__(self, events=None):
        self.is_recording = False
        self.events = list(events or [])
        self.started_with = None
        self.stopped = 0

    def start(self, target):
        self.is_recording = True
        self.started_with = target

    def stop(self):
        self.is_recording = False
        self.stopped += 1
        return "", ""


class FakeTask:
    name = "一键日常"


class FakeExecutor:
    def __init__(self, task=None):
        self.current_task = task


class TestFlowRecording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _app() is None:
            raise unittest.SkipTest("Qt 离屏不可用")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self._app_patch = mock.patch("ok.og.app", mock.Mock(tr=lambda s: s), create=True)
        self._app_patch.start()

    def tearDown(self):
        self._app_patch.stop()
        self._tmp.cleanup()

    def _tab(self):
        from src.gui.flow_tab import FlowTab

        tab = FlowTab()
        build = getattr(tab, "_build", None)
        if callable(build):
            build()
        tab._notify = lambda *args, **kwargs: None
        tab.refresh_flows = lambda: None
        tab._set_status = lambda widget, text: None
        return tab

    def test_start_recording_calls_framework_recorder(self):
        tab = self._tab()
        fake = FakeRecorder()
        with mock.patch("ok.ui.qt.tasks.RecordScript.recorder", fake), mock.patch(
            "ok.og.executor", FakeExecutor(), create=True
        ):
            tab._start_recording()
        self.assertTrue(fake.is_recording)
        self.assertTrue(tab._recording)

    def test_refuses_while_task_running(self):
        tab = self._tab()
        fake = FakeRecorder()
        with mock.patch("ok.ui.qt.tasks.RecordScript.recorder", fake), mock.patch(
            "ok.og.executor", FakeExecutor(FakeTask()), create=True
        ):
            tab._start_recording()
        self.assertFalse(fake.is_recording, "任务运行中不该开始录制")
        self.assertFalse(tab._recording)

    def test_stop_generates_flow_file(self):
        tab = self._tab()
        events = [
            {"type": "mouse_down", "x": 0.2, "y": 0.3, "button": "left", "time": 1.0},
            {"type": "mouse_up", "x": 0.2, "y": 0.3, "time": 1.1},
            {"type": "key_down", "key": "w", "time": 2.0},
            {"type": "key_up", "key": "w", "time": 2.2},
        ]
        fake = FakeRecorder(events)

        written = {}

        def fake_save(flow, folder=None):
            path = self.folder / f"{flow.name}.json"
            path.write_text(flow.to_json(), encoding="utf-8")
            written["path"] = path
            written["flow"] = flow
            return path

        with mock.patch("ok.ui.qt.tasks.RecordScript.recorder", fake), mock.patch(
            "src.gui.flow_tab.save_flow", side_effect=fake_save
        ):
            tab._recording = True
            tab._stop_recording()

        self.assertEqual(fake.stopped, 1)
        self.assertFalse(tab._recording)
        self.assertIn("path", written)
        types = [step.type for step in written["flow"].steps]
        # 0.9 秒空档会被补一个 wait；click/key 都在，顺序正确
        self.assertEqual(types, ["click", "wait", "key"])

    def test_empty_recording_creates_nothing(self):
        tab = self._tab()
        fake = FakeRecorder([])
        with mock.patch("ok.ui.qt.tasks.RecordScript.recorder", fake), mock.patch(
            "src.gui.flow_tab.save_flow"
        ) as save_mock:
            tab._recording = True
            tab._stop_recording()
        save_mock.assert_not_called()
        self.assertFalse(tab._recording)


if __name__ == "__main__":
    unittest.main()
