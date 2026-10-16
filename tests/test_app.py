"""Dictation state machine: hold threshold, cancel paths, indicator and sound timing."""

from __future__ import annotations

import logging

from helpers import FakeRecorder, ScriptedTranscriber, ws
from murmur.app import State


def hold(r, seconds: float) -> None:
    r.clock.t = seconds
    r.d.on_release()


def test_normal_dictation_inserts_text(make_dictation):
    r = make_dictation()
    r.d.on_press()
    assert r.d.state is State.RECORDING
    hold(r, 1.0)
    assert r.ins.inserted == ["hello world"]
    assert r.d.state is State.IDLE


def test_short_tap_discards_everything(make_dictation):
    r = make_dictation()
    r.d.on_press()
    hold(r, 0.9)
    assert (r.tr.calls, r.ins.inserted, r.d.state) == (0, [], State.IDLE)


def test_hold_exactly_at_the_threshold_is_accepted(make_dictation):
    r = make_dictation()
    r.d.on_press()
    hold(r, 1.0)
    assert r.ins.inserted == ["hello world"]


def test_other_key_cancels_recording_and_the_later_release_is_a_noop(make_dictation):
    r = make_dictation()
    r.d.on_press()
    r.d.on_other_key()
    assert r.d.state is State.IDLE
    hold(r, 2.0)
    assert (r.tr.calls, r.ins.inserted) == (0, [])


def test_empty_transcript_inserts_nothing(make_dictation):
    r = make_dictation(text="")
    r.d.on_press()
    hold(r, 1.0)
    assert (r.tr.calls, r.ins.inserted) == (1, [])


def test_second_press_while_recording_is_ignored(make_dictation):
    r = make_dictation()
    r.d.on_press()
    r.d.on_press()
    assert r.rec.started == 1
    assert len(r.schedule.pending) == 1


def test_press_during_transcription_is_ignored_and_logged(make_dictation, caplog):
    r = make_dictation()
    r.d.on_press()
    r.d.state = State.TRANSCRIBING
    with caplog.at_level(logging.INFO, logger="murmur"):
        r.d.on_press()
    assert r.rec.started == 1
    assert "Option pressed during transcription; ignored" in caplog.text


def test_max_length_callback_stops_and_transcribes(make_dictation):
    r = make_dictation()
    r.d.on_press()
    r.clock.t = 120.0
    r.rec.on_max()
    assert r.ins.inserted == ["hello world"]
    r.d.on_release()  # the later physical release is a no-op
    assert r.tr.calls == 1


def test_transcriber_error_returns_to_idle(make_dictation):
    r = make_dictation()

    def boom(audio):
        raise RuntimeError("boom")

    r.tr.transcribe = boom
    r.d.on_press()
    hold(r, 1.0)
    assert r.d.state is State.IDLE
    assert r.indicator.states[-1] == "idle"


def test_recorder_failure_leaves_the_machine_idle(make_dictation, caplog):
    r = make_dictation(recorder=FakeRecorder(fail_on_start=OSError("no mic")), stream=True)
    with caplog.at_level(logging.ERROR, logger="murmur"):
        r.d.on_press()
    assert r.d.state is State.IDLE
    assert r.d.streamer is None
    assert r.schedule.pending == []
    assert "Could not start recording" in caplog.text


def test_start_sound_waits_for_the_threshold_and_stop_sound_follows_release(make_dictation):
    r = make_dictation(sounds=True)
    r.d.on_press()
    assert r.sounds == []  # nothing at key down
    assert r.schedule.pending[0][0] == 1.0  # scheduled for min_hold_ms
    r.schedule.fire()
    assert r.sounds == [r.d.cfg.start_sound]
    hold(r, 1.5)
    assert r.sounds == [r.d.cfg.start_sound, r.d.cfg.stop_sound]


def test_no_sounds_by_default(make_dictation):
    r = make_dictation()
    r.d.on_press()
    r.schedule.fire()
    hold(r, 1.5)
    assert r.sounds == []


def test_tap_never_plays_the_start_sound(make_dictation):
    r = make_dictation(sounds=True)
    r.d.on_press()
    hold(r, 0.3)
    r.schedule.fire()  # the timer still fires after the tap
    assert r.sounds == []


def test_indicator_shows_recording_only_after_the_threshold(make_dictation):
    r = make_dictation()
    r.d.on_press()
    assert r.indicator.states == []
    r.schedule.fire()
    assert r.indicator.states == ["recording"]
    hold(r, 1.0)
    assert r.indicator.states == ["recording", "transcribing", "idle"]


def test_stale_threshold_timer_from_a_tap_does_not_light_the_next_recording(make_dictation):
    r = make_dictation()
    r.d.on_press()
    hold(r, 0.2)  # tap
    r.d.on_press()  # a second press starts a new recording
    r.schedule.fire(0)  # the first press's timer fires during the second press
    assert "recording" not in r.indicator.states
    r.schedule.fire(1)
    assert r.indicator.states.count("recording") == 1


def test_combo_cancel_after_the_threshold_hides_the_indicator(make_dictation):
    r = make_dictation()
    r.d.on_press()
    r.schedule.fire()
    r.d.on_other_key()
    assert r.indicator.states == ["recording", "idle"]


def test_streaming_release_types_the_tail(make_dictation):
    rec = FakeRecorder()
    r = make_dictation(recorder=rec, stream=True)
    r.d.transcriber = ScriptedTranscriber([ws("hello", "there")])
    r.d.on_press()
    rec.feed(1.0)
    hold(r, 1.0)
    assert r.ins.typed == ["hello there"]
    assert r.ins.inserted == []  # streamed text is typed, never pasted
    assert r.d.state is State.IDLE


def test_streaming_failure_returns_to_idle(make_dictation):
    rec = FakeRecorder()
    r = make_dictation(recorder=rec, stream=True)
    r.d.transcriber = ScriptedTranscriber([])  # the final pass raises IndexError
    r.d.on_press()
    rec.feed(1.0)
    hold(r, 1.0)
    assert r.d.state is State.IDLE


def test_streaming_combo_cancel_stops_the_streamer(make_dictation):
    r = make_dictation(stream=True)
    r.d.on_press()
    streamer = r.d.streamer
    r.d.on_other_key()
    assert streamer.cancelled and r.d.streamer is None
    assert r.d.state is State.IDLE


def test_streaming_tap_cancels_the_streamer(make_dictation):
    r = make_dictation(stream=True)
    r.d.on_press()
    streamer = r.d.streamer
    hold(r, 0.4)
    assert streamer.cancelled and r.d.streamer is None


def test_the_config_is_refreshed_at_key_down_before_recording_and_the_snapshot(make_dictation):
    r = make_dictation()
    order = []
    r.rec.start = lambda: order.append("recorder.start")

    def refresh():
        order.append("refresh")
        r.d.cfg.sounds = True  # as if the file had just been reloaded

    r.d.refresh = refresh
    r.d.on_press()
    assert order == ["refresh", "recorder.start"]
    r.schedule.fire()
    assert r.sounds == [r.d.cfg.start_sound]  # this very press already uses the new value


def test_presses_that_are_ignored_do_not_refresh(make_dictation):
    r = make_dictation()
    calls = []
    r.d.refresh = lambda: calls.append(1)
    r.d.on_press()
    r.d.on_press()  # already recording
    r.d.state = State.TRANSCRIBING
    r.d.on_press()
    assert calls == [1]


def test_a_failing_refresh_never_blocks_dictation(make_dictation, caplog):
    r = make_dictation()

    def boom():
        raise RuntimeError("refresh failed")

    r.d.refresh = boom
    with caplog.at_level(logging.ERROR, logger="murmur"):
        r.d.on_press()
    assert r.d.state is State.RECORDING and r.rec.started == 1
    assert "Config refresh failed" in caplog.text
