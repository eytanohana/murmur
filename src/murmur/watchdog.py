"""Log a thread dump if a step runs longer than a limit."""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import traceback

log = logging.getLogger("murmur")


def dump_threads() -> str:
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        stack = "".join(traceback.format_stack(frame))
        parts.append(f"--- thread {names.get(ident, ident)} ---\n{stack}")
    return "\n".join(parts)


def _fire(label: str, seconds: float) -> None:
    log.error("%s has taken over %.0fs; thread stacks follow\n%s", label, seconds,
              dump_threads())


@contextlib.contextmanager
def watchdog(label: str, seconds: float = 10.0):
    """Log every thread's stack if the block takes longer than `seconds`.

    This uses a plain timer thread rather than faulthandler: faulthandler keeps
    one process-wide timer, which nested or concurrent use replaces, and its
    cancel call can race with an expiring timer.
    """
    timer = threading.Timer(seconds, _fire, args=(label, seconds))
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
