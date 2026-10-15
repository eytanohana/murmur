"""Sounds and the optional menu bar item."""

from __future__ import annotations

import subprocess


def play_sound(path: str, enabled: bool = True) -> None:
    """Play a sound without blocking."""
    if not enabled:
        return
    try:
        subprocess.Popen(
            ["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        pass


class NullStatus:
    """Status sink used when the menu bar is off."""

    def set(self, state: str) -> None:
        pass


def make_menubar_status():
    """Build the rumps status item. Requires the `menubar` extra.

    rumps needs the main thread for its run loop, so the caller must run
    `status.run()` on the main thread and the dictation loop elsewhere.
    """
    import rumps

    titles = {"idle": "mic", "recording": "REC", "transcribing": "..."}

    class Status(rumps.App):
        def __init__(self):
            super().__init__("whisper-dictation", title=titles["idle"], quit_button="Quit")

        def set(self, state: str) -> None:
            self.title = titles.get(state, titles["idle"])

    return Status()
