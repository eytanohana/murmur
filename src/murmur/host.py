"""The main-thread owner: one NSApplication run loop hosting the pill and the status item."""

from __future__ import annotations

import signal
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .config import Config
from .indicator import Indicator, NullIndicator, Overlay


class Part(Protocol):
    def build(self) -> None: ...

    def set(self, state: str) -> None: ...


class Host:
    """Runs AppKit on the main thread while dictation works on other threads.

    set() fans a state out to every part from any thread. SIGTERM and SIGINT
    terminate the app, which exits with status 0.
    """

    def __init__(self, parts: list[Part]):
        self.parts = parts
        self.app = None

    def set(self, state: str) -> None:
        for part in self.parts:
            part.set(state)

    def run(self) -> None:
        import AppKit

        self.app = AppKit.NSApplication.sharedApplication()
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        for part in self.parts:
            part.build()
        install_signal_handlers(self.stop)
        # A repeating timer returns control to Python so signal handlers run.
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, lambda _t: None)
        self.app.run()

    def stop(self) -> None:
        if self.app is not None:
            self.app.terminate_(None)


def install_signal_handlers(stop: Callable[[], None]) -> None:
    """SIGTERM (launchd) and SIGINT end the run loop with a clean exit status."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop())


def make_indicator(
    cfg: Config, config_path: Path, on_restart: Callable[[], None] = lambda: None
) -> Indicator:
    """Pill and/or status item per the config; a null indicator when both are off."""
    parts: list[Part] = []
    if cfg.indicator == "overlay":
        parts.append(Overlay(cfg))
    if cfg.menubar_icon:
        from .statusitem import StatusItem

        parts.append(StatusItem(cfg, config_path, on_restart))
    return Host(parts) if parts else NullIndicator()
