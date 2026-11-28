import gettext
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

import cv2
import numpy as np
from ok import og
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src import char
from src.char.base_char import BaseChar
from src.char.starborn import Starborn
from src.combat.char_factory import CHAR_DISPLAY_NAMES
from src.combat.combat import Combat
from src.combat.team_manager import TeamManager
from src.config import config
from src.core.base_game_task import BaseGameTask
from src.data.char_templates import CharTemplateStore
from src.gui.character_tab import CharacterTab

ROOT = Path(__file__).resolve().parents[1]


def game_frame(name):
    path = ROOT / "ok_templates" / f"screenshot_{name}.png"
    return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)


class TestCharacterManagement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = game_frame("20261127_005435")

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "角色模板"
        self.store = CharTemplateStore(self.folder)
        self.manager = TeamManager(self.store)

    def test_three_slots_restart_resize_swap_and_real_combat_entry(self):
        modules = self.folder.parent / "char_modules"
        modules.mkdir()
        (modules / "portrait_fixture.py").write_text(dedent("""\
            from abc import ABC, abstractmethod
            from src.char.base_char import BaseChar
            from src.char.starborn import Starborn

            class PortraitFixture(BaseChar):
                def __init__(self, *args, tag=None, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.tag = tag

            class AbstractFixture(BaseChar, ABC):
                @abstractmethod
                def do_perform(self): ...

            class _PrivateFixture(BaseChar):
                pass

            Alias = PortraitFixture
            """), encoding="utf-8")
        self.enterContext(patch.object(char, "__path__", [*char.__path__, str(modules)]))
        self.enterContext(patch.object(char, "portrait_fixture", create=True))
        self.addCleanup(sys.modules.pop, "src.char.portrait_fixture", None)
        self.manager = TeamManager(self.store)
        for name in ("AbstractFixture", "_PrivateFixture", "Alias", "BaseChar"):
            self.assertIsNone(self.manager.factory.get_spec(name))

        old_frame = game_frame("20260918_224930")
        old_slots = self.manager.observe(old_frame, capture=True)
        self.assertEqual([slot.char_name for slot in old_slots], ["BaseChar"] * 3)
        self.assertEqual([slot.confidence for slot in old_slots], [0.0] * 3)
        self.assertEqual(json.loads(self.store.index.read_bytes()), {slot.template_id: "BaseChar" for slot in old_slots})
        for slot, name in zip(old_slots, ("Starborn", "PortraitFixture", "PortraitFixture"), strict=True):
            self.manager.bind(slot.template_id, name)
            self.assertEqual(UUID(slot.template_id).version, 4)
            self.assertTrue((self.folder / f"{slot.template_id}.png").is_file())
        self.assertEqual(json.loads(self.store.index.read_text(encoding="utf-8")),
                         {slot.template_id: slot.char_name for slot in self.manager.current_team})

        manager = TeamManager(CharTemplateStore(self.folder))
        fixture_cls = manager.factory.get_spec("PortraitFixture").cls
        manager.factory.register("PortraitFixture", fixture_cls, tag="test")
        task = BaseGameTask(SimpleNamespace(frame=self.frame, scene=None, debug=False), SimpleNamespace(tr=str))
        combat = Combat(task)
        combat.team_manager = manager
        task.find_best_match_in_box = task.find_one = Mock(side_effect=AssertionError("FeatureSet used"))
        task.info_set = Mock()
        # The same blue-haired portrait has moved from slot 1 in the CN screenshot to slot 2 in JP.
        for size in ((1920, 1080), (2560, 1440), (1280, 720)):
            with self.subTest(size=size):
                task.executor.frame = cv2.resize(self.frame, size)
                self.assertTrue(combat.load_chars())
                self.assertEqual([c.char_name for c in combat.chars], ["BaseChar", "Starborn", "BaseChar"])
                self.assertIsInstance(combat.chars[1], Starborn)
                self.assertIs(combat.has_char("Starborn"), combat.chars[1])
                self.assertTrue(combat.chars[0].is_current_char)
                self.assertGreater(combat.chars[1].confidence, 0.82)
        self.assertEqual(len(self.store.templates()), 3)
        # A correction on the JP appearance changes that new UUID only.
        new_slot = self.manager.observe(self.frame, capture=True)[1]
        self.assertNotEqual(new_slot.template_id, old_slots[0].template_id)
        self.assertEqual(new_slot.char_name, "Starborn")
        self.manager.bind(new_slot.template_id, "PortraitFixture")
        self.assertTrue(combat.load_chars())
        self.assertIsInstance(combat.chars[1], fixture_cls)
        self.assertEqual(combat.chars[1].tag, "test")
        self.assertEqual(self.manager.observe(old_frame)[0].char_name, "Starborn")
        # An explicit base binding on this appearance wins over a less similar named portrait.
        self.manager.bind(new_slot.template_id, "BaseChar")
        self.assertTrue(combat.load_chars())
        self.assertIs(type(combat.chars[1]), BaseChar)
        self.assertEqual(combat.chars[1].confidence, 0)
        self.assertEqual(self.manager.observe(old_frame)[0].char_name, "Starborn")
        for key in (new_slot.template_id, old_slots[0].template_id):
            self.manager.delete(key)
        self.assertTrue(combat.load_chars())
        self.assertEqual(combat.chars[1].char_name, "BaseChar")
        self.assertEqual(combat.chars[1].confidence, 0)

    def test_unimplemented_legacy_binding_survives_recapture_and_can_be_rebound(self):
        key = self.manager.observe(self.frame, capture=True)[0].template_id
        self.store.bind(key, "RetiredChar")
        index = self.store.index.read_bytes()
        image_file = self.folder / f"{key}.png"
        image = image_file.read_bytes()
        manager = TeamManager(CharTemplateStore(self.folder))
        with self.assertRaises(ValueError):
            manager.bind(key, "RetiredChar")
        combat = Combat(BaseGameTask(SimpleNamespace(frame=self.frame, scene=None), SimpleNamespace(tr=str)))
        current = manager.instantiate(combat, self.frame, 3, [])[0]
        self.assertIs(type(current), BaseChar)
        self.assertEqual((current.char_name, current.confidence), ("BaseChar", 0))
        slot = manager.observe(self.frame, capture=True)[0]
        self.assertEqual((slot.template_id, slot.char_name, slot.confidence), (key, "BaseChar", 0))
        self.assertEqual(self.store.index.read_bytes(), index)
        self.assertEqual(image_file.read_bytes(), image)

        manager.bind(key, "Starborn")
        current = manager.instantiate(combat, self.frame, 3, [])[0]
        self.assertIsInstance(current, Starborn)
        self.assertEqual(self.store.templates()[0].char_name, "Starborn")

    def test_legacy_unknown_bindings_resolve_to_base_and_normalize_on_save(self):
        keys = [slot.template_id for slot in self.manager.observe(self.frame, capture=True)]
        old_bindings = dict(zip(keys, (None, "unknown", "BaseChara"), strict=True))
        self.store.index.write_text(json.dumps(old_bindings), encoding="utf-8")
        manager = TeamManager(CharTemplateStore(self.folder))
        self.assertEqual([(t.uuid, t.char_name) for t in manager.store.templates()],
                         [(key, "BaseChar") for key in keys])
        team = manager.observe(self.frame, capture=True)
        self.assertEqual([(slot.template_id, slot.char_name, slot.confidence) for slot in team],
                         [(key, "BaseChar", 0) for key in keys])
        self.assertEqual(json.loads(self.store.index.read_bytes()), old_bindings)
        manager.bind(keys[0], None)
        self.assertEqual(json.loads(self.store.index.read_bytes()), dict.fromkeys(keys, "BaseChar"))

    def test_recapture_missing_images_multiple_appearances_and_partial_party(self):
        slots = self.manager.observe(self.frame, capture=True)
        self.assertEqual([s.template_id for s in self.manager.observe(self.frame, capture=True)],
                         [s.template_id for s in slots])
        for slot in slots[:2]:
            self.manager.bind(slot.template_id, "Starborn")
        (self.folder / f"{slots[0].template_id}.png").unlink()
        team = self.manager.observe(self.frame)
        self.assertEqual([s.char_name for s in team], ["BaseChar", "Starborn", "BaseChar"])
        # One slot remains in the HUD. Capturing the background must not create new templates.
        cropped = self.frame.copy()
        cropped[285:460, 1660:] = 0
        self.assertEqual(self.manager.team_state(cropped), (True, 0, 1))
        one = self.manager.observe(cropped, capture=True)
        self.assertEqual([s.template_id is not None for s in one], [True, False, False])
        self.assertEqual(len(self.store.templates()), 4)
        self.assertTrue(all(s.template_id is None for s in self.manager.observe(np.zeros_like(cropped), capture=True)))
        self.assertEqual(len(self.store.templates()), 4)

    def test_index_failure_is_recoverable(self):
        with patch.object(self.store, "_write", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.manager.observe(self.frame, capture=True)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.store.index.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.manager.observe(self.frame, capture=True)
        self.assertEqual(self.store.index.read_text(encoding="utf-8"), "broken")

    def test_tab_capture_edit_save_locale_restart_and_scrollable_delete(self):
        self.assertIn(["src.gui.character_tab", "CharacterTab"], config["custom_tabs"])
        self.enterContext(patch.dict(CHAR_DISPLAY_NAMES, {"RetiredChar": "阿比"}))
        app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
        translations = gettext.translation("ok", ROOT / "i18n", languages=["ja_JP"])
        with patch.object(og, "app", SimpleNamespace(tr=translations.gettext)):
            tab = CharacterTab(self.manager)
            tab.resize(700, 460)
            tab.show()
            self.addCleanup(tab.close)
            tab.executor = SimpleNamespace(device_manager=SimpleNamespace(capture_method=None))
            tab.capture_button.click()
            self.assertTrue(tab.message.isVisible())
            capture = Mock()
            capture.get_frame.return_value = self.frame
            tab.executor.device_manager.capture_method = capture
            foreground = self.enterContext(patch("src.core.game_window.win32gui.GetForegroundWindow", return_value=12))
            self.enterContext(patch("src.core.game_window.win32gui.IsIconic", return_value=False))
            window = Mock(hwnd=73, top_hwnd=73)
            window.bring_to_front.side_effect = lambda: setattr(foreground, "return_value", 73)
            tab.executor.device_manager.hwnd_window = window
            loop = QEventLoop()
            tab.capture_events.finished.connect(loop.quit)
            tab.capture_button.click()
            QTimer.singleShot(5000, loop, loop.quit)
            loop.exec()
            capture.get_frame.assert_called_once()
            window.bring_to_front.assert_called_once()
            self.assertEqual(tab.table.rowCount(), 3)
            tab.executor.device_manager.hwnd_window = None
            tab.capture_button.click()
            QTimer.singleShot(5000, loop, loop.quit)
            loop.exec()
            self.assertIn("最前面", tab.message.text())
            self.assertEqual(tab.table.rowCount(), 3)
            capture.get_frame.assert_called_once()
            card = tab.cards[0]
            self.store.bind(card.slot.template_id, "RetiredChar")
            tab.refresh_library()
            self.assertEqual(tab.table.item(0, 1).text(), translations.gettext("阿比"))
            self.assertEqual(card.name_label.text(), "デフォルト")
            card.edit_button.click()
            self.assertTrue(card.selector.isVisible())
            self.assertEqual(card.edit_button.text(), "保存")
            self.assertEqual(card.selector.currentData(), "BaseChar")
            self.assertEqual(card.selector.currentText(), "デフォルト")
            self.assertEqual(card.selector.findData("RetiredChar"), -1)
            self.assertEqual(card.selector.count(), len(self.manager.factory.characters) + 1)
            card.selector.setCurrentIndex(card.selector.findData("Starborn"))
            self.assertEqual(card.selector.currentText(), "星臨者")
            card.edit_button.click()
            self.assertTrue(card.selector.isHidden())
            self.assertEqual(card.name_label.text(), "星臨者")
            self.assertEqual(card.confidence_label.text(), "一致度 100%")
            self.assertEqual(self.store.templates()[0].char_name, "Starborn")
            self.store.add([(card.slot.portrait, None)] * 8)
            tab.refresh_library()
            app.processEvents()
            self.assertGreater(tab.table.verticalScrollBar().maximum(), 0)
            self.assertEqual(tab.verticalScrollBar().maximum(), 0)
            button = tab.table.cellWidget(0, 3)
            self.assertGreaterEqual(button.width(), button.sizeHint().width())
            tab.close()

        translations = gettext.translation("ok", ROOT / "i18n", languages=["en_US"])
        with patch.object(og, "app", SimpleNamespace(tr=translations.gettext)):
            manager = TeamManager(CharTemplateStore(self.folder))
            manager.observe(self.frame)
            reopened = CharacterTab(manager)
            reopened.show()
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.table.item(0, 1).text(), "Starborn")
            key = self.store.templates()[0].uuid
            card = reopened.cards[0]
            card.edit_button.click()
            card.selector.setCurrentIndex(card.selector.findData("BaseChar"))
            card.edit_button.click()
            self.assertEqual(card.name_label.text(), "Default")
            self.assertEqual(reopened.table.item(0, 1).text(), "Default")
            self.assertEqual(card.slot.confidence, 0)
            self.assertEqual(json.loads(self.store.index.read_bytes())[key], "BaseChar")
            reopened.table.cellWidget(0, 3).click()
            self.assertFalse((self.folder / f"{key}.png").exists())
            self.assertNotIn(key, json.loads(self.store.index.read_text(encoding="utf-8")))
