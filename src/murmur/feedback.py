"""Start and stop sounds."""

from __future__ import annotations

import subprocess


def play_sound(path: str, enabled: bool = True) -> None:
    """Play a sound without blocking."""
    if not enabled:
        return
    try:
        subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class NullStatus:
    """Status sink used when no indicator is configured."""

    def set(self, state: str) -> None:
        pass
