"""The main-thread owner: one NSApplication run loop hosting the pill and the status item."""

from __future__ import annotations

import logging
import signal
from collections.abc import Callable
from typing import Protocol

from .indicator import Overlay
from .mainthread import main_thread
from .modelswitch import ModelSwitcher
from .reopen import OPEN_SETTINGS_NOTIFICATION, route_open_request
from .settings import LiveSettings

log = logging.getLogger("murmur")


class Part(Protocol):
    def build(self) -> None: ...

    def set(self, state: str) -> None: ...


class Host:
    """Runs AppKit on the main thread while dictation works on other threads.

    set() fans a state out to every part from any thread. SIGTERM and SIGINT
    terminate the app, which exits with status 0. The app delegate handles the reopen
    event (launching murmur again) and the second-launch notification by opening the
    Settings window, so the app can always be reached even with no menu bar icon.
    """

    def __init__(self, parts: list[Part], window=None):
        self.parts = parts
        self.window = window  # the SettingsWindow, or None
        self.app = None
        self.delegate = None
        self.show_settings_on_start = False  # first launch: a short welcome where to grant access
        self.open_scope: str | None = None  # notification object: the instance lock path
        self.is_busy: Callable[[], bool] = lambda: False  # True while a dictation is running

    def set(self, state: str) -> None:
        for part in self.parts:
            part.set(state)

    def open_settings(self) -> None:
        """Show and focus the Settings window (main thread)."""
        if self.window is not None:
            self.window.show()

    def handle_reopen(self) -> bool:
        """murmur was launched again while running: open Settings. Returns whether AppKit
        should also do its default reopen handling (it should not)."""
        log.info("murmur was opened again; showing Settings")
        self.open_settings()
        return False

    def handle_open_request(self) -> None:
        """A second copy started by executing the binary asked us to open Settings."""
        if not route_open_request(self.is_busy()):
            log.info("Settings requested by a second launch, but a dictation is running; ignored")
            return
        log.info("A second launch asked for Settings")
        self.open_settings()

    def run(self) -> None:
        import AppKit
        from Foundation import NSDistributedNotificationCenter

        class AppDelegate(AppKit.NSObject):
            def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_windows):
                return self.host.handle_reopen()

            def openSettings_(self, note):
                self.host.handle_open_request()

        self.app = AppKit.NSApplication.sharedApplication()
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.delegate = AppDelegate.alloc().init()
        self.delegate.host = self
        self.app.setDelegate_(self.delegate)
        if self.open_scope is not None:
            NSDistributedNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self.delegate, "openSettings:", OPEN_SETTINGS_NOTIFICATION, self.open_scope
            )
        for part in self.parts:
            part.build()
        install_signal_handlers(self.stop)
        if self.show_settings_on_start:
            main_thread(self.open_settings)
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
) -> Host:
    """The pill, if enabled, and the status item, which follows menubar_icon at runtime.

    The AppKit loop always runs (also for the plain CLI): the status item and the Settings
    window can be switched on later, and the app must stay reachable by launching it again.
    """
    cfg = live.cfg
    parts: list[Part] = []
    if cfg.indicator == "overlay":
        parts.append(Overlay(cfg))
    window = None
    if switcher is not None:
        from .settingswindow import SettingsWindow
        from .statusitem import StatusItem

        window = SettingsWindow(live, switcher, on_restart)
        item = StatusItem(live, on_restart, switcher, window)
        live.key_hooks["menubar_icon"] = item.enable
        parts.append(item)
    return Host(parts, window)
