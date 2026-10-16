"""The main-thread owner: one NSApplication run loop hosting the pill and the status item."""

from __future__ import annotations

import signal
from collections.abc import Callable
from typing import Protocol

from .indicator import Indicator, NullIndicator, Overlay
from .modelswitch import ModelSwitcher
from .settings import LiveSettings


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
    live: LiveSettings,
    on_restart: Callable[[], None] = lambda: None,
    switcher: ModelSwitcher | None = None,
) -> Indicator:
    """Pill and/or status item per the config; a null indicator when both are off."""
    cfg = live.cfg
    parts: list[Part] = []
    if cfg.indicator == "overlay":
        parts.append(Overlay(cfg))
    if cfg.menubar_icon:
        from .settingswindow import SettingsWindow
        from .statusitem import StatusItem

        window = SettingsWindow(live, switcher, on_restart) if switcher else None
        parts.append(StatusItem(live, on_restart, switcher, window))
    return Host(parts) if parts else NullIndicator()
