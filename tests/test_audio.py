"""Pure audio helpers: silence detection, edge trimming and the buffer cut point."""

from __future__ import annotations

import numpy as np

from helpers import SR, tone, w
from murmur.audio import (
    drop_leading_silence,
    drop_trailing_silence,
    speech_seconds,
    trailing_silence_s,
    trim_sample,
)

THRESHOLD = 0.005


def clip(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate(parts).astype(np.float32)


def test_speech_seconds_counts_loud_frames():
    assert abs(speech_seconds(clip(tone(1, 0), tone(0.5)), SR, THRESHOLD) - 0.5) < 0.03
    assert speech_seconds(tone(1, 0), SR, THRESHOLD) == 0.0
    assert speech_seconds(tone(0.01), SR, THRESHOLD) == 0.0  # shorter than one frame


def test_trailing_silence_counts_quiet_frames_at_the_end():
    assert abs(trailing_silence_s(clip(tone(1), tone(0.5, 0)), SR, THRESHOLD) - 0.5) < 0.03
    assert trailing_silence_s(tone(1), SR, THRESHOLD) == 0.0
    assert trailing_silence_s(tone(1, 0), SR, THRESHOLD) == 1.0
    # A quiet gap before the last loud frame does not count.
    assert trailing_silence_s(clip(tone(0.5), tone(0.5, 0), tone(0.1)), SR, THRESHOLD) == 0.0


def test_drop_trailing_silence_keeps_a_short_tail():
    out = drop_trailing_silence(clip(tone(1), tone(2, 0)), SR, THRESHOLD, keep_s=0.2)
    assert abs(len(out) / SR - 1.2) < 0.03
    short = clip(tone(1), tone(0.1, 0))
    assert len(drop_trailing_silence(short, SR, THRESHOLD)) == len(short)


def test_drop_leading_silence_keeps_a_short_lead_and_reports_removed_samples():
    audio = clip(tone(2, 0), tone(1))
    out, removed = drop_leading_silence(audio, SR, THRESHOLD, keep_s=0.2)
    assert abs(removed / SR - 1.8) < 0.03
    assert len(out) == len(audio) - removed
    assert np.array_equal(out, audio[removed:])
    same, none = drop_leading_silence(tone(1), SR, THRESHOLD)
    assert none == 0 and len(same) == SR


def test_trim_sample_cuts_at_the_last_committed_word():
    words = [w("a", 0, 5.0), w("b", 5.0, 12.0), w("c", 12.0, 21.0)]
    audio = tone(22)
    assert trim_sample(words, 2, SR, 22 * SR, audio, THRESHOLD) == 12 * SR
    assert trim_sample(words, 0, SR, 22 * SR, audio, THRESHOLD) is None
    assert trim_sample(words, 4, SR, 22 * SR, audio, THRESHOLD) is None  # more than exist
    assert trim_sample(words, 3, SR, 21 * SR, audio, THRESHOLD) is None  # cut at buffer end


def test_trim_sample_moves_back_into_silence_after_a_stretched_word_end():
    # Speech 0-1 s, silence 1-2.5 s; Whisper says the word ends at 2.5 s.
    audio = clip(tone(1), tone(1.5, 0))
    cut = trim_sample([w("a", 0.0, 2.5)], 1, SR, 4 * SR, audio, THRESHOLD)
    assert abs(cut - SR) <= int(0.03 * SR)


def test_trim_sample_ignores_quiet_frames_in_the_first_100ms_of_the_word():
    # 60 ms of quiet, 0.5 s of speech, then quiet: the cut belongs after the speech.
    audio = clip(tone(0.06, 0), tone(0.5), tone(0.5, 0))
    cut = trim_sample([w("a", 0.0, 1.0)], 1, SR, 2 * SR, audio, THRESHOLD)
    assert abs(cut - int(0.56 * SR)) <= int(0.03 * SR)


def test_trim_sample_keeps_the_word_end_when_speech_is_continuous():
    assert trim_sample([w("a", 0.0, 1.0)], 1, SR, 2 * SR, tone(2), THRESHOLD) == SR
