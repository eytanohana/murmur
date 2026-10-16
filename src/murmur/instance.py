"""Single-instance guard: two copies of murmur would both type every word."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class InstanceLock:
    """An exclusive flock on a file. The kernel drops it when the process dies or execs."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        """True when this process now owns the lock, False when another one holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return True

    def holder_pid(self) -> int | None:
        """The pid the current owner wrote, for the log message."""
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing the descriptor releases the lock
            self._fd = None
