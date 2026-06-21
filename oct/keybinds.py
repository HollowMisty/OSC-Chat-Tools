"""Global keybind management (wraps the ``keyboard`` library)."""
from __future__ import annotations

from typing import Callable

import keyboard


class KeybindManager:
    """Register named global hotkeys with callbacks; rebindable at runtime."""

    def __init__(self):
        self._hooks: dict[str, object] = {}

    def bind(self, name: str, key: str, callback: Callable[[], None]) -> None:
        self.unbind(name)
        if key:
            try:
                self._hooks[name] = keyboard.add_hotkey(key, callback)
            except Exception:
                pass

    def unbind(self, name: str) -> None:
        hook = self._hooks.pop(name, None)
        if hook is not None:
            try:
                keyboard.remove_hotkey(hook)
            except Exception:
                pass

    def unbind_all(self) -> None:
        for name in list(self._hooks):
            self.unbind(name)

    @staticmethod
    def is_pressed(key: str) -> bool:
        try:
            return keyboard.is_pressed(key)
        except Exception:
            return False
