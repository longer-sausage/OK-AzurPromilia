"""An isolated, best-effort WGC readback for the first pickup check."""

from __future__ import annotations

import ctypes

import cv2
import numpy as np
import win32gui
from ok.capture.windows import d3d11
from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod
from ok.rotypes import IUnknown
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)
WAIT_SECONDS = 0.08


class _D3D11Box(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint) for name in ('left', 'top', 'front', 'right', 'bottom', 'back')]


def supported(method):
    # Avoid HDR conversion, HWND composition and client-origin ambiguity. A
    # decorated or resized window simply keeps the existing full-frame path.
    if type(method) is not WindowsGraphicsCaptureMethod or not hasattr(method, 'get_pickup_region'):
        return False
    window = method.hwnd_window
    size = method.last_size
    hwnd = getattr(window, 'hwnd', 0)
    if not hwnd or method.capture_hwnd != hwnd or method.frame_pool is None or not size:
        return False
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, cw, ch = win32gui.GetClientRect(hwnd)
        return ((cx, cy) == (left, top) and (right - left, bottom - top) == (cw, ch)
                and (cw, ch) == (size.Width, size.Height) and cw > 0 and ch > 0
                and not win32gui.GetWindow(hwnd, 5))  # GW_CHILD: no HWND composition needed
    except win32gui.error:
        return False


def _convert_region(method, frame, region):
    x, y, right, bottom = region
    if (frame.ContentSize.Width != method.last_size.Width or frame.ContentSize.Height != method.last_size.Height
            or not (0 <= x < right <= frame.ContentSize.Width and 0 <= y < bottom <= frame.ContentSize.Height)):
        return np.empty((0, 0, 3), dtype=np.uint8)
    width, height = right - x, bottom - y
    tex = frame.Surface.astype(method.IDirect3DDxgiInterfaceAccess).GetInterface(
        d3d11.ID3D11Texture2D.GUID).astype(d3d11.ID3D11Texture2D)
    try:
        desc = tex.GetDesc()
        # DXGI_FORMAT_B8G8R8A8_UNORM (87), the SDR WGC format.
        if desc.Format != 87 or desc.SampleDesc.Count != 1 or right > desc.Width or bottom > desc.Height:
            return np.empty((0, 0, 3), dtype=np.uint8)
        key = (width, height, desc.Format)
        if getattr(method, '_pickup_staging_key', None) != key:
            _release_staging(method)
            desc.Width, desc.Height = width, height
            desc.Usage = d3d11.D3D11_USAGE_STAGING
            desc.CPUAccessFlags = d3d11.D3D11_CPU_ACCESS_READ
            desc.BindFlags = desc.MiscFlags = 0
            method._pickup_staging = method.dxdevice.CreateTexture2D(ctypes.byref(desc), None)
            method._pickup_staging_key = key
        cpu = method._pickup_staging
        context = method.immediatedc
        box = _D3D11Box(x, y, 0, right, bottom, 1)
        context.CopySubresourceRegion(cpu, 0, 0, 0, 0, tex, 0, ctypes.byref(box))
        mapped = context.Map(cpu, 0, d3d11.D3D11_MAP_READ, 0)
        try:
            pixels = np.ctypeslib.as_array(ctypes.cast(mapped.pData, ctypes.POINTER(ctypes.c_ubyte)),
                                          (height, mapped.RowPitch // 4, 4))
            return cv2.cvtColor(pixels[:, :width], cv2.COLOR_BGRA2BGR)
        finally:
            context.Unmap(cpu, 0)
    finally:
        tex.Release()


def _release_staging(method):
    staging = getattr(method, '_pickup_staging', None)
    method._pickup_staging = None
    method._pickup_staging_key = None
    if staging is not None:
        staging.Release()


def _get_region(method, region):
    if not method.get_frame_lock.acquire(blocking=False):
        return None
    try:
        if method.exit_event.is_set() or not method.start_or_stop() or not supported(method):
            return None
        try:
            with method.lock:
                generation = method._frame_cancel_generation
                method._pickup_region = region
                method.last_frame = None
                method.frame_event.clear()
                method.frame_requested.set()
            method.frame_event.wait(WAIT_SECONDS)
            with method.lock:
                frame = method.last_frame if generation == method._frame_cancel_generation else None
                return frame if frame is not None and frame.size else None
        finally:
            with method.lock:
                method.frame_requested.clear()
                method.frame_event.clear()
                method.last_frame = None
                method._pickup_region = None
    finally:
        method.get_frame_lock.release()


def install_pickup_wgc_region():
    cls = WindowsGraphicsCaptureMethod
    if hasattr(cls, 'get_pickup_region'):
        return
    copy_region = ctypes.WINFUNCTYPE(
        None, IUnknown, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
        IUnknown, ctypes.c_uint, ctypes.POINTER(_D3D11Box),
    )(46, 'CopySubresourceRegion')

    def copy_subresource_region(context, *args):
        # ctypes COM callables do not bind the instance when stored on a class.
        return copy_region(context, *args)

    d3d11.ID3D11DeviceContext.CopySubresourceRegion = copy_subresource_region
    convert, close, create_device = cls.convert_dx_frame, cls.close, cls.create_device

    def recreate_device(self):
        with self.lock:
            _release_staging(self)
            return create_device(self)

    def convert_frame(self, frame):
        region = getattr(self, '_pickup_region', None)
        if region is None:
            return convert(self, frame)
        try:
            return _convert_region(self, frame, region)
        except Exception as error:
            logger.debug(f'WGC pickup-region readback failed: {error}')
            # Signal completion immediately; the executor will use full capture.
            return np.empty((0, 0, 3), dtype=np.uint8)

    def close_capture(self):
        # Let upstream cancel an in-flight capture before waiting on its lock.
        try:
            return close(self)
        finally:
            with self.lock:
                if self.frame_pool is None:
                    _release_staging(self)

    cls.convert_dx_frame = convert_frame
    cls.create_device = recreate_device
    cls.close = close_capture
    cls.get_pickup_region = _get_region
