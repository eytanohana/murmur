"""Global Option key listener built on pynput.

Callbacks run on a dedicated dispatcher thread, never on the pynput listener
thread, so a slow callback (opening the microphone, a lock) cannot stall the
system-wide event tap. Key events injected by software (our own typing) never
count as "other key" cancels.

Emits on_press when Option goes down, on_release when it comes up, and
on_other_key when any other key is pressed while Option is held.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from pynput import keyboard

log = logging.getLogger("murmur")

LEFT = keyboard.Key.alt_l
RIGHT = keyboard.Key.alt_r
GENERIC = keyboard.Key.alt


# Keyed by the valid option_keys values in config.OPTION_KEYS.
OPTION_KEY_SETS = {
    "left": frozenset({LEFT, GENERIC}),
    "right": frozenset({RIGHT}),
    "both": frozenset({LEFT, RIGHT, GENERIC}),
}


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
        self._keys = OPTION_KEY_SETS[option_keys]
        self._held: set = set()
        self._dispatch: Callable[[Callable[[], None]], None] = lambda fn: fn()
        self._queue: queue.Queue = queue.Queue()
        self._listener: keyboard.Listener | None = None

    def handle_press(self, key, injected: bool = False) -> None:
        if key in self._keys:
            was_empty = not self._held
            self._held.add(key)
            if was_empty:
                log.info("Option down")
                self._dispatch(self._on_press)
        elif self._held and not injected:
            log.info("Other key pressed while Option held")
            self._dispatch(self._on_other_key)

    def handle_release(self, key, injected: bool = False) -> None:
        if key in self._keys and key in self._held:
            self._held.discard(key)
            if not self._held:
                log.info("Option up")
                self._dispatch(self._on_release)

    def _worker(self) -> None:
        while True:
            fn = self._queue.get()
            if fn is None:
                return
            try:
                fn()
            except Exception:
                log.exception("Hotkey callback failed")

    def start(self) -> None:
        self._dispatch = self._queue.put
        threading.Thread(target=self._worker, daemon=True).start()
        self._listener = keyboard.Listener(
            on_press=self.handle_press, on_release=self.handle_release
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
        self._queue.put(None)
