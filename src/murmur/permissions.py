"""Startup permission checks. They also trigger the macOS prompts, which matters under launchd."""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
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


def check_microphone(sample_rate: int = 16000) -> bool:
    """Open the input briefly so macOS shows the Microphone prompt at startup.

    Under launchd there is no terminal and no first recording to trigger it.
    A denied microphone delivers only exact zeros. Returns True when real audio arrived.
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
            "Could not open the microphone (%s). %s. murmur checks again by itself once "
            "it is granted.",
            exc,
            grant_hint("Microphone", permission_target()),
        )
        return False
    if not chunks or not np.any(np.concatenate(chunks)):
        log.warning(
            "The microphone returned only silence. If a permission prompt appeared, "
            "allow it; otherwise: %s. murmur checks again by itself once it is granted.",
            grant_hint("Microphone", permission_target()),
        )
        return False
    return True


def check_permissions() -> None:
    """Warn about missing macOS permissions and trigger the system prompts.

    The prompts are requested for this process, so they also appear when it runs
    from launchd with no terminal attached. Warnings name the real python binary.
    """
    target = permission_target()
    if not accessibility_trusted(prompt=True):
        log.warning(
            "Accessibility not granted (AXIsProcessTrusted is false). Typing and "
            "pasting are silently dropped without it. %s. murmur restarts by itself "
            "once you grant it.",
            grant_hint("Accessibility", target),
        )
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
            log.warning(
                "Input Monitoring not granted. %s. murmur restarts by itself once you grant it.",
                grant_hint("Input Monitoring", target),
            )
    except (ImportError, AttributeError):
        pass
    check_microphone()


# ---- noticing a grant while the app runs -------------------------------------------------------------------------------

ACCESSIBILITY, INPUT_MONITORING, MICROPHONE = "accessibility", "input_monitoring", "microphone"
LISTENER_DEPENDS_ON = frozenset({ACCESSIBILITY, INPUT_MONITORING})  # the hotkey listener's taps
NAMES = {
    ACCESSIBILITY: "Accessibility",
    INPUT_MONITORING: "Input Monitoring",
    MICROPHONE: "Microphone",
}
RESTART_WINDOW_S = 120.0  # never relaunch twice within this long (the loop guard)
POLL_INTERVAL_S = 2.0

State = dict[str, bool]


def microphone_authorized() -> bool | None:
    """AVCaptureDevice authorization for audio (3 = authorized); None if it cannot be read."""
    try:
        import objc

        objc.loadBundle(
            "AVFoundation", {}, bundle_path="/System/Library/Frameworks/AVFoundation.framework"
        )
        status = objc.lookUpClass("AVCaptureDevice").authorizationStatusForMediaType_("soun")
    except Exception:  # noqa: BLE001  the framework or class is missing: leave the mic unwatched
        return None
    return int(status) == 3


def probe() -> State:  # pragma: no cover - cheap real system calls (about 10 to 30 ms)
    """Current state of the permissions, without prompting."""
    import Quartz

    state = {
        ACCESSIBILITY: bool(accessibility_trusted(prompt=False)),
        INPUT_MONITORING: bool(Quartz.CGPreflightListenEventAccess()),
    }
    mic = microphone_authorized()
    if mic is not None:
        state[MICROPHONE] = mic
    return state


def missing(state: State) -> set[str]:
    return {name for name, granted in state.items() if not granted}


def newly_granted(before: State, after: State) -> set[str]:
    """Permissions that were missing in `before` and are granted in `after`."""
    return {name for name, granted in after.items() if granted and before.get(name) is False}


def decide(flipped: set[str], restart_allowed: bool) -> list[str]:
    """What to do about permissions that were just granted.

    The hotkey listener was created while Accessibility or Input Monitoring was missing, so
    its event tap stays dead after the grant: relaunch (macOS itself asks for a restart after
    an Input Monitoring grant). If a relaunch just happened, recreate the listener instead of
    looping. A microphone grant needs no restart: the next recording opens a fresh stream.
    """
    actions: list[str] = []
    if flipped & LISTENER_DEPENDS_ON:
        actions.append("restart" if restart_allowed else "recreate_listener")
    if MICROPHONE in flipped:
        actions.append("recheck_microphone")
    return actions


class RestartGuard:
    """Remembers the last permission-triggered relaunch in a file, so it can never loop."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock

    def allows(self) -> bool:
        try:
            last = float(self.path.read_text().strip())
        except (OSError, ValueError):
            return True
        return self.clock() - last > RESTART_WINDOW_S

    def record(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(str(self.clock()))
        except OSError:
            pass


class PermissionActions:
    """Carries out `decide`. Runs on the watcher thread; the callables do their own hopping."""

    def __init__(
        self,
        restart: Callable[[], None],
        is_busy: Callable[[], bool],
        recreate_listener: Callable[[], None],
        recheck_microphone: Callable[[], object],
        guard: RestartGuard,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.restart = restart
        self.is_busy = is_busy
        self.recreate_listener = recreate_listener
        self.recheck_microphone = recheck_microphone
        self.guard = guard
        self.sleep = sleep
        self.restarted = False  # set once a relaunch was requested from this process

    def __call__(self, flipped: set[str]) -> None:
        names = ", ".join(NAMES[n] for n in sorted(flipped))
        actions = decide(flipped, self.guard.allows() and not self.restarted)
        for action in actions:
            if action == "restart":
                log.info("%s granted; restarting murmur so the hotkey starts working", names)
                while self.is_busy():  # never in the middle of a dictation
                    self.sleep(0.5)
                self.guard.record()
                self.restarted = True
                self.restart()
            elif action == "recreate_listener":
                log.info(
                    "%s granted again shortly after a restart; recreating the hotkey listener",
                    names,
                )
                self.recreate_listener()
            elif action == "recheck_microphone":
                ok = False
                while self.is_busy():
                    self.sleep(0.5)
                ok = bool(self.recheck_microphone())
                log.info(
                    "Microphone granted; a fresh check %s",
                    "heard audio, no restart needed" if ok else "still returned silence",
                )


class PermissionWatcher:
    """Polls the permissions that were missing at startup, until all are granted."""

    def __init__(
        self,
        read: Callable[[], State],
        initial: State,
        on_granted: Callable[[set[str]], None],
        interval: float = POLL_INTERVAL_S,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.read = read
        self.state = dict(initial)
        self.on_granted = on_granted
        self.interval = interval
        self.sleep = sleep
        self._stop = False

    @property
    def pending(self) -> bool:
        return bool(missing(self.state))

    def poll(self) -> set[str]:
        """One cheap check. Calls `on_granted` for permissions that flipped to granted."""
        after = self.read()
        flipped = newly_granted(self.state, after)
        self.state.update(after)
        if flipped:
            self.on_granted(flipped)
        return flipped

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        """Loop while something is missing; returns when all are granted or stop() is called."""
        while self.pending and not self._stop:
            self.sleep(self.interval)
            if self._stop:
                break
            try:
                self.poll()
            except Exception:
                log.exception("Permission check failed")

    def start(self) -> None:
        if self.pending:  # nothing missing at startup: no thread, no polling
            import threading

            threading.Thread(target=self.run, daemon=True).start()


def recheck_microphone() -> bool:
    """Open a fresh input stream after a grant. PortAudio is reset first: it may have listed
    no input device when it was initialised before the grant."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.debug("Could not reset PortAudio", exc_info=True)
    return check_microphone()
