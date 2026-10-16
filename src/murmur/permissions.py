"""Startup permission checks. They also trigger the macOS prompts, which matters under launchd."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from .agent import real_python
from .inserter import accessibility_trusted

log = logging.getLogger("murmur")


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
            "Could not open the microphone (%s). Grant Microphone access to %s in "
            "System Settings > Privacy & Security.",
            exc,
            real_python(Path(sys.executable)),
        )
        return
    if not chunks or not np.any(np.concatenate(chunks)):
        log.warning(
            "The microphone returned only silence. If a permission prompt appeared, "
            "allow it and restart. Otherwise grant Microphone access to %s in System "
            "Settings > Privacy & Security.",
            real_python(Path(sys.executable)),
        )


def check_permissions() -> None:
    """Warn about missing macOS permissions and trigger the system prompts.

    The prompts are requested for this process, so they also appear when it runs
    from launchd with no terminal attached. Warnings name the real python binary.
    """
    binary = real_python(Path(sys.executable))
    if not accessibility_trusted(prompt=True):
        log.warning(
            "Accessibility not granted (AXIsProcessTrusted is false). Typing and "
            "pasting are silently dropped without it. Add %s (or the terminal app "
            "running this) under System Settings > Privacy & Security > "
            "Accessibility, then restart.",
            binary,
        )
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
            log.warning(
                "Input Monitoring not granted. Add %s (or the terminal app) under "
                "System Settings > Privacy & Security > Input Monitoring, then "
                "restart.",
                binary,
            )
    except (ImportError, AttributeError):
        pass
    check_microphone()
