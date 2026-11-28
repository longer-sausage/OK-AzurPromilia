"""本机头像 PNG 和 UUID → 稳定 Char 名索引。"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from uuid import UUID, uuid4

import cv2
import numpy as np

from src.char import normalize_binding_name
from src.core.user_data import CHAR_DIR, LEGACY_CHAR_DIR
from src.image.team_portraits import PORTRAIT_SIZE


@dataclass(frozen=True, eq=False)
class CharTemplate:
    uuid: str
    char_name: str
    image: np.ndarray | None


class CharTemplateStore:
    # GUI 与战斗可能同时访问同一目录，即使使用了不同的 store 实例。
    _lock = RLock()

    def __init__(self, directory: Path = CHAR_DIR):
        self.directory = directory
        self.index = directory / "bindings.json"

    def _migrate_legacy_directory(self) -> None:
        """在存储锁内整体迁移默认头像库，保证 PNG 和绑定索引一起切换。"""
        if self.directory.exists() or self.directory.absolute() != CHAR_DIR.absolute() or not LEGACY_CHAR_DIR.is_dir():
            return
        try:
            LEGACY_CHAR_DIR.rename(self.directory)
        except (FileExistsError, FileNotFoundError):
            # 另一进程可能已完成同一次迁移。
            if not self.directory.is_dir():
                raise

    def _bindings(self) -> dict[str, str]:
        self._migrate_legacy_directory()
        try:
            data = json.loads(self.index.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f"Invalid template index: {self.index}")
        for key, name in data.items():
            if str(UUID(key)) != key or not (name is None or (isinstance(name, str) and name)):
                raise ValueError(f"Invalid template binding: {key!r}")
            data[key] = normalize_binding_name(name)
        return data

    def _write(self, bindings: dict[str, str]) -> None:
        path = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as temp:
                path = Path(temp.name)
                json.dump(bindings, temp, ensure_ascii=False, indent=2)
                temp.write("\n")
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(path, self.index)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def templates(self) -> tuple[CharTemplate, ...]:
        with self._lock:
            result = []
            for key, name in self._bindings().items():
                path = self.directory / f"{key}.png"
                try:
                    encoded = np.frombuffer(path.read_bytes(), np.uint8)
                    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR) if encoded.size else None
                except (FileNotFoundError, cv2.error):
                    image = None
                if image is not None and image.shape[:2] != (PORTRAIT_SIZE, PORTRAIT_SIZE):
                    image = cv2.resize(image, (PORTRAIT_SIZE, PORTRAIT_SIZE), interpolation=cv2.INTER_AREA)
                result.append(CharTemplate(key, name, image))
            return tuple(result)

    def add(self, portraits: list[tuple[np.ndarray, str | None]]) -> list[str]:
        """三个槽位作为一次操作提交。发布索引之前，识别端不会看到半批头像。"""
        if not portraits:
            return []
        with self._lock:
            bindings = self._bindings()
            self.directory.mkdir(parents=True, exist_ok=True)
            keys = []
            try:
                for image, name in portraits:
                    ok, png = cv2.imencode(".png", image)
                    if not ok:
                        raise ValueError("Cannot encode portrait")
                    key = str(uuid4())
                    with (self.directory / f"{key}.png").open("xb") as file:
                        keys.append(key)
                        file.write(png.tobytes())
                    bindings[key] = normalize_binding_name(name)
                self._write(bindings)
            except Exception:
                for key in keys:
                    (self.directory / f"{key}.png").unlink(missing_ok=True)
                raise
            return keys

    def bind(self, key: str, name: str | None) -> None:
        with self._lock:
            bindings = self._bindings()
            if key not in bindings:
                raise KeyError(key)
            bindings[key] = normalize_binding_name(name)
            self._write(bindings)

    def delete(self, key: str) -> None:
        with self._lock:
            bindings = self._bindings()
            if key not in bindings:
                return
            old_name = bindings.pop(key)
            self._write(bindings)
            try:
                (self.directory / f"{key}.png").unlink(missing_ok=True)
            except OSError:
                bindings[key] = old_name
                self._write(bindings)
                raise
