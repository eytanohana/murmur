"""Shared fakes for the dictation tests (no audio device, no model, no key events)."""

from __future__ import annotations

import numpy as np

from murmur.transcriber import Word

SR = 16000


def w(text: str, start: float = 0.0, end: float = 0.0) -> Word:
    return Word(text, start, end)


def ws(*texts: str) -> list[Word]:
    """Words with plausible timestamps, half a second apart."""
    return [w(t, i * 0.5, i * 0.5 + 0.4) for i, t in enumerate(texts)]


def tone(seconds: float, level: float = 0.1) -> np.ndarray:
    """Constant-level audio; level 0.0 is silence."""
    return np.full(int(seconds * SR), level, dtype=np.float32)


class FakeRecorder:
    """Stands in for Recorder: a growing buffer that tests feed by hand."""

    def __init__(self, audio: np.ndarray | None = None, fail_on_start: Exception | None = None):
        self.audio = audio if audio is not None else np.zeros(0, dtype=np.float32)
        self.fail_on_start = fail_on_start
        self.started = 0
        self.trims: list[int] = []
        self.on_max = None

    def feed(self, seconds: float, level: float = 0.1) -> None:
        self.audio = np.concatenate([self.audio, tone(seconds, level)])

    def start(self) -> None:
        if self.fail_on_start:
            raise self.fail_on_start
        self.started += 1

    def stop(self) -> np.ndarray:
        return self.audio.copy()

    def snapshot(self) -> np.ndarray:
        return self.audio.copy()

    def trim(self, n_samples: int) -> None:
        self.trims.append(n_samples)
        self.audio = self.audio[n_samples:]


class FakeInserter:
    """Records what would have been typed or pasted."""

    def __init__(self):
        self.typed: list[str] = []
        self.inserted: list[str] = []

    def type_text(self, text: str) -> None:
        self.typed.append(text)

    def insert(self, text: str) -> None:
        self.inserted.append(text)


class FakeTranscriber:
    """Whole-clip transcriber for the non-streaming path."""

    def __init__(self, text: str = "hello world"):
        self.text = text
        self.calls = 0

    def transcribe(self, audio: np.ndarray) -> str:
        self.calls += 1
        return self.text


class ScriptedTranscriber:
    """Streaming transcriber that returns one scripted word list per call.

    `on_call` runs inside each call, which lets a test act while a pass is in flight.
    """

    def __init__(self, results: list[list[Word]], on_call=None):
        self.results = list(results)
        self.on_call = on_call
        self.prompts: list[str | None] = []
        self.timestamps: list[bool] = []
        self.audio_lengths: list[int] = []

    def transcribe_words(
        self, audio: np.ndarray, prompt: str | None = None, timestamps: bool = True
    ) -> list[Word]:
        self.prompts.append(prompt)
        self.timestamps.append(timestamps)
        self.audio_lengths.append(len(audio))
        if self.on_call:
            self.on_call()
        return self.results.pop(0)


class ManualClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class ManualScheduler:
    """Collects scheduled callbacks so a test can fire them when it chooses."""

    def __init__(self):
        self.pending: list[tuple[float, object]] = []

    def __call__(self, delay_s: float, fn) -> None:
        self.pending.append((delay_s, fn))

    def fire(self, index: int = -1) -> None:
        self.pending[index][1]()


class RecordingStatus:
    def __init__(self):
        self.states: list[str] = []

    def set(self, state: str) -> None:
        self.states.append(state)
