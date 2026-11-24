from __future__ import annotations

_PATCH_INSTALLED = False


def install_startup_patches():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.patches.capture_timestamp_patch import install_capture_timestamp_patch
    from src.patches.cascade_dropdown_patch import install_cascade_dropdown_patch
    from src.patches.close_behavior_patch import install_close_behavior_patch
    from src.patches.crash_guard_patch import install_crash_guard_patch
    from src.patches.i18n_collection_patch import install_i18n_collection_patch
    from src.patches.launch_args_patch import install_launch_args_patch
    from src.patches.nav_layout_patch import install_nav_layout_patch
    from src.patches.no_frame_task_patch import install_no_frame_task_patch
    from src.patches.ocr_text_fix_patch import install_ocr_text_fix_patch
    from src.patches.process_execute_patch import install_process_execute_patch
    from src.patches.qfluent_mute_promo_patch import install_mute_promo_patch
    from src.patches.qfluent_navigation_patch import install_qfluent_navigation_patch
    from src.patches.screenshot_sidecar_patch import install_screenshot_sidecar_patch
    from src.patches.startup_window_patch import install_startup_window_patch
    from src.patches.task_config_lock_patch import install_task_config_lock_patch
    from src.patches.ui_extras_patch import install_ui_extras_patch
    from src.patches.win32_gdi_point_patch import install_win32_gdi_point_patch

    # 崩溃诊断必须第一个挂：后面任何一个补丁初始化时炸了也要留下记录
    install_crash_guard_patch()
    install_cascade_dropdown_patch()
    install_capture_timestamp_patch()
    # 关闭询问要早于其它 UI 补丁：它是用户最先碰到的交互
    install_close_behavior_patch()
    install_i18n_collection_patch()
    install_launch_args_patch()
    install_mute_promo_patch()
    # 导航布局：窗口建好后合并滚动区（只影响侧栏外观）
    install_nav_layout_patch()
    install_no_frame_task_patch()
    install_ocr_text_fix_patch()
    install_process_execute_patch()
    install_qfluent_navigation_patch()
    install_screenshot_sidecar_patch()
    install_startup_window_patch()
    install_task_config_lock_patch()
    install_ui_extras_patch()
    install_win32_gdi_point_patch()
    _PATCH_INSTALLED = True
