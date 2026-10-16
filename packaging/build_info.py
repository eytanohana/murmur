"""Facts the app bundle takes from the build environment."""

from __future__ import annotations

import re
from importlib.metadata import distribution


def mlx_min_macos(default: str = "15.0") -> str:
    """The macOS version the installed mlx wheel was built for (its WHEEL platform tag).

    uv installs the mlx wheel matching the build machine's macOS (macosx_14_0, 15_0 or
    26_0), and the bundle only runs on that version or newer, so Info.plist says so.
    """
    for line in (distribution("mlx").read_text("WHEEL") or "").splitlines():
        match = re.match(r"Tag: .*macosx_(\d+)_\d+_arm64", line)
        if match:
            return f"{match.group(1)}.0"
    return default
