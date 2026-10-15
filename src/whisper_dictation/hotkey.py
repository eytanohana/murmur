"""Global Option key listener built on pynput.

Emits on_press when Option goes down, on_release when it comes up, and
on_other_key when any other key is pressed while Option is held.
"""

from __future__ import annotations

from collections.abc import Callable

from pynput import keyboard

LEFT = keyboard.Key.alt_l
RIGHT = keyboard.Key.alt_r
GENERIC = keyboard.Key.alt


def option_keys_for(setting: str) -> set:
    if setting == "left":
        return {LEFT, GENERIC}
    if setting == "right":
        return {RIGHT}
    return {LEFT, RIGHT, GENERIC}


class HotkeyListener:
    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_other_key: Callable[[], None],
        option_keys: str = "both",
    ):
        self._on_press = on_press
        self._on_release = on_release
        self._on_other_key = on_other_key
        self._keys = option_keys_for(option_keys)
        self._held: set = set()
        self._listener: keyboard.Listener | None = None

    def handle_press(self, key) -> None:
        if key in self._keys:
            was_empty = not self._held
            self._held.add(key)
            if was_empty:
                self._on_press()
        elif self._held:
            self._on_other_key()

    def handle_release(self, key) -> None:
        if key in self._keys and key in self._held:
            self._held.discard(key)
            if not self._held:
                self._on_release()

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self.handle_press, on_release=self.handle_release
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    def join(self) -> None:
        if self._listener:
            self._listener.join()
