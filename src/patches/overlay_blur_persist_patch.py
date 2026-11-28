"""Persist UID patches on the consumer only; keep ok's foreground visibility gate."""

from functools import wraps

import numpy as np
from ok import og
from ok.ui.overlay import Win32GdiOverlay
from ok.util.blur import get_blur_rects

_PATCH_INSTALLED = False


def _processor():
    return getattr(getattr(og, "executor", None), "blur_overlay_processor", None)


def install_overlay_blur_persist_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    cls = Win32GdiOverlay
    update = cls.update_overlay
    receive = cls.update_blur_patches
    clear = cls.clear_blur_patches
    poll = cls._poll_input

    @wraps(update)
    def update_overlay(self, visible, x, y, window_width, window_height, width, height, scaling):
        with self._lock:
            saved = getattr(self, "_ap_uid_cache", None)
            proc = _processor()
            if saved and (proc is None or not proc.enabled()):
                self._ap_uid_cache = saved = None
                self.blur_images = []
            if saved and visible and width > 0 and height > 0:
                dimensions, patches = saved
                if dimensions != (width, height):
                    # Cover the resized UID until a frame at the new size arrives.
                    patches = [(a, b, w, h, np.zeros((h, w, 3), dtype=np.uint8))
                               for a, b, w, h in get_blur_rects(proc.blur_area, width, height)]
                    self._ap_uid_cache = ((width, height), patches)
                self.blur_images = patches
            # Upstream hides the window and clears active drawing, not our cache.
            return update(self, visible, x, y, window_width, window_height, width, height, scaling)

    @wraps(receive)
    def update_blur_patches(self, patches):
        with self._lock:
            proc = _processor()
            if self._closed or proc is None or not proc.enabled():
                self._ap_uid_cache = None
                return clear(self)
            width, height = self._width, self._height
            # A queued worker result can trail a resize/hide.
            if not self._source_visible or not patches:
                return None
            if [p[:4] for p in patches] != get_blur_rects(proc.blur_area, width, height):
                return None
            self._ap_uid_cache = ((width, height), list(patches))
            return receive(self, patches)

    @wraps(clear)
    def clear_blur_patches(self):
        with self._lock:
            proc = _processor()
            if proc is not None and proc.enabled() and getattr(self, "_ap_uid_cache", None):
                # A queued clear from focus loss may arrive after reactivation.
                return None
            self._ap_uid_cache = None
            return clear(self)

    @wraps(poll)
    def poll_input(self):
        # No frame/window event is guaranteed while idle. Observe the switch in
        # the overlay's existing input poll, without another thread or capture.
        with self._lock:
            if getattr(self, "_ap_uid_cache", None):
                proc = _processor()
                if proc is None or not proc.enabled():
                    self._ap_uid_cache = None
                    clear(self)
                    # If no frame arrived while switched off, ok's worker still
                    # considers its last patch published. Invalidate that comparison
                    # so the same scene can be published again when re-enabled.
                    if proc is not None:
                        proc.last_patches = None
        return poll(self)

    cls.update_overlay = update_overlay
    cls.update_blur_patches = update_blur_patches
    cls.clear_blur_patches = clear_blur_patches
    cls._poll_input = poll_input
    _PATCH_INSTALLED = True
