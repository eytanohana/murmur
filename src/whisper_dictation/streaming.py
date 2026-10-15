"""Streaming dictation: re-transcribe the growing buffer and commit stable words.

Whisper is not a streaming model. While Option is held, a pass runs on the
current utterance buffer about every `stream_interval` seconds. Words that
match in the same position on two consecutive passes are stable and are typed
immediately, except the last `stream_holdback_words` words of the newest pass.
Text already typed is never deleted or edited. On release one final pass over
the full buffer types only the words after the committed prefix.

Passes never overlap: they run one after another on a single thread, so a pass
that takes longer than the interval simply delays the next one.
"""

from __future__ import annotations

import logging
import re
import threading
import time

import numpy as np

from .transcriber import rms
from .watchdog import watchdog

log = logging.getLogger("whisper_dictation")


def norm(word: str) -> str:
    """Normalize a word for comparison across passes (case and punctuation)."""
    return re.sub(r"[^\w']", "", word.lower())


def join_words(words: list[str]) -> str:
    """Join words with spaces, except before tokens that attach to the previous word.

    Whisper splits some words into pieces such as "on" and "-call".
    """
    out = ""
    for word in words:
        if out and word[0] not in ".,;:!?-)%'\"":
            out += " "
        out += word
    return out


def common_prefix_len(prev: list[str], cur: list[str]) -> int:
    """Number of leading words that match in the same position."""
    n = 0
    for a, b in zip(prev, cur, strict=False):
        if norm(a) != norm(b):
            break
        n += 1
    return n


def commit_target(committed: int, prev: list[str], cur: list[str], holdback: int) -> int:
    """How many leading words of `cur` may be committed after this pass."""
    stable = common_prefix_len(prev, cur)
    return max(committed, min(stable, len(cur) - holdback))


def release_tail(committed: list[str], final: list[str]) -> tuple[list[str], bool]:
    """Words to append on release, and whether `final` disagrees with the prefix.

    On disagreement the committed text is kept and only the words after the
    committed count are appended.
    """
    n = len(committed)
    mismatch = len(final) < n or any(
        norm(a) != norm(b) for a, b in zip(committed, final, strict=False)
    )
    return final[n:], mismatch


def speech_seconds(audio: np.ndarray, sample_rate: int, threshold: float,
                   frame_s: float = 0.02) -> float:
    """Seconds of audio in 20 ms frames whose RMS is above the threshold."""
    frame = int(sample_rate * frame_s)
    if frame <= 0 or audio.size < frame:
        return 0.0
    usable = audio[: audio.size // frame * frame].reshape(-1, frame)
    frame_rms = np.sqrt(np.mean(np.square(usable, dtype=np.float64), axis=1))
    return float(np.count_nonzero(frame_rms > threshold)) * frame_s


def trim_sample(words, committed: int, sample_rate: int, total_samples: int) -> int | None:
    """Sample index to cut the buffer at: the end of the last committed word."""
    if committed <= 0 or committed > len(words):
        return None
    cut = int(words[committed - 1].end * sample_rate)
    if cut <= 0 or cut >= total_samples:
        return None
    return cut


class Streamer:
    """Owns one utterance: pass loop, committed text, trimming and the final pass."""

    def __init__(self, cfg, recorder, transcriber, inserter, clock=time.monotonic):
        self.cfg = cfg
        self.recorder = recorder
        self.transcriber = transcriber
        self.inserter = inserter
        self.clock = clock
        self.sr = cfg.sample_rate
        self.seg_committed: list[str] = []   # committed words in the current buffer
        self.prior_text = ""                 # committed text from trimmed segments
        self.prev_words: list = []
        self.last_pass_samples = 0
        self.inserted_any = False
        self.total_committed = 0
        self.pass_times: list[float] = []
        self._cancelled = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # control

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop streaming without blocking. Text already typed stays."""
        # No lock: this runs from the hotkey path and must not wait on a typing call.
        self._cancelled = True
        self._stop.set()
        log.info("Streaming cancelled; %d words already inserted stay", self.total_committed)

    def _loop(self) -> None:
        # Passes start as soon as the hold threshold is reached.
        deadline = self.clock() + self.cfg.min_hold_ms / 1000
        while not self._stop.wait(max(0.0, deadline - self.clock())):
            started = self.clock()
            try:
                self.tick()
            except Exception:
                log.exception("Streaming pass failed")
            deadline = started + self.cfg.stream_interval

    # one pass

    def _prompt(self) -> str | None:
        return self.prior_text[-200:] or None

    def tick(self) -> None:
        """Run one streaming pass if enough new speech has arrived."""
        if self._cancelled:
            return
        audio = self.recorder.snapshot()
        n = len(audio)
        new = audio[self.last_pass_samples:]
        if speech_seconds(new, self.sr, self.cfg.silence_rms) < self.cfg.stream_min_speech_s:
            return
        t0 = self.clock()
        log.info("stream pass start: %.1fs audio", n / self.sr)
        with watchdog("streaming pass"):
            words = self.transcriber.transcribe_words(audio, self._prompt())
        took = self.clock() - t0
        self.pass_times.append(took)
        self.last_pass_samples = n
        cur = [w.text for w in words]
        target = commit_target(len(self.seg_committed), [w.text for w in self.prev_words],
                               cur, self.cfg.stream_holdback_words)
        newly = cur[len(self.seg_committed):target]
        if newly and self._emit(newly):
            self.seg_committed.extend(newly)
        self.prev_words = words
        log.info("stream pass end: %.2fs, text=%r, %d committed", took, " ".join(cur),
                 self.total_committed)
        self._maybe_trim(words, n)

    def _maybe_trim(self, words, n: int) -> None:
        if n / self.sr <= self.cfg.stream_trim_s:
            return
        cut = trim_sample(words, len(self.seg_committed), self.sr, n)
        if cut is None:
            return
        self.recorder.trim(cut)
        self.prior_text = (self.prior_text + " " + " ".join(self.seg_committed)).strip()
        self.seg_committed = []
        self.prev_words = []
        self.last_pass_samples = max(0, n - cut)
        log.info("buffer trimmed at %.1fs; continuing with %.1fs", cut / self.sr,
                 (n - cut) / self.sr)

    def _emit(self, words: list[str]) -> bool:
        with self._lock:
            if self._cancelled:
                return False
            text = join_words(words)
            if self.inserted_any:
                text = " " + text
            self.inserter.type_text(text)
            self.inserted_any = True
            self.total_committed += len(words)
            return True

    # release

    def finish(self, final_audio: np.ndarray) -> list[str]:
        """Wait for a running pass, run the final pass, type the tail."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._cancelled:
            return []
        before = self.total_committed
        words: list = []
        if rms(final_audio) >= self.cfg.silence_rms:
            t0 = self.clock()
            log.info("final pass start: %.1fs audio", len(final_audio) / self.sr)
            with watchdog("final pass"):
                words = self.transcriber.transcribe_words(final_audio, self._prompt())
            log.info("final pass end: text=%r", " ".join(w.text for w in words))
            self.pass_times.append(self.clock() - t0)
        final = [w.text for w in words]
        tail, mismatch = release_tail(self.seg_committed, final)
        if mismatch:
            log.warning("final text disagrees with committed prefix; keeping inserted "
                        "text. committed=%r final=%r", " ".join(self.seg_committed),
                        " ".join(final))
        if tail:
            self._emit(tail)
        log.info("released: %d words committed before release, %d in final tail",
                 before, len(tail))
        return tail
