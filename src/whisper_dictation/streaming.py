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

from .transcriber import Word, rms
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


def trailing_silence_s(audio: np.ndarray, sample_rate: int, threshold: float,
                       frame_s: float = 0.02) -> float:
    """Seconds of audio at the end whose 20 ms frames are all at or below the threshold."""
    frame = int(sample_rate * frame_s)
    if frame <= 0 or audio.size < frame:
        return 0.0
    usable = audio[audio.size % frame:].reshape(-1, frame)  # align frames to the end
    loud = np.sqrt(np.mean(np.square(usable, dtype=np.float64), axis=1)) > threshold
    if not loud.any():
        return usable.shape[0] * frame_s
    return float(usable.shape[0] - 1 - np.nonzero(loud)[0][-1]) * frame_s


def drop_trailing_silence(audio: np.ndarray, sample_rate: int, threshold: float,
                          keep_s: float = 0.2) -> np.ndarray:
    """Cut trailing silence down to `keep_s`. Whisper invents words over long silence."""
    extra = trailing_silence_s(audio, sample_rate, threshold) - keep_s
    if extra <= 0:
        return audio
    return audio[: audio.size - int(extra * sample_rate)]


def drop_leading_silence(audio: np.ndarray, sample_rate: int, threshold: float,
                         keep_s: float = 0.2) -> tuple[np.ndarray, int]:
    """Cut leading silence down to `keep_s`. Returns the audio and samples removed.

    Leading silence makes Whisper start with an invented word such as "Okay,".
    """
    extra = trailing_silence_s(audio[::-1], sample_rate, threshold) - keep_s
    if extra <= 0:
        return audio, 0
    removed = int(extra * sample_rate)
    return audio[removed:], removed


def trim_sample(words, committed: int, sample_rate: int, total_samples: int,
                audio: np.ndarray | None = None, threshold: float = 0.005) -> int | None:
    """Sample index to cut the buffer at, after the last committed word.

    Whisper stretches the end time of a word that is followed by silence into the
    next word, so cutting at that time can clip the start of the next word. With
    `audio` given, the cut moves back to the first quiet stretch (60 ms) inside the
    word's span, when there is one.
    """
    if committed <= 0 or committed > len(words):
        return None
    word = words[committed - 1]
    cut = int(word.end * sample_rate)
    if cut <= 0 or cut >= total_samples:
        return None
    if audio is not None:
        frame = int(sample_rate * 0.02)
        start = int(word.start * sample_rate)
        span = audio[start:cut]
        quiet = np.sqrt(np.mean(np.square(
            span[: span.size // frame * frame].reshape(-1, frame), dtype=np.float64),
            axis=1)) <= threshold if span.size >= frame else np.zeros(0, dtype=bool)
        for i in range(5, len(quiet) - 2):  # skip the first 100 ms of the word
            if quiet[i] and quiet[i + 1] and quiet[i + 2]:
                return start + i * frame
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
        self.flushed_samples = 0   # buffer length at the last pause flush
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
        # A pause after unflushed speech: run a pass and commit everything it heard.
        flush = (
            trailing_silence_s(audio, self.sr, self.cfg.silence_rms)
            >= self.cfg.stream_flush_silence_s
            and speech_seconds(audio[self.flushed_samples:], self.sr, self.cfg.silence_rms)
            >= self.cfg.stream_min_speech_s
        )
        if not flush and (
            speech_seconds(new, self.sr, self.cfg.silence_rms) < self.cfg.stream_min_speech_s
        ):
            return
        t0 = self.clock()
        log.info("stream pass start: %.1fs audio", n / self.sr)
        # Timestamps are only needed on the pass that trims a long buffer.
        need_cut = n / self.sr > self.cfg.stream_trim_s
        with watchdog("streaming pass"):
            # Long trailing silence makes Whisper invent words ("Okay,"), and a flush
            # would commit them for good.
            heard = (drop_trailing_silence(audio, self.sr, self.cfg.silence_rms)
                     if flush else audio)
            heard, removed = drop_leading_silence(heard, self.sr, self.cfg.silence_rms)
            words = self.transcriber.transcribe_words(heard, self._prompt(),
                                                      timestamps=need_cut)
            offset = removed / self.sr
            words = [Word(w.text, w.start + offset, w.end + offset) for w in words]
        took = self.clock() - t0
        self.pass_times.append(took)
        self.last_pass_samples = n
        cur = [w.text for w in words]
        holdback = 0 if flush else self.cfg.stream_holdback_words
        target = commit_target(len(self.seg_committed), [w.text for w in self.prev_words],
                               cur, holdback)
        if flush:
            target = max(target, len(cur))
            self.flushed_samples = n
            log.info("pause detected; flushing held-back words")
        newly = cur[len(self.seg_committed):target]
        if newly and self._emit(newly):
            self.seg_committed.extend(newly)
        self.prev_words = words
        log.info("stream pass end: %.2fs, text=%r, %d committed", took, " ".join(cur),
                 self.total_committed)
        self._maybe_trim(words, n, audio)

    def _maybe_trim(self, words, n: int, audio: np.ndarray) -> None:
        if n / self.sr <= self.cfg.stream_trim_s:
            return
        cut = trim_sample(words, len(self.seg_committed), self.sr, n, audio,
                          self.cfg.silence_rms)
        if cut is None:
            return
        self.recorder.trim(cut)
        self.prior_text = (self.prior_text + " " + " ".join(self.seg_committed)).strip()
        self.seg_committed = []
        self.prev_words = []
        self.last_pass_samples = max(0, n - cut)
        self.flushed_samples = max(0, self.flushed_samples - cut)
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
                heard = drop_trailing_silence(final_audio, self.sr, self.cfg.silence_rms)
                heard, _ = drop_leading_silence(heard, self.sr, self.cfg.silence_rms)
                words = self.transcriber.transcribe_words(heard, self._prompt(),
                                                          timestamps=False)
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
