"""Save box metadata alongside the original screenshot produced by ok-script."""

import json
from functools import wraps
from pathlib import Path

from ok import Logger
from ok.core.screenshot import Screenshot

from src.patches.screenshot_sidecar import build_sidecar, serialize_boxes, sidecar_path_of

_PATCH_INSTALLED = False
logger = Logger.get_logger(__name__)


def install_screenshot_sidecar_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    original = Screenshot.generate_screen_shot

    @wraps(original)
    def generate(self, frame, ui_dict, folder, name, show_box, frame_box, processor=None):
        if not folder or not show_box or not ui_dict:
            return original(self, frame, ui_dict, folder, name, show_box, frame_box, processor)
        snapshot = ui_dict.copy()
        x_offset, y_offset = (-frame_box.x, -frame_box.y) if frame_box is not None else (0, 0)
        filename = original(self, frame, snapshot, folder, name, False, frame_box, processor)
        if filename:
            path = Path(filename)
            sidecar = sidecar_path_of(path)
            try:
                boxes = serialize_boxes(snapshot, x_offset, y_offset)
                sidecar.write_text(json.dumps(build_sidecar(path.name, boxes), ensure_ascii=False, indent=2),
                                   encoding="utf-8")
            except (OSError, TypeError, ValueError, AttributeError) as exc:
                logger.warning(f"write box sidecar failed: {sidecar} ({exc})")
        return filename

    Screenshot.generate_screen_shot = generate
    _PATCH_INSTALLED = True
