"""Insert text at the cursor of the focused app using Quartz keyboard events.

Events are posted with CGEventPost, which is safe from any thread (pynput's
Controller.type calls input-source APIs that must run on the main thread on
recent macOS). Each event gets its flags set explicitly, so a physically held
Option key does not leak into the typed characters. macOS silently drops posted
events when the process lacks Accessibility permission, so trust is checked and
logged.
"""

from __future__ import annotations

import logging
import subprocess
import time

import Quartz

from .watchdog import watchdog

log = logging.getLogger("whisper_dictation")

CHUNK_UNITS = 20  # UTF-16 code units per synthetic key event
KEYCODE_V = 9
KEYCODE_RETURN = 36


def accessibility_trusted(prompt: bool = False) -> bool:
    """True if this process may post key events (AXIsProcessTrusted)."""
    import ApplicationServices as ax

    if prompt:
        options = {ax.kAXTrustedCheckOptionPrompt: True}
        return bool(ax.AXIsProcessTrustedWithOptions(options))
    return bool(ax.AXIsProcessTrusted())


def utf16_chunks(text: str, limit: int = CHUNK_UNITS) -> list[str]:
    """Split text so each piece is at most `limit` UTF-16 units, never inside a pair."""
    chunks: list[str] = []
    cur: list[str] = []
    units = 0
    for ch in text:
        n = 2 if ord(ch) > 0xFFFF else 1
        if units + n > limit:
            chunks.append("".join(cur))
            cur, units = [], 0
        cur.append(ch)
        units += n
    if cur:
        chunks.append("".join(cur))
    return chunks


def _post(source, keycode: int, down: bool, flags: int, text: str | None = None) -> None:
    ev = Quartz.CGEventCreateKeyboardEvent(source, keycode, down)
    if text is not None:
        Quartz.CGEventKeyboardSetUnicodeString(ev, len(text.encode("utf-16-le")) // 2, text)
    Quartz.CGEventSetFlags(ev, flags)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _source():
    # Private state: the event source does not inherit held modifier keys.
    return Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)


def post_text(text: str) -> int:
    """Type text as Unicode key events with no modifier flags. Returns events posted."""
    source = _source()
    posted = 0
    for chunk in utf16_chunks(text):
        if chunk == "\n":
            _post(source, KEYCODE_RETURN, True, 0)
            _post(source, KEYCODE_RETURN, False, 0)
        else:
            _post(source, 0, True, 0, chunk)
            _post(source, 0, False, 0, chunk)
        posted += 2
        time.sleep(0.002)
    return posted


def post_cmd_v() -> None:
    source = _source()
    flags = Quartz.kCGEventFlagMaskCommand
    _post(source, KEYCODE_V, True, flags)
    _post(source, KEYCODE_V, False, flags)


def get_clipboard() -> bytes:
    return subprocess.run(["pbpaste"], capture_output=True, check=False).stdout


def set_clipboard(data: bytes) -> None:
    subprocess.run(["pbcopy"], input=data, check=False)


class Inserter:
    def __init__(self, mode: str = "paste", restore_delay_ms: int = 150):
        self.mode = mode
        self.restore_delay = restore_delay_ms / 1000
        self._warned = False

    def _check_trust(self) -> None:
        if not self._warned and not accessibility_trusted():
            self._warned = True
            log.warning("Accessibility is not granted, so macOS silently drops typed and "
                        "pasted keys. Grant it to your terminal in System Settings > "
                        "Privacy & Security > Accessibility, then restart the terminal.")

    def insert(self, text: str) -> None:
        if not text:
            return
        if self.mode == "type":
            self.type_text(text)
        else:
            self._paste(text)

    def type_text(self, text: str) -> None:
        """Type with synthetic key events. Used for streamed chunks (no clipboard)."""
        if not text:
            return
        self._check_trust()
        with watchdog("insert"):
            posted = post_text(text)
        log.info("insert (type) %r -> %d key events posted", text, posted)

    def _paste(self, text: str) -> None:
        # pbpaste/pbcopy only round-trip plain text; rich clipboard content is lost.
        self._check_trust()
        with watchdog("insert"):
            previous = get_clipboard()
            set_clipboard(text.encode("utf-8"))
            post_cmd_v()
            time.sleep(self.restore_delay)
            set_clipboard(previous)
        log.info("insert (paste) %r -> Cmd+V posted", text)
