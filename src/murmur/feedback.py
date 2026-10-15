"""Start and stop sounds."""

from __future__ import annotations

import subprocess


def play_sound(path: str) -> None:
    """Play a sound without blocking."""
    try:
        subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
