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
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from .audio import (
    drop_leading_silence,
    drop_trailing_silence,
    rms,
    speech_seconds,
    trailing_silence_s,
    trim_sample,
)
from .transcriber import Word
from .watchdog import watchdog

if TYPE_CHECKING:
    from .config import Config
    from .inserter import Inserter
    from .recorder import Recorder
    from .transcriber import Transcriber

log = logging.getLogger("murmur")

PROMPT_CHARS = 200  # tail of already-typed text given to Whisper as context after a trim


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


class Streamer:
    """Owns one utterance: pass loop, committed text, trimming and the final pass."""

    def __init__(
        self,
        cfg: Config,
        recorder: Recorder,
        transcriber: Transcriber,
        inserter: Inserter,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg = cfg
        self.recorder = recorder
        self.transcriber = transcriber
        self.inserter = inserter
        self.clock = clock
        self.sr = cfg.sample_rate
        self.seg_committed: list[str] = []  # committed words in the current buffer
        self.prior_text = ""  # committed text from trimmed segments
        self.prev_words: list[Word] = []
        self.last_pass_samples = 0
        self.flushed_samples = 0  # buffer length at the last pause flush
        self.inserted_any = False
        self.total_committed = 0
        self.pass_times: list[float] = []
        self._cancelled = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

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
            except Exception:  # a failed pass must not end the loop for the rest of the hold
                log.exception("Streaming pass failed")
            deadline = started + self.cfg.stream_interval

    # one pass

    def _prompt(self) -> str | None:
        return self.prior_text[-PROMPT_CHARS:] or None

    def _pause_detected(self, audio: np.ndarray) -> bool:
        """A pause after speech that has not been flushed yet."""
        rms_floor = self.cfg.silence_rms
        return (
            trailing_silence_s(audio, self.sr, rms_floor) >= self.cfg.stream_flush_silence_s
            and speech_seconds(audio[self.flushed_samples :], self.sr, rms_floor)
            >= self.cfg.stream_min_speech_s
        )

    def _has_new_speech(self, audio: np.ndarray) -> bool:
        new = audio[self.last_pass_samples :]
        return speech_seconds(new, self.sr, self.cfg.silence_rms) >= self.cfg.stream_min_speech_s

    def _hear(
        self, audio: np.ndarray, label: str, drop_trailing: bool, timestamps: bool
    ) -> list[Word]:
        """Transcribe with silence trimmed off the edges; word times are in buffer time.

        Long silence makes Whisper invent words ("Okay,"), and a flush or the final
        pass would commit them for good.
        """
        with watchdog(label):
            heard = audio
            if drop_trailing:
                heard = drop_trailing_silence(heard, self.sr, self.cfg.silence_rms)
            heard, removed = drop_leading_silence(heard, self.sr, self.cfg.silence_rms)
            words = self.transcriber.transcribe_words(heard, self._prompt(), timestamps=timestamps)
        offset = removed / self.sr
        return [Word(w.text, w.start + offset, w.end + offset) for w in words]

    def tick(self) -> None:
        """Run one streaming pass if enough new speech has arrived."""
        if self._cancelled:
            return
        audio = self.recorder.snapshot()
        n = len(audio)
        flush = self._pause_detected(audio)
        if not flush and not self._has_new_speech(audio):
            return
        t0 = self.clock()
        log.info("stream pass start: %.1fs audio", n / self.sr)
        # Timestamps are only needed on the pass that trims a long buffer.
        need_cut = n / self.sr > self.cfg.stream_trim_s
        words = self._hear(audio, "streaming pass", drop_trailing=flush, timestamps=need_cut)
        took = self.clock() - t0
        self.pass_times.append(took)
        self.last_pass_samples = n
        cur = [w.text for w in words]
        if flush:
            # The speaker paused: nothing more is coming, so commit everything heard.
            target = max(len(self.seg_committed), len(cur))
            self.flushed_samples = n
            log.info("pause detected; flushing held-back words")
        else:
            target = commit_target(
                len(self.seg_committed),
                [w.text for w in self.prev_words],
                cur,
                self.cfg.stream_holdback_words,
            )
        newly = cur[len(self.seg_committed) : target]
        if newly and self._emit(newly):
            self.seg_committed.extend(newly)
        self.prev_words = words
        log.info(
            "stream pass end: %.2fs, text=%r, %d committed",
            took,
            " ".join(cur),
            self.total_committed,
        )
        self._maybe_trim(words, n, audio)

    def _maybe_trim(self, words: list[Word], n: int, audio: np.ndarray) -> None:
        if n / self.sr <= self.cfg.stream_trim_s:
            return
        cut = trim_sample(words, len(self.seg_committed), self.sr, n, audio, self.cfg.silence_rms)
        if cut is None:
            return
        self.recorder.trim(cut)
        self.prior_text = (self.prior_text + " " + " ".join(self.seg_committed)).strip()
        self.seg_committed = []
        self.prev_words = []
        self.last_pass_samples = max(0, n - cut)
        self.flushed_samples = max(0, self.flushed_samples - cut)
        log.info(
            "buffer trimmed at %.1fs; continuing with %.1fs", cut / self.sr, (n - cut) / self.sr
        )

    def _emit(self, words: list[str]) -> bool:
        """Type words; False when cancelled. The lock keeps cancel and typing ordered."""
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

    def _final_words(self, final_audio: np.ndarray) -> list[str]:
        if rms(final_audio) < self.cfg.silence_rms:
            return []
        t0 = self.clock()
        log.info("final pass start: %.1fs audio", len(final_audio) / self.sr)
        words = self._hear(final_audio, "final pass", drop_trailing=True, timestamps=False)
        log.info("final pass end: text=%r", " ".join(w.text for w in words))
        self.pass_times.append(self.clock() - t0)
        return [w.text for w in words]

    def finish(self, final_audio: np.ndarray) -> list[str]:
        """Wait for a running pass, run the final pass, type the tail."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._cancelled:
            return []
        before = self.total_committed
        final = self._final_words(final_audio)
        tail, mismatch = release_tail(self.seg_committed, final)
        if mismatch:
            log.warning(
                "final text disagrees with committed prefix; keeping inserted "
                "text. committed=%r final=%r",
                " ".join(self.seg_committed),
                " ".join(final),
            )
        if tail:
            self._emit(tail)
        log.info("released: %d words committed before release, %d in final tail", before, len(tail))
        return tail
