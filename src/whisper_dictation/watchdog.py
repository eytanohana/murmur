"""Log a thread dump if a step runs longer than a limit."""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import threading

log = logging.getLogger("whisper_dictation")


@contextlib.contextmanager
def watchdog(label: str, seconds: float = 10.0):
    """Dump all thread stacks to stderr if the block takes longer than `seconds`.

    faulthandler keeps one timer per process, so nested use replaces the outer
    timer. The steps guarded here (a streaming pass, an insert) run one at a time.
    """
    message = f"{label} has taken over {seconds:.0f}s; dumping thread stacks to stderr"
    timer = threading.Timer(seconds, log.error, args=(message,))
    timer.daemon = True
    timer.start()
    faulthandler.dump_traceback_later(seconds, exit=False)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()
        timer.cancel()
