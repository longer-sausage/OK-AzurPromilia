from __future__ import annotations

_PATCH_INSTALLED = False


def install_startup_patches():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.patches.cascade_dropdown_patch import install_cascade_dropdown_patch
    from src.patches.capture_timestamp_patch import install_capture_timestamp_patch
    from src.patches.no_frame_task_patch import install_no_frame_task_patch
    from src.patches.ocr_text_fix_patch import install_ocr_text_fix_patch
    from src.patches.screenshot_sidecar_patch import install_screenshot_sidecar_patch
    from src.patches.task_config_lock_patch import install_task_config_lock_patch
    from src.patches.win32_gdi_point_patch import install_win32_gdi_point_patch

    install_cascade_dropdown_patch()
    install_capture_timestamp_patch()
    install_no_frame_task_patch()
    install_ocr_text_fix_patch()
    install_screenshot_sidecar_patch()
    install_task_config_lock_patch()
    install_win32_gdi_point_patch()
    _PATCH_INSTALLED = True
