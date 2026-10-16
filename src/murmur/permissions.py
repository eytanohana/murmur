"""Startup permission checks. They also trigger the macOS prompts, which matters under launchd."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from .agent import real_python
from .frozen import bundle_path, is_frozen
from .inserter import accessibility_trusted

log = logging.getLogger("murmur")


def permission_target() -> str:
    """What the user must find in System Settings: murmur.app when frozen, else the python."""
    app = bundle_path() if is_frozen() else None
    if app is not None:
        return f"murmur.app ({app})"
    return str(real_python(Path(sys.executable)))


def grant_hint(pane: str, target: str) -> str:
    """How to grant a permission. An app shows up in the lists once it has asked."""
    if target.startswith("murmur.app"):
        return f"Turn on {target} under System Settings > Privacy & Security > {pane}"
    return f"Add {target} (or the terminal app running this) under System Settings > Privacy & Security > {pane}"


def check_microphone(sample_rate: int = 16000) -> None:
    """Open the input briefly so macOS shows the Microphone prompt at startup.

    Under launchd there is no terminal and no first recording to trigger it.
    A denied microphone delivers only exact zeros.
    """
    chunks: list = []
    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            callback=lambda indata, *_: chunks.append(indata.copy()),
        ):
            time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001  any open failure means no usable microphone
        log.warning(
            "Could not open the microphone (%s). %s.",
            exc,
            grant_hint("Microphone", permission_target()),
        )
        return
    if not chunks or not np.any(np.concatenate(chunks)):
        log.warning(
            "The microphone returned only silence. If a permission prompt appeared, "
            "allow it and restart. Otherwise: %s.",
            grant_hint("Microphone", permission_target()),
        )


def check_permissions() -> None:
    """Warn about missing macOS permissions and trigger the system prompts.

    The prompts are requested for this process, so they also appear when it runs
    from launchd with no terminal attached. Warnings name the real python binary.
    """
    target = permission_target()
    if not accessibility_trusted(prompt=True):
        log.warning(
            "Accessibility not granted (AXIsProcessTrusted is false). Typing and "
            "pasting are silently dropped without it. %s, then restart.",
            grant_hint("Accessibility", target),
        )
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
            log.warning(
                "Input Monitoring not granted. %s, then restart.",
                grant_hint("Input Monitoring", target),
            )
    except (ImportError, AttributeError):
        pass
    check_microphone()
