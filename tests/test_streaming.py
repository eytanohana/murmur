from types import SimpleNamespace

import numpy as np

from whisper_dictation.app import Dictation, State
from whisper_dictation.config import Config
from whisper_dictation.streaming import (
    Streamer,
    commit_target,
    common_prefix_len,
    drop_leading_silence,
    drop_trailing_silence,
    join_words,
    release_tail,
    speech_seconds,
    trailing_silence_s,
    trim_sample,
)
from whisper_dictation.transcriber import Word

SR = 16000


def w(text, start=0.0, end=0.0):
    return Word(text, start, end)


def test_common_prefix_ignores_case_and_punctuation():
    assert common_prefix_len(["Hello,", "world"], ["hello", "World.", "again"]) == 2
    assert common_prefix_len(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert common_prefix_len([], ["a"]) == 0


def test_commit_target_requires_two_passes_and_holds_back():
    prev = ["a", "b", "c", "d"]
    cur = ["a", "b", "c", "d", "e"]
    assert commit_target(0, prev, cur, holdback=2) == 3
    assert commit_target(0, [], cur, holdback=2) == 0  # first pass commits nothing
    assert commit_target(0, prev, cur, holdback=0) == 4


def test_join_words_attaches_split_tokens():
    assert join_words(["the", "on", "-call", "handbook,", "ok"]) == "the on-call handbook, ok"


def test_commit_target_never_goes_backwards():
    assert commit_target(3, ["a", "x"], ["a", "x", "y"], holdback=1) == 3


def test_release_tail_appends_words_after_prefix():
    tail, mismatch = release_tail(["a", "b"], ["a", "b", "c", "d"])
    assert tail == ["c", "d"] and not mismatch


def test_release_tail_mismatch_keeps_prefix_and_appends_tail():
    tail, mismatch = release_tail(["a", "b"], ["a", "z", "c"])
    assert tail == ["c"] and mismatch
    tail, mismatch = release_tail(["a", "b", "c"], ["a"])
    assert tail == [] and mismatch


def test_trim_sample_cuts_at_last_committed_word():
    words = [w("a", 0, 5.0), w("b", 5.0, 12.0), w("c", 12.0, 21.0)]
    assert trim_sample(words, 2, SR, 22 * SR) == 12 * SR
    assert trim_sample(words, 0, SR, 22 * SR) is None
    assert trim_sample(words, 3, SR, 21 * SR) is None  # cut at the end of the buffer


def test_trim_sample_moves_back_into_silence_after_a_stretched_word_end():
    # Speech 0-1 s, silence 1-2.5 s, next word from 2.5 s. Whisper says the word ends at 2.5 s.
    audio = np.concatenate([np.full(SR, 0.1), np.zeros(int(1.5 * SR))]).astype(np.float32)
    words = [w("a", 0.0, 2.5)]
    cut = trim_sample(words, 1, SR, 4 * SR, audio, 0.005)
    assert abs(cut - SR) <= int(0.03 * SR)
    assert trim_sample(words, 1, SR, 4 * SR) == int(2.5 * SR)  # no audio given


def test_drop_trailing_silence_keeps_a_short_tail():
    audio = np.concatenate([np.full(SR, 0.1), np.zeros(2 * SR)]).astype(np.float32)
    out = drop_trailing_silence(audio, SR, 0.005, keep_s=0.2)
    assert abs(len(out) / SR - 1.2) < 0.03
    short = np.concatenate([np.full(SR, 0.1), np.zeros(SR // 10)]).astype(np.float32)
    assert len(drop_trailing_silence(short, SR, 0.005)) == len(short)


def test_drop_leading_silence_keeps_a_short_lead_and_reports_removed_samples():
    audio = np.concatenate([np.zeros(2 * SR), np.full(SR, 0.1)]).astype(np.float32)
    out, removed = drop_leading_silence(audio, SR, 0.005, keep_s=0.2)
    assert abs(removed / SR - 1.8) < 0.03
    assert len(out) == len(audio) - removed
    same, none = drop_leading_silence(np.full(SR, 0.1, dtype=np.float32), SR, 0.005)
    assert none == 0 and len(same) == SR


def test_speech_seconds_counts_loud_frames():
    audio = np.concatenate([np.zeros(SR), np.full(SR // 2, 0.1)]).astype(np.float32)
    assert abs(speech_seconds(audio, SR, 0.005) - 0.5) < 0.03
    assert speech_seconds(np.zeros(SR, dtype=np.float32), SR, 0.005) == 0.0


def test_trailing_silence_counts_quiet_frames_at_the_end():
    audio = np.concatenate([np.full(SR, 0.1), np.zeros(SR // 2)]).astype(np.float32)
    assert abs(trailing_silence_s(audio, SR, 0.005) - 0.5) < 0.03
    assert trailing_silence_s(np.full(SR, 0.1, dtype=np.float32), SR, 0.005) == 0.0
    assert trailing_silence_s(np.zeros(SR, dtype=np.float32), SR, 0.005) == 1.0


class FakeRecorder:
    def __init__(self):
        self.audio = np.zeros(0, dtype=np.float32)
        self.trims = []
        self.on_max = None

    def feed(self, seconds, level=0.1):
        self.audio = np.concatenate([self.audio, np.full(int(seconds * SR), level, np.float32)])

    def snapshot(self):
        return self.audio.copy()

    def trim(self, n):
        self.trims.append(n)
        self.audio = self.audio[n:]

    def start(self):
        pass

    def stop(self):
        return self.audio.copy()


class ScriptedTranscriber:
    def __init__(self, results):
        self.results = list(results)
        self.prompts = []
        self.timestamps = []

    def transcribe_words(self, audio, prompt=None, timestamps=True):
        self.prompts.append(prompt)
        self.timestamps.append(timestamps)
        return self.results.pop(0)


class FakeInserter:
    def __init__(self):
        self.typed = []

    def type_text(self, text):
        self.typed.append(text)


def ws(*texts):
    return [w(t, i * 0.5, i * 0.5 + 0.4) for i, t in enumerate(texts)]


def make(results, **cfg):
    rec, ins = FakeRecorder(), FakeInserter()
    tr = ScriptedTranscriber(results)
    s = Streamer(Config(**cfg), rec, tr, ins)
    return s, rec, tr, ins


def test_streamer_commits_stable_words_then_release_tail():
    s, rec, _, ins = make([
        ws("hello", "big", "world"),
        ws("hello", "big", "world", "how", "are"),
        ws("hello", "big", "world", "how", "are", "you"),  # final
    ], stream_holdback_words=2)
    rec.feed(1.0)
    s.tick()
    assert ins.typed == []
    rec.feed(1.0)
    s.tick()
    assert ins.typed == ["hello big world"]
    rec.feed(1.0)
    s.finish(rec.audio)
    assert ins.typed == ["hello big world", " how are you"]


def test_streamer_skips_pass_without_new_speech():
    s, rec, tr, _ = make([ws("a", "b", "c")])
    rec.feed(1.0)
    s.tick()
    assert len(tr.prompts) == 1
    rec.feed(0.3, level=0.0)
    s.tick()
    assert len(tr.prompts) == 1  # short silence added, no pass


def test_streamer_mismatch_keeps_inserted_text(caplog):
    s, rec, _, ins = make([
        ws("a", "b", "c", "d"),
        ws("a", "b", "c", "d"),
        ws("a", "x", "c", "d", "e"),  # final disagrees at word 2
    ], stream_holdback_words=1)
    rec.feed(1.0)
    s.tick()
    rec.feed(1.0)
    s.tick()
    assert ins.typed == ["a b c"]
    with caplog.at_level("WARNING", logger="whisper_dictation"):
        s.finish(rec.audio)
    assert ins.typed == ["a b c", " d e"]
    assert "disagrees" in caplog.text


def test_streamer_trims_long_buffer_and_passes_prompt():
    first = [w("one", 0, 4), w("two", 4, 10), w("three", 10, 18), w("four", 18, 22)]
    s, rec, tr, ins = make([first, first, ws("four", "five"), ws("four", "five", "six")],
                           stream_holdback_words=1)
    rec.feed(22.0)
    s.tick()
    rec.feed(0.6)
    s.tick()
    # stable and not held back: one, two, three; cut at end of "three" (18 s)
    assert ins.typed == ["one two three"]
    assert rec.trims == [18 * SR]
    assert len(rec.audio) == len(rec.snapshot()) < 5 * SR
    rec.feed(1.0)
    s.tick()
    assert tr.prompts[-1] == "one two three"
    s.finish(rec.audio)
    assert "".join(ins.typed).split() == ["one", "two", "three", "four", "five", "six"]


def test_timestamps_requested_only_for_long_buffers_and_never_for_final():
    first = [w("one", 0, 4), w("two", 4, 10), w("three", 10, 18), w("four", 18, 22)]
    s, rec, tr, _ = make([ws("a", "b"), first, first, ws("c")], stream_holdback_words=1)
    rec.feed(5.0)
    s.tick()                 # short buffer: no timestamps
    rec.feed(17.0)
    s.tick()                 # 22 s buffer: timestamps for the trim
    rec.feed(0.6)
    s.tick()
    s.finish(rec.audio)      # final pass never needs timestamps
    assert tr.timestamps[0] is False
    assert tr.timestamps[1] is True
    assert tr.timestamps[-1] is False


def test_pause_flushes_held_back_words_while_option_is_held():
    s, rec, tr, ins = make([
        ws("hello", "big", "world"),                       # first pass, nothing stable
        ws("hello", "big", "world", "how", "are", "you"),  # pass after the pause
    ], stream_holdback_words=2)
    rec.feed(1.0)
    s.tick()
    assert ins.typed == []
    rec.feed(0.5)                       # speech continues, no pause yet
    rec.feed(0.8, level=0.0)            # pause longer than stream_flush_silence_s
    s.tick()
    assert ins.typed == ["hello big world how are you"]
    assert s.seg_committed == ["hello", "big", "world", "how", "are", "you"]
    rec.feed(1.0, level=0.0)
    s.tick()                            # still silent: no further pass
    assert len(tr.prompts) == 2


def test_short_pause_does_not_flush():
    s, rec, _, ins = make([ws("a", "b", "c"), ws("a", "b", "c", "d")], stream_holdback_words=2)
    rec.feed(1.0)
    s.tick()
    rec.feed(0.4)
    rec.feed(0.4, level=0.0)            # below 0.7 s
    s.tick()
    assert ins.typed == ["a b"]         # normal stable-prefix commit only


def test_flush_never_edits_typed_text_and_release_types_only_new_words():
    s, rec, _, ins = make([
        ws("one", "two", "three"),
        ws("one", "two", "three"),
        ws("one", "two", "three", "four"),   # final after a later word
    ], stream_holdback_words=1)
    rec.feed(1.0)
    s.tick()
    rec.feed(0.8, level=0.0)
    s.tick()
    assert ins.typed == ["one two three"]
    rec.feed(0.6)
    s.finish(rec.audio)
    assert ins.typed == ["one two three", " four"]


def test_cancel_stops_inserting_but_keeps_text():
    s, rec, _, ins = make([ws("a", "b", "c"), ws("a", "b", "c")], stream_holdback_words=0)
    rec.feed(1.0)
    s.tick()
    s.cancel()
    rec.feed(1.0)
    s.tick()
    assert ins.typed == []
    assert s.finish(rec.audio) == []


def test_dictation_streams_and_types_tail_on_release():
    rec, ins = FakeRecorder(), FakeInserter()
    ins.insert = lambda text: ins.typed.append("PASTE " + text)
    tr = ScriptedTranscriber([ws("hello", "there")])
    clock = SimpleNamespace(t=0.0)
    d = Dictation(Config(), rec, tr, ins, clock=lambda: clock.t, run_async=False,
                  sound=lambda *a: None)
    d.on_press()
    rec.feed(1.0)
    clock.t = 1.0
    d.on_release()
    assert ins.typed == ["hello there"]
    assert d.state is State.IDLE


def test_dictation_combo_cancel_stops_streamer():
    rec, ins = FakeRecorder(), FakeInserter()
    d = Dictation(Config(), rec, ScriptedTranscriber([]), ins, run_async=False,
                  sound=lambda *a: None)
    d.on_press()
    streamer = d.streamer
    d.on_other_key()
    assert streamer._cancelled and d.streamer is None
    assert d.state is State.IDLE
