import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import og
from ok.core import start_controller
from ok.device.DeviceManager import DeviceManager
from ok.ui.qt.tasks import config_form
from ok.util import window as ok_window
from PySide6.QtWidgets import QApplication

from src.config import config
from src.core import game_window
from src.gui.account_config_tab import AccountConfigTab, InMemoryConfig
from src.patches import cascade_dropdown_patch
from src.tasks.onetime.commission_daily_task import CommissionDailyTask


class TestFrameworkIntegration(unittest.TestCase):
    def test_native_launcher_passes_arguments_to_windows(self):
        for exe_name in ("AzurPromilia.exe", "AzurPromiliaJP.exe"):
            with TemporaryDirectory(prefix="Azur Promilia ") as directory:
                exe = Path(directory, exe_name)
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
                            with self.subTest(exe_name=exe_name, dx11=dx11):
                                global_config.get_config.return_value = {"Launch with DX11": dx11}
                                self.assertTrue(controller.start_device())
                                launch.assert_called_with(str(exe), "open", prefix + "-start=azurpromilia_launcher",
                                                          directory, 5)

    def test_find_game_hwnd_matches_cn_and_jp_clients(self):
        windows_by_case = (
            ("AzurPromilia.exe", 1001),
            ("AzurPromiliaJP.exe", 2002),
            ("azurpromiliajp.exe", 2003),
        )
        for exe_name, target_hwnd in windows_by_case:
            with self.subTest(exe_name=exe_name):
                fake_windows = {
                    999: ("OtherWndClass", "AzurPromiliaJP.exe", (0, 0, 1920, 1080)),
                    1000: ("UnityWndClass", "OtherGame.exe", (0, 0, 1920, 1080)),
                    target_hwnd: ("UnityWndClass", exe_name, (0, 0, 1920, 1080)),
                }

                def enum_windows(callback, extra):
                    for hwnd in fake_windows:
                        callback(hwnd, extra)

                with (
                    patch.object(game_window.win32gui, "EnumWindows", side_effect=enum_windows),
                    patch.object(game_window.win32gui, "GetForegroundWindow", return_value=target_hwnd),
                    patch.object(game_window.win32gui, "IsWindowVisible", return_value=True),
                    patch.object(game_window.win32gui, "GetClassName", side_effect=lambda h: fake_windows[h][0]),
                    patch.object(game_window.win32gui, "GetWindowRect", side_effect=lambda h: fake_windows[h][2]),
                    patch.object(game_window.win32process, "GetWindowThreadProcessId", side_effect=lambda h: (1, h)),
                    patch.object(
                        game_window.psutil,
                        "Process",
                        side_effect=lambda pid: SimpleNamespace(name=lambda p=pid: fake_windows[p][1]),
                    ),
                ):
                    self.assertEqual(game_window.find_game_hwnd(config["windows"], timeout=0.01), target_hwnd)

    def test_ok_find_hwnd_and_device_manager_recognize_jp_client_with_stale_cn_cache(self):
        jp_hwnd = 24680
        jp_path = r"D:\YostarGames\AZUPRO_JP\AzurPromiliaJP.exe"
        fake_windows = {
            jp_hwnd: (
                "UnityWndClass",
                "AzurPromiliaJP",
                "AzurPromiliaJP.exe",
                jp_path,
                [jp_path, "-start=azurpromilia_launcher"],
            ),
        }

        def enum_windows(callback, results):
            for hwnd in fake_windows:
                callback(hwnd, results)

        with (
            patch.object(ok_window.win32gui, "EnumWindows", side_effect=enum_windows),
            patch.object(ok_window.win32gui, "IsWindow", return_value=True),
            patch.object(ok_window.win32gui, "IsWindowEnabled", return_value=True),
            patch.object(ok_window.win32gui, "IsWindowVisible", return_value=True),
            patch.object(ok_window.win32gui, "GetClassName", side_effect=lambda h: fake_windows[h][0]),
            patch.object(ok_window.win32gui, "GetWindowText", side_effect=lambda h: fake_windows[h][1]),
            patch.object(
                ok_window,
                "get_exe_by_hwnd",
                side_effect=lambda h: fake_windows[h][2:5],
            ),
            patch.object(
                ok_window,
                "get_window_bounds",
                return_value=(0, 0, 1936, 1119, 1920, 1080, 1.0),
            ),
        ):
            name, hwnd, full_path, _, _, width, height, _ = ok_window.find_hwnd(
                config["windows"].get("title"),
                config["windows"].get("exe"),
                0,
                0,
                player_id=-1,
                class_name=config["windows"].get("hwnd_class"),
                selected_hwnd=920818,
            )
            self.assertEqual(name, "AzurPromiliaJP")
            self.assertEqual(hwnd, jp_hwnd)
            self.assertEqual(full_path, jp_path)
            self.assertEqual((width, height), (1920, 1080))

            manager = object.__new__(DeviceManager)
            manager.windows_capture_config = dict(config["windows"])
            manager.device_dict = {}
            manager.config = {
                "preferred": "pc_920818",
                "pc_full_path": r"D:\AzurPromilia\AzurPromilia_Game\AzurPromilia.exe",
                "selected_exe": ["AzurPromilia.exe"],
                "selected_hwnd": 920818,
            }

            imei = manager.update_pc_device()
            self.assertEqual(imei, f"pc_{jp_hwnd}")
            self.assertEqual(manager.config["pc_full_path"], jp_path)
            device = manager.device_dict[imei]
            self.assertTrue(device["connected"])
            self.assertEqual(device["nick"], "AzurPromiliaJP")
            self.assertEqual(device["full_path"], jp_path)
            self.assertEqual(device["real_hwnd"], jp_hwnd)

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
