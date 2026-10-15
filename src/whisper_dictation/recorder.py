"""Microphone capture into a numpy buffer."""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self, sample_rate: int = 16000, max_seconds: float = 120.0, on_max=None):
        self.sample_rate = sample_rate
        self.max_samples = int(max_seconds * sample_rate)
        self.on_max = on_max
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._max_fired = False

    def _callback(self, indata, frames, time_info, status) -> None:
        fire = False
        with self._lock:
            if self._samples < self.max_samples:
                self._chunks.append(indata[:, 0].copy())
                self._samples += frames
            if self._samples >= self.max_samples and not self._max_fired:
                self._max_fired = True
                fire = True
        if fire and self.on_max:
            # Do not stop the stream from inside its own callback thread.
            threading.Thread(target=self.on_max, daemon=True).start()

    def start(self) -> None:
        with self._lock:
            self._chunks = []
            self._samples = 0
            self._max_fired = False
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Stop capture and return the audio as a float32 mono array."""
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)[: self.max_samples]
