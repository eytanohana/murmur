"""Facts about running inside the PyInstaller-built murmur.app."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_path(executable: str | None = None) -> Path | None:
    """The .app folder when `executable` is Contents/MacOS/<name> inside one, else None."""
    exe = Path(executable or sys.executable).resolve()
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        app = exe.parent.parent.parent
        if app.suffix == ".app":
            return app
    return None
