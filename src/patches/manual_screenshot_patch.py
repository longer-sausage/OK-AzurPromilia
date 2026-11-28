"""ok-script 3.0.14 的手动截图入口。保留框架的保存格式、截图处理器和完成通知。"""

from functools import partial
from pathlib import Path

import cv2
from ok import og
from ok.core.code_runner import submit_call
from ok.core.screenshot import screenshot_file_stem
from ok.task.exceptions import ExecutorBusyException
from ok.ui.qt.debug.diagnosis_page import DiagnosisPage
from ok.ui.qt.kit.feedback import show_toast
from ok.ui.qt.shell import diagnostics
from ok.ui.qt.tasks import TemplateTab
from ok.ui.qt.util.Alert import alert_error, alert_info
from ok.util.blur import apply_blur_areas, get_blur_algorithm
from PySide6.QtCore import QCoreApplication

from src.core.game_window import ManualCaptureError, capture_manual_frame


def _manual_frame():
    try:
        return capture_manual_frame(og.device_manager)
    except ManualCaptureError as exc:
        raise ManualCaptureError(og.app.tr(str(exc))) from None


def _capture_diagnostic(processor=None):
    try:
        method = getattr(og.device_manager, "capture_method", None)
        frame = _manual_frame()
        shots = og.ok.screenshot
        return shots.generate_screen_shot(frame, shots.ui_dict, shots.screenshot_folder,
                                          screenshot_file_stem(type(method).__name__), True, None,
                                          processor=processor)
    except Exception as exc:
        diagnostics.logger.error("capture failed", exc)
        alert_error(str(exc))
        return None


def _capture_template():
    frame = _manual_frame()
    frame = apply_blur_areas(frame, og.config.get("blur_area"), get_blur_algorithm(og.global_config))
    if processor := og.config.get("screenshot_processor"):
        frame = processor(frame.copy())
    folder = TemplateTab.ensure_template_folder()
    path = Path(folder) / f"{TemplateTab.get_next_image_name(folder)}.png"
    if not cv2.imwrite(str(path), frame):
        raise RuntimeError(QCoreApplication.translate("TemplateTab", "Could not write screenshot to {path}")
                           .format(path=path))
    return str(path)


def _capture_ocr(task):
    # 显式传帧。框架默认从 executor.frame 读取，那里可能仍是置前之前的画面。
    return task.ocr(frame=_manual_frame(), log=True, screenshot=True)


def _diagnosis_ocr(page):
    tasks = og.executor.get_all_tasks()
    if not tasks:
        return
    try:
        job = submit_call(og.executor, partial(_capture_ocr, tasks[0]), name="diagnosis_ocr")
    except ExecutorBusyException as exc:
        message = QCoreApplication.translate("JobControls", "The app is controlled by {owner}").format(owner=exc.owner)
        show_toast(page, message, "OCR", tone="danger")
        return
    page.ocr_button.setEnabled(False)
    job.future.add_done_callback(lambda _: page.ocr_done.emit(job, None))


def _connection_ocr():
    if og.executor.paused:
        alert_error(diagnostics._tr("Please Start First"))
        return
    try:
        result = _capture_ocr(og.executor.get_all_tasks()[0])
        alert_info(diagnostics._tr(f"OCR success (Logged): {result}"))
        folder = diagnostics.screenshot_folder()
        if folder:
            if result:
                text = "".join(f"{box.name}, {box}, {box.confidence}\n" for box in result)
                (Path(folder) / "ocr_result.txt").write_text(text, encoding="utf-8")
            diagnostics.open_screenshot_folder()
    except Exception as exc:
        diagnostics.logger.error("debug ocr_log exception", exc)
        alert_error(str(exc))


def install_manual_screenshot_patch():
    # 这些函数在按钮、快捷键的工作线程或 OCR 作业中调用。自动截图走独立的框架路径。
    diagnostics.capture = _capture_diagnostic
    TemplateTab.save_template_screenshot = _capture_template
    diagnostics._ocr_log = _connection_ocr
    DiagnosisPage.run_ocr = _diagnosis_ocr
