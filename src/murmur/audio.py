"""Pure audio helpers: energy, silence detection, edge trimming and the buffer cut point."""

from __future__ import annotations

import numpy as np

FRAME_S = 0.02  # energy is measured in 20 ms frames
EDGE_KEEP_S = 0.2  # silence left at a trimmed edge so speech is not clipped
TRIM_SKIP_FRAMES = 5  # ignore the first 100 ms of a word when looking for a quiet cut
TRIM_QUIET_FRAMES = 3  # a quiet stretch is 60 ms of consecutive quiet frames


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def frame_size(sample_rate: int) -> int:
    return int(sample_rate * FRAME_S)


def frame_rms(audio: np.ndarray, frame: int, align_end: bool = False) -> np.ndarray:
    """RMS of each whole frame. Frames start at the first sample, or end at the last one."""
    usable = audio[audio.size % frame :] if align_end else audio[: audio.size // frame * frame]
    return np.sqrt(np.mean(np.square(usable.reshape(-1, frame), dtype=np.float64), axis=1))


def speech_seconds(audio: np.ndarray, sample_rate: int, threshold: float) -> float:
    """Seconds of audio in frames whose RMS is above the threshold."""
    frame = frame_size(sample_rate)
    if frame <= 0 or audio.size < frame:
        return 0.0
    return float(np.count_nonzero(frame_rms(audio, frame) > threshold)) * FRAME_S


def trailing_silence_s(audio: np.ndarray, sample_rate: int, threshold: float) -> float:
    """Seconds of audio at the end whose frames are all at or below the threshold."""
    frame = frame_size(sample_rate)
    if frame <= 0 or audio.size < frame:
        return 0.0
    loud = frame_rms(audio, frame, align_end=True) > threshold
    if not loud.any():
        return loud.size * FRAME_S
    return float(loud.size - 1 - np.nonzero(loud)[0][-1]) * FRAME_S


def drop_trailing_silence(
    audio: np.ndarray, sample_rate: int, threshold: float, keep_s: float = EDGE_KEEP_S
) -> np.ndarray:
    """Cut trailing silence down to `keep_s`. Whisper invents words over long silence."""
    extra = trailing_silence_s(audio, sample_rate, threshold) - keep_s
    if extra <= 0:
        return audio
    return audio[: audio.size - int(extra * sample_rate)]


def drop_leading_silence(
    audio: np.ndarray, sample_rate: int, threshold: float, keep_s: float = EDGE_KEEP_S
) -> tuple[np.ndarray, int]:
    """Cut leading silence down to `keep_s`. Returns the audio and samples removed.

    Leading silence makes Whisper start with an invented word such as "Okay,".
    """
    extra = trailing_silence_s(audio[::-1], sample_rate, threshold) - keep_s
    if extra <= 0:
        return audio, 0
    removed = int(extra * sample_rate)
    return audio[removed:], removed


def trim_sample(
    words,
    committed: int,
    sample_rate: int,
    total_samples: int,
    audio: np.ndarray,
    threshold: float,
) -> int | None:
    """Sample index to cut the buffer at, after the last committed word.

    Whisper stretches the end time of a word that is followed by silence into the
    next word, so cutting at that time can clip the start of the next word. The cut
    moves back to the first quiet stretch inside the word's span, when there is one.
    """
    if committed <= 0 or committed > len(words):
        return None
    word = words[committed - 1]
    cut = int(word.end * sample_rate)
    if cut <= 0 or cut >= total_samples:
        return None
    frame = frame_size(sample_rate)
    start = int(word.start * sample_rate)
    quiet = frame_rms(audio[start:cut], frame) <= threshold
    for i in range(TRIM_SKIP_FRAMES, len(quiet) - (TRIM_QUIET_FRAMES - 1)):
        if quiet[i : i + TRIM_QUIET_FRAMES].all():
            return start + i * frame
    return cut
