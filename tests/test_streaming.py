"""Word-level commit logic and the Streamer pass loop."""

from __future__ import annotations

import logging
import threading

import pytest

from helpers import SR, tone, w, ws
from murmur.streaming import commit_target, common_prefix_len, join_words, release_tail

# ---- pure word logic -------------------------------------------------------


def test_common_prefix_ignores_case_and_punctuation():
    assert common_prefix_len(["Hello,", "world"], ["hello", "World.", "again"]) == 2
    assert common_prefix_len(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert common_prefix_len([], ["a"]) == 0


def test_commit_target_needs_two_matching_passes_and_holds_back():
    prev = ["a", "b", "c", "d"]
    cur = ["a", "b", "c", "d", "e"]
    assert commit_target(0, prev, cur, holdback=2) == 3
    assert commit_target(0, [], cur, holdback=2) == 0  # first pass commits nothing
    assert commit_target(0, prev, cur, holdback=0) == 4


def test_commit_target_never_goes_backwards():
    assert commit_target(3, ["a", "x"], ["a", "x", "y"], holdback=1) == 3


def test_join_words_attaches_split_tokens():
    assert join_words(["the", "on", "-call", "handbook,", "ok"]) == "the on-call handbook, ok"


def test_release_tail_appends_words_after_the_prefix():
    assert release_tail(["a", "b"], ["a", "b", "c", "d"]) == (["c", "d"], False)
    assert release_tail(["a", "b"], ["A", "b.", "c"]) == (["c"], False)  # case and punctuation


def test_release_tail_mismatch_keeps_prefix_and_appends_tail():
    assert release_tail(["a", "b"], ["a", "z", "c"]) == (["c"], True)
    assert release_tail(["a", "b", "c"], ["a"]) == ([], True)  # final is shorter than the prefix


# ---- Streamer --------------------------------------------------------------


def test_commits_stable_words_then_types_the_release_tail(make_streamer):
    s, rec, _, ins = make_streamer(
        [
            ws("hello", "big", "world"),
            ws("hello", "big", "world", "how", "are"),
            ws("hello", "big", "world", "how", "are", "you"),  # final
        ],
        stream_holdback_words=2,
    )
    rec.feed(1.0)
    s.tick()
    assert ins.typed == []
    rec.feed(1.0)
    s.tick()
    assert ins.typed == ["hello big world"]
    rec.feed(1.0)
    s.finish(rec.audio)
    assert ins.typed == ["hello big world", " how are you"]  # leading space between chunks


def test_pass_is_skipped_without_enough_new_speech(make_streamer):
    s, rec, tr, _ = make_streamer([ws("a", "b", "c")])
    rec.feed(1.0)
    s.tick()
    rec.feed(0.2, level=0.0)
    s.tick()
    assert len(tr.prompts) == 1
    rec.feed(0.2)  # 0.2 s of speech is below stream_min_speech_s
    s.tick()
    assert len(tr.prompts) == 1


def test_mismatch_keeps_inserted_text_and_logs_a_warning(make_streamer, caplog):
    s, rec, _, ins = make_streamer(
        [
            ws("a", "b", "c", "d"),
            ws("a", "b", "c", "d"),
            ws("a", "x", "c", "d", "e"),  # final disagrees at word 2
        ],
        stream_holdback_words=1,
    )
    rec.feed(1.0)
    s.tick()
    rec.feed(1.0)
    s.tick()
    assert ins.typed == ["a b c"]
    with caplog.at_level(logging.WARNING, logger="murmur"):
        s.finish(rec.audio)
    assert ins.typed == ["a b c", " d e"]
    assert "final text disagrees with committed prefix" in caplog.text


def test_long_buffer_is_trimmed_at_the_last_committed_word_and_prompts_later_passes(
    make_streamer,
):
    first = [w("one", 0, 4), w("two", 4, 10), w("three", 10, 18), w("four", 18, 22)]
    s, rec, tr, ins = make_streamer(
        [first, first, ws("four", "five"), ws("four", "five", "six")], stream_holdback_words=1
    )
    rec.feed(22.0)
    s.tick()
    rec.feed(0.6)
    s.tick()
    assert ins.typed == ["one two three"]  # stable and not held back
    assert rec.trims == [18 * SR]
    assert len(rec.audio) < 5 * SR
    assert s.seg_committed == [] and s.prev_words == []
    rec.feed(1.0)
    s.tick()
    assert tr.prompts[-1] == "one two three"
    s.finish(rec.audio)
    assert "".join(ins.typed).split() == ["one", "two", "three", "four", "five", "six"]


def test_trim_corrects_pass_bookkeeping_by_the_cut(make_streamer):
    s, rec, _, _ = make_streamer([[w("one", 0, 18.0), w("two", 18.0, 22.0)]])
    rec.feed(22.0)
    s.flushed_samples = 20 * SR
    s.last_pass_samples = 22 * SR
    s._maybe_trim([w("one", 0, 18.0)], 22 * SR, rec.audio)  # nothing committed yet: no trim
    assert rec.trims == [] and s.flushed_samples == 20 * SR
    s.seg_committed = ["one"]
    s._maybe_trim([w("one", 0, 18.0)], 22 * SR, rec.audio)
    assert rec.trims == [18 * SR]
    assert s.last_pass_samples == 4 * SR
    assert s.flushed_samples == 2 * SR


def test_word_timestamps_are_requested_only_for_long_buffers_and_never_for_the_final(
    make_streamer,
):
    first = [w("one", 0, 4), w("two", 4, 10), w("three", 10, 18), w("four", 18, 22)]
    s, rec, tr, _ = make_streamer([ws("a", "b"), first, first, ws("c")], stream_holdback_words=1)
    rec.feed(5.0)
    s.tick()
    rec.feed(17.0)
    s.tick()
    rec.feed(0.6)
    s.tick()
    s.finish(rec.audio)
    assert tr.timestamps == [False, True, True, False]


def test_pause_flushes_held_back_words_while_option_is_held(make_streamer):
    s, rec, tr, ins = make_streamer(
        [
            ws("hello", "big", "world"),  # first pass, nothing stable
            ws("hello", "big", "world", "how", "are", "you"),  # pass after the pause
        ],
        stream_holdback_words=2,
    )
    rec.feed(1.0)
    s.tick()
    assert ins.typed == []
    rec.feed(0.5)  # speech continues, no pause yet
    rec.feed(0.8, level=0.0)  # pause longer than stream_flush_silence_s
    s.tick()
    assert ins.typed == ["hello big world how are you"]
    assert s.seg_committed == ["hello", "big", "world", "how", "are", "you"]
    rec.feed(1.0, level=0.0)
    s.tick()  # still silent: no further pass
    assert len(tr.prompts) == 2


def test_short_pause_does_not_flush(make_streamer):
    s, rec, _, ins = make_streamer(
        [ws("a", "b", "c"), ws("a", "b", "c", "d")], stream_holdback_words=2
    )
    rec.feed(1.0)
    s.tick()
    rec.feed(0.4)
    rec.feed(0.4, level=0.0)  # below 0.7 s
    s.tick()
    assert ins.typed == ["a b"]  # normal stable-prefix commit only


def test_pause_without_new_speech_since_the_last_flush_does_not_rerun(make_streamer):
    s, rec, tr, _ = make_streamer([ws("a", "b"), ws("a", "b")])
    rec.feed(0.5)
    rec.feed(0.8, level=0.0)
    s.tick()  # flush
    rec.feed(0.8, level=0.0)
    s.tick()
    assert len(tr.prompts) == 1


def test_flush_and_final_passes_drop_trailing_silence_and_every_pass_drops_leading(
    make_streamer,
):
    s, rec, tr, _ = make_streamer([ws("a"), ws("a"), ws("a")])
    rec.feed(1.0, level=0.0)  # 1 s of leading silence
    rec.feed(0.5)
    rec.feed(1.0, level=0.0)  # 1 s of trailing silence: triggers a flush
    s.tick()
    # leading 1.0 s -> 0.2 s, trailing 1.0 s -> 0.2 s: 0.2 + 0.5 + 0.2 s remain
    assert abs(tr.audio_lengths[0] / SR - 0.9) < 0.05
    s.finish(rec.audio)
    assert abs(tr.audio_lengths[1] / SR - 0.9) < 0.05


def test_normal_pass_keeps_trailing_audio(make_streamer):
    s, rec, tr, _ = make_streamer([ws("a")])
    rec.feed(1.0)
    rec.feed(0.4, level=0.0)  # a short quiet tail below the flush threshold
    s.tick()
    assert abs(tr.audio_lengths[0] / SR - 1.4) < 0.05


def test_leading_silence_shifts_word_times_before_the_trim_cut(make_streamer):
    # 3 s silence + 18 s speech = 21 s: a timestamp pass. Leading silence is cut to 0.2 s,
    # so heard times are 2.8 s earlier than buffer times.
    words = [w("one", 0.0, 5.0), w("two", 5.0, 10.0), w("three", 10.0, 15.0)]
    s, rec, _, _ = make_streamer([words, words], stream_holdback_words=1)
    rec.feed(3.0, level=0.0)
    rec.feed(18.0)
    s.tick()
    rec.feed(0.5)
    s.tick()  # "one" and "two" are stable; the cut is the end of "two"
    assert len(rec.trims) == 1
    assert abs(rec.trims[0] - int((10.0 + 2.8) * SR)) <= int(0.03 * SR)


def test_final_pass_skips_the_model_for_silent_audio(make_streamer):
    s, _, tr, ins = make_streamer([])
    assert s.finish(tone(1.0, 0)) == []
    assert tr.prompts == [] and ins.typed == []


def test_cancel_stops_inserting_keeps_text_and_skips_later_work(make_streamer):
    s, rec, tr, ins = make_streamer([ws("a", "b", "c"), ws("a", "b", "c")], stream_holdback_words=0)
    rec.feed(1.0)
    s.tick()
    s.cancel()
    assert s.cancelled
    rec.feed(1.0)
    s.tick()
    assert len(tr.prompts) == 1  # the cancelled tick never reached the model
    assert ins.typed == []
    assert s.finish(rec.audio) == []
    assert len(tr.prompts) == 1


def test_cancel_during_a_pass_discards_its_words(make_streamer):
    holder = {}
    s, rec, _, ins = make_streamer(
        [ws("a", "b", "c"), ws("a", "b", "c")],
        on_call=lambda: holder["s"].cancel() if len(holder["tr"].prompts) == 2 else None,
        stream_holdback_words=0,
    )
    holder["s"], holder["tr"] = s, s.transcriber
    rec.feed(1.0)
    s.tick()  # first pass: nothing stable yet
    rec.feed(1.0)
    s.tick()  # cancelled while the model runs
    assert ins.typed == []
    assert s.seg_committed == []  # a dropped emit must not advance the committed prefix


def test_failed_emit_is_not_counted_as_committed(make_streamer):
    s, _, _, ins = make_streamer([ws("a")])
    s.cancel()
    assert s._emit(["a"]) is False
    assert ins.typed == [] and s.total_committed == 0 and not s.inserted_any


def test_loop_runs_the_first_pass_after_the_hold_threshold_not_the_interval(make_streamer):
    s, _, _, _ = make_streamer([], min_hold_ms=50, stream_interval=30.0)
    ticked = threading.Event()
    s.tick = ticked.set
    s.start()
    try:
        assert ticked.wait(2.0)  # would take 30 s if the first deadline used the interval
    finally:
        s.cancel()


def test_loop_repeats_every_interval_and_survives_a_failing_pass(make_streamer):
    s, _, _, _ = make_streamer([], min_hold_ms=10, stream_interval=0.02)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("pass failed")
        if len(calls) >= 3:
            done.set()

    done = threading.Event()
    s.tick = flaky
    s.start()
    try:
        assert done.wait(2.0)
    finally:
        s.cancel()


@pytest.mark.parametrize("silent", [True, False])
def test_finish_without_any_committed_words_types_the_whole_final_text(make_streamer, silent):
    s, _, _, ins = make_streamer([ws("hello", "there")])
    audio = tone(1.0, 0 if silent else 0.1)
    s.finish(audio)
    assert ins.typed == ([] if silent else ["hello there"])


# ---- quiet speech: the final pass must not be gated on the whole recording ----------------------------------


def quiet_clip(level, lead_s=3.0, speech_s=2.0):
    import numpy as np

    from helpers import tone

    return np.concatenate([tone(lead_s, 0.0), tone(speech_s, level)])


def test_the_final_pass_runs_when_speech_is_quiet_but_the_recording_is_mostly_silence(
    make_streamer,
):
    # Speech at RMS 0.0065 is above the 0.005 threshold, but the whole recording (3 s of quiet
    # lead from the hold, then 2 s of speech) averages 0.0041. The streaming passes gate on the
    # trimmed audio and heard it; the final pass used to gate on the whole clip and skip it, so
    # the held-back words were never typed.
    from murmur.audio import rms

    audio = quiet_clip(0.0065)
    assert rms(audio) < 0.005  # diluted below the threshold by the silence around the speech
    s, _, tr, ins = make_streamer([ws("one", "two")])
    assert s.finish(audio) == ["one", "two"]
    assert ins.typed == ["one two"] and len(tr.audio_lengths) == 1
    assert 2.0 < tr.audio_lengths[0] / SR < 3.0  # the edges were trimmed, the speech was kept


def test_speech_below_the_threshold_is_still_skipped_and_the_log_says_how_loud_it_was(
    make_streamer, caplog
):
    s, _, tr, ins = make_streamer([ws("never")])
    with caplog.at_level(logging.INFO, logger="murmur"):
        assert s.finish(quiet_clip(0.003)) == []
    assert tr.prompts == [] and ins.typed == []
    assert "Nothing heard" in caplog.text and "RMS 0.0030" in caplog.text
    assert "silence threshold is 0.0050" in caplog.text and "input volume" in caplog.text


def test_the_nothing_heard_note_is_not_logged_when_words_were_typed(make_streamer, caplog):
    s, rec, _, _ = make_streamer([ws("a", "b", "c"), ws("a", "b", "c", "d"), []])
    rec.feed(1.0)
    s.tick()
    rec.feed(1.0)
    s.tick()
    with caplog.at_level(logging.INFO, logger="murmur"):
        s.finish(rec.audio)
    assert "Nothing heard" not in caplog.text
