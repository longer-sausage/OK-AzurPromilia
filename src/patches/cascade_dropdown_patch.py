"""Display grouped choices as submenus while keeping the native drop_down schema."""

from functools import wraps

from ok import og
from ok.ui.qt.kit.buttons import Button
from ok.ui.qt.tasks import config_form
from ok.ui.qt.tasks.config_editors import ConfigEditor
from PySide6.QtWidgets import QMenu

_PATCH_INSTALLED = False


class GroupedDropDownEditor(ConfigEditor):
    def __init__(self, description, config, key, groups):
        self.button = Button()
        super().__init__(description, config, key, self.button)
        menu = QMenu(self.button)
        for label, values in groups.items():
            submenu = menu.addMenu(og.app.tr(label))
            for value in values:
                action = submenu.addAction(og.app.tr(value))
                action.setData(value)
        menu.triggered.connect(self._select)
        self.button.setMenu(menu)
        self.update_value()

    def _select(self, action):
        value = action.data()
        if value is not None:
            self.update_config(value)
            self.update_value()

    def update_value(self):
        self.button.setText(og.app.tr(self.config[self.key]))


def install_cascade_dropdown_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    original = config_form.config_editor

    @wraps(original)
    def config_editor(config_type, config_desc, config, key, value, task):
        spec = (config_type or {}).get(key)
        if isinstance(spec, dict) and spec.get("type") == "drop_down" and spec.get("option_groups"):
            return GroupedDropDownEditor(config_desc, config, key, spec["option_groups"])
        return original(config_type, config_desc, config, key, value, task)

    config_form.config_editor = config_editor
    _PATCH_INSTALLED = True
