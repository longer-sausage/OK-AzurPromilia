from __future__ import annotations

import logging

_PATCH_INSTALLED = False
_original_start_device = None
logger = logging.getLogger(__name__)


def _start_device_with_args(self, initial_refresh_done=False):
    """Patch start_device to support config['windows']['args']."""
    from ok import og
    from ok.util.process import execute, is_admin
    from ok.core.events import communicate

    device = og.device_manager.get_preferred_device()
    logger.info(f'start_device: {device}')

    if device and not device['connected']:
        if device['device'] == "windows" and not is_admin():
            communicate.starting_emulator.emit(True,
                                               "PC version requires admin privileges, Please restart this app with admin privileges!",
                                               0)
            communicate.restart_admin.emit()
            return False
        path = og.device_manager.get_exe_path(device)
        if path:
            logger.info(f"starting game {path}")
            args = None

            # DX11
            dx11_config = og.global_config.get_config('Launch with DX11')
            if dx11_config and dx11_config.get('Launch with DX11'):
                args = "-dx11 -d3d11 -force-d3d11"

            # config['windows']['args']
            windows_args = self.config.get('windows', {}).get('args')
            if windows_args:
                extra = ' '.join(windows_args) if isinstance(windows_args, list) else str(windows_args)
                args = f"{args} {extra}".strip() if args else extra

            if not execute(path, arguments=args, start_method=self.start_method):
                communicate.starting_emulator.emit(True, self.tr("Start game failed, please start game first"), 0)
                return False
            if device['device'] == "windows" and not self._wait_until_started_window_stable():
                return False
            if not self._wait_until_device_ready():
                return False
        else:
            communicate.starting_emulator.emit(True,
                                               self.tr('Game path does not exist, Please open game manually!'), 0)
            return False
    elif not self._wait_until_device_ready(refresh_first=not initial_refresh_done):
        return False
    communicate.starting_emulator.emit(True, None, 0)
    return True


def install_launch_args_patch():
    global _PATCH_INSTALLED, _original_start_device
    if _PATCH_INSTALLED:
        return
    try:
        from ok.gui.StartController import StartController
        _original_start_device = StartController.start_device
        StartController.start_device = _start_device_with_args
        _PATCH_INSTALLED = True
    except Exception:
        pass
