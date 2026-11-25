import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import og
from ok.core import start_controller
from ok.ui.qt.tasks import config_form
from PySide6.QtWidgets import QApplication

from src.config import config
from src.gui.account_config_tab import AccountConfigTab, InMemoryConfig
from src.patches import cascade_dropdown_patch
from src.tasks.onetime.commission_daily_task import CommissionDailyTask


class TestFrameworkIntegration(unittest.TestCase):
    def test_native_launcher_passes_arguments_to_windows(self):
        with TemporaryDirectory(prefix="Azur Promilia ") as directory:
            exe = Path(directory, "AzurPromilia.exe")
            exe.touch()
            device_manager = Mock()
            device_manager.get_preferred_device.return_value = {"device": "windows", "connected": False}
            device_manager.get_exe_path.return_value = str(exe)
            global_config = Mock()
            with (
                patch.object(og, "device_manager", device_manager),
                patch.object(og, "global_config", global_config, create=True),
                patch.object(start_controller, "Handler"),
                patch.object(start_controller, "is_admin", return_value=True),
                patch.object(start_controller, "communicate"),
                patch.object(os, "startfile") as launch,
            ):
                controller = start_controller.StartController(config, Event())
                with (
                    patch.object(controller, "_wait_until_started_window_stable", return_value=True),
                    patch.object(controller, "_wait_until_device_ready", return_value=True),
                ):
                    for dx11, prefix in ((False, ""), (True, "-dx11 -d3d11 -force-d3d11 ")):
                        with self.subTest(dx11=dx11):
                            global_config.get_config.return_value = {"Launch with DX11": dx11}
                            self.assertTrue(controller.start_device())
                            launch.assert_called_with(str(exe), "open", prefix + "-start=azurpromilia_launcher",
                                                      directory, 5)

    def test_grouped_commissions_in_native_settings_form(self):
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        key = "选择委托"
        defaults = CommissionDailyTask.sub_task_default_config
        types = CommissionDailyTask.sub_task_config_type
        values = InMemoryConfig(defaults, defaults)
        with (
            patch.object(og, "app", SimpleNamespace(tr=str)),
            patch.object(config_form, "config_editor", config_form.config_editor),
            patch.object(cascade_dropdown_patch, "_PATCH_INSTALLED", False),
        ):
            cascade_dropdown_patch.install_cascade_dropdown_patch()
            tab = AccountConfigTab()
            form = config_form.ConfigForm(None, values, defaults, {}, types, parent=tab)
            editor = form.config_widget_by_key[key]
            groups = editor.button.menu().actions()
            choices = [action for group in groups for action in group.menu().actions()]
            self.assertEqual([action.data() for action in choices], types[key]["options"])
            self.assertEqual(editor.button.text(), defaults[key])
            choices[-1].trigger()
            self.assertEqual(editor.button.text(), choices[-1].text())
            self.assertEqual(values[key], choices[-1].data())
            tab.deleteLater()
            app.processEvents()
