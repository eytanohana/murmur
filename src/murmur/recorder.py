"""Microphone capture into a numpy buffer."""

from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd

from .config import Config

log = logging.getLogger("murmur")

DEVICE_TIMEOUT_S = 5.0  # CoreAudio can block forever (device change, sleep); never wait longer


def _bounded(fn, timeout: float):
    """Run fn on a daemon thread. Returns (finished, result, error, thread).

    When fn does not finish in time the caller moves on; the thread keeps running.
    """
    box: dict = {}

    def run() -> None:
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001  handed back to the caller
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    return (not thread.is_alive()), box.get("result"), box.get("error"), thread


def _close(stream) -> None:
    try:
        stream.stop()
    finally:
        stream.close()


class Recorder:
    def __init__(
        self, cfg: Config | None = None, on_max=None, device_timeout: float = DEVICE_TIMEOUT_S
    ):
        self.cfg = cfg or Config()
        self.device_timeout = device_timeout
        self.sample_rate = self.cfg.sample_rate
        self.max_samples = self._cap()
        self.on_max = on_max
        self._chunks: list[np.ndarray] = []
        self._total = 0  # samples captured since start, never reduced by trim
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._max_fired = False
        self._warned_status = False

    def _cap(self) -> int:
        return int(self.cfg.max_record_s * self.sample_rate)

    def _callback(self, indata, frames, time_info, status) -> None:
        if status and not self._warned_status:
            self._warned_status = True  # one warning per recording, not one per buffer
            log.warning("Audio input problem (%s); the recording may have gaps", status)
        fire = False
        with self._lock:
            if self._total < self.max_samples:
                self._chunks.append(indata[:, 0].copy())
                self._total += frames
            if self._total >= self.max_samples and not self._max_fired:
                self._max_fired = True
                fire = True
        if fire and self.on_max:
            # Do not stop the stream from inside its own callback thread.
            threading.Thread(target=self.on_max, daemon=True).start()

    def start(self) -> None:
        with self._lock:
            self._chunks = []
            self._total = 0
            self._max_fired = False
            self._warned_status = False
            self.max_samples = self._cap()  # a max_record_s change applies to this recording
        abandoned = threading.Event()

        def open_stream():
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            stream.start()
            if abandoned.is_set():  # we gave up waiting: do not leave the mic on
                _close(stream)
                return None
            return stream

        finished, stream, error, _ = _bounded(open_stream, self.device_timeout)
        if not finished:
            abandoned.set()
            raise RuntimeError(f"the microphone did not start within {self.device_timeout:.0f}s")
        if error is not None:
            raise error
        self._stream = stream

    def stop(self) -> np.ndarray:
        """Stop capture and return the audio as a float32 mono array."""
        stream, self._stream = self._stream, None
        if stream is not None:
            finished, _, error, _ = _bounded(lambda: _close(stream), self.device_timeout)
            if not finished:
                log.error(
                    "Closing the microphone took over %.0fs; giving up on it", self.device_timeout
                )
            elif error is not None:
                log.warning("Closing the microphone failed: %s", error)
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def snapshot(self) -> np.ndarray:
        """Copy of the current buffer while recording continues."""
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            if len(self._chunks) > 1:
                self._chunks = [np.concatenate(self._chunks)]
            return self._chunks[0].copy()

    def trim(self, n_samples: int) -> None:
        """Drop the first n_samples from the buffer."""
        with self._lock:
            if not self._chunks:
                return
            data = np.concatenate(self._chunks)[n_samples:]
            self._chunks = [data] if data.size else []
