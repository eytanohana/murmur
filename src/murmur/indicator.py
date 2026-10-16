"""Visible recording indicator: a floating overlay pill.

AppKit must run on the main thread, so an indicator owns the main thread through
run() while dictation work happens on other threads. set() is safe to call from
any thread. stop() ends the process with status 0 (NSApp terminate).
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from typing import Protocol

PILL_W, PILL_H, MARGIN = 78.0, 26.0, 6.0

# state -> (label, dot color name); idle hides the overlay
LOOKS = {"recording": ("REC", "red"), "transcribing": ("...", "orange")}


def overlay_origin(frame, visible, size, position: str, margin: float = MARGIN):
    """Bottom-left origin for the pill. frame and visible are (x, y, w, h) tuples.

    Top positions sit just below the menu bar (and the notch area), because the
    visible frame excludes them. Bottom positions use the visible frame too, so
    they clear the Dock.
    """
    fx, _fy, fw, _fh = frame
    vx, vy, vw, vh = visible
    w, h = size
    if position.endswith("center"):
        x = fx + (fw - w) / 2
    elif position.endswith("left"):
        x = vx + margin
    else:
        x = vx + vw - w - margin
    y = vy + vh - h - margin if position.startswith("top") else vy + margin
    return x, y


class Indicator(Protocol):
    """What Dictation and main() need from an indicator."""

    def set(self, state: str) -> None: ...

    def run(self) -> None: ...


class NullIndicator:
    """No visible indicator; run() just parks the main thread."""

    def set(self, state: str) -> None:
        pass

    def run(self) -> None:
        threading.Event().wait()


def _main_thread(fn: Callable, *args) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callAfter(fn, *args)


class Overlay:
    """Borderless, click-through, always-on-top pill that never takes focus."""

    def __init__(self, position: str = "top-center"):
        self.position = position
        self.panel = None
        self.dot = None
        self.label = None
        self.app = None

    # main thread only

    def _build(self) -> None:
        import AppKit
        from AppKit import NSColor, NSPanel

        class _Panel(NSPanel):
            def canBecomeKeyWindow(self):
                return False

            def canBecomeMainWindow(self):
                return False

        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        panel = _Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, PILL_W, PILL_H), style, AppKit.NSBackingStoreBuffered, False
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        content = panel.contentView()
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(PILL_H / 2)
        content.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.9).CGColor()
        )
        dot = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(11, (PILL_H - 10) / 2, 10, 10))
        dot.setWantsLayer_(True)
        dot.layer().setCornerRadius_(5)
        content.addSubview_(dot)
        label = AppKit.NSTextField.labelWithString_("REC")
        label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(12))
        label.setTextColor_(NSColor.whiteColor())
        label.setFrame_(AppKit.NSMakeRect(27, (PILL_H - 16) / 2 - 1, PILL_W - 31, 16))
        content.addSubview_(label)
        self.panel, self.dot, self.label = panel, dot, label

    def _apply(self, state: str) -> None:
        import AppKit

        if self.panel is None:
            return
        look = LOOKS.get(state)
        if look is None:
            self.panel.orderOut_(None)
            return
        text, color = look
        rgb = {"red": (1.0, 0.19, 0.19), "orange": (1.0, 0.62, 0.04)}[color]
        self.dot.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, 1.0).CGColor()
        )
        self.label.setStringValue_(text)
        screen = AppKit.NSScreen.screens()[0]  # the screen that has the menu bar
        f, v = screen.frame(), screen.visibleFrame()
        x, y = overlay_origin(
            (f.origin.x, f.origin.y, f.size.width, f.size.height),
            (v.origin.x, v.origin.y, v.size.width, v.size.height),
            (PILL_W, PILL_H),
            self.position,
        )
        self.panel.setFrameOrigin_(AppKit.NSMakePoint(x, y))
        self.panel.orderFrontRegardless()  # never makeKey: focus stays in the typing app

    # any thread

    def set(self, state: str) -> None:
        if self.panel is not None:
            _main_thread(self._apply, state)

    def run(self) -> None:
        import AppKit

        self.app = AppKit.NSApplication.sharedApplication()
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._build()
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


# Keyed by the valid indicator values in config.INDICATORS.
FACTORIES: dict[str, Callable[[str], Indicator]] = {
    "overlay": Overlay,
    "none": lambda _position: NullIndicator(),
}


def make_indicator(kind: str, position: str = "top-center") -> Indicator:
    return FACTORIES[kind](position)
