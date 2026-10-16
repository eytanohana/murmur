"""Start and stop sounds."""

from __future__ import annotations

import contextlib
import subprocess


def play_sound(path: str) -> None:
    """Play a sound without blocking."""
    with contextlib.suppress(OSError):
        subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
