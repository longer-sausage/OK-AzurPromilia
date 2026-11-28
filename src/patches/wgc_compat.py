"""Permit a normal, bordered WGC probe on supported pre-20348 Windows builds."""

from functools import wraps

import ok
from ok.device.capture_methods import update
from ok.util import window
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)


def _has_window_capture_factory():
    from ok.rotypes.roapi import GetActivationFactory
    from ok.rotypes.Windows.Graphics.Capture import IGraphicsCaptureItemInterop

    return bool(GetActivationFactory('Windows.Graphics.Capture.GraphicsCaptureItem').astype(
        IGraphicsCaptureItemInterop))


def install_wgc_compat():
    original = window.windows_graphics_available
    if getattr(original, '_ok_ap_bordered_wgc', False):
        return

    @wraps(original)
    def available():
        if not window.WGC_MIN_BUILD <= window.WINDOWS_BUILD_NUMBER < window.WGC_NO_BORDER_MIN_BUILD:
            return original()
        try:
            return _has_window_capture_factory()
        except Exception as error:
            logger.warning(f'Bordered WGC unavailable: {error}')
            return False

    available._ok_ap_bordered_wgc = True
    window.windows_graphics_available = available
    update.windows_graphics_available = available
    # OK resolves this name lazily, but another importer may have cached it.
    ok.windows_graphics_available = available
