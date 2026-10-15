"""Insert text at the cursor of the focused app."""

from __future__ import annotations

import subprocess
import time

from pynput import keyboard


def get_clipboard() -> bytes:
    return subprocess.run(["pbpaste"], capture_output=True, check=False).stdout


def set_clipboard(data: bytes) -> None:
    subprocess.run(["pbcopy"], input=data, check=False)


class Inserter:
    def __init__(self, mode: str = "paste", restore_delay_ms: int = 150):
        self.mode = mode
        self.restore_delay = restore_delay_ms / 1000
        self._kb = keyboard.Controller()

    def insert(self, text: str) -> None:
        if not text:
            return
        if self.mode == "type":
            self._kb.type(text)
        else:
            self._paste(text)

    def _paste(self, text: str) -> None:
        # pbpaste/pbcopy only round-trip plain text; rich clipboard content is lost.
        previous = get_clipboard()
        set_clipboard(text.encode("utf-8"))
        with self._kb.pressed(keyboard.Key.cmd):
            self._kb.press("v")
            self._kb.release("v")
        time.sleep(self.restore_delay)
        set_clipboard(previous)
