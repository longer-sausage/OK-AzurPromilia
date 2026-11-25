from PySide6.QtCore import QObject

from ok import og

from src.patches.startup_patches import install_startup_patches


class Globals(QObject):

    def __init__(self, exit_event):
        super().__init__()
        # ok-script creates my_app for the desktop launchers and the CLI.
        og.app.start_controller.STARTED_WINDOW_MIN_SIZE = og.app.config["supported_resolution"]["min_size"]
        install_startup_patches()

    def on_show_main_window(self, window):
        if not window.config.get("check_updates_on_startup", True):
            # The framework calls this check when the window first appears.
            window._startup_update_check = lambda: None
