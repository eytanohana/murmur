"""Live config keys reach their collaborators; restart keys are classified; no mid-utterance change."""

from dataclasses import fields
from unittest import mock

import numpy as np
from pynput.keyboard import Key

from helpers import FakeRecorder, ScriptedTranscriber, ws
from murmur import indicator, inserter
from murmur.config import LIVE_KEYS, RESTART_KEYS, Config
from murmur.hotkey import HotkeyListener
from murmur.indicator import Overlay
from murmur.recorder import Recorder
from murmur.transcriber import Transcriber

# ---- classification ----------------------------------------------------------


def test_restart_keys_are_exactly_the_ones_that_need_a_reload():
    assert RESTART_KEYS == {"model_repo", "language", "sample_rate", "indicator", "menubar_icon"}


def test_every_key_is_live_or_restart_never_both():
    names = {f.name for f in fields(Config)}
    assert LIVE_KEYS | RESTART_KEYS == names
    assert not LIVE_KEYS & RESTART_KEYS
    assert {
        "min_hold_ms",
        "max_record_s",
        "silence_rms",
        "option_keys",
        "insert_mode",
        "paste_restore_delay_ms",
        "sounds",
        "start_sound",
        "stop_sound",
        "indicator_position",
        "stream",
        "stream_interval",
        "stream_holdback_words",
        "stream_trim_s",
        "stream_min_speech_s",
        "stream_flush_silence_s",
    } == LIVE_KEYS


# ---- each live key reaches the code that uses it --------------------------------


def test_transcriber_reads_silence_rms_from_the_shared_config():
    cfg = Config(silence_rms=0.005)
    t = Transcriber(cfg)
    t._decode = lambda *a, **k: {"text": " hi ", "segments": []}
    clip = np.full(16000, 0.01, dtype=np.float32)
    assert t.transcribe(clip) == "hi"
    cfg.silence_rms = 0.02  # the clip is now below the threshold
    assert t.transcribe(clip) == ""


def test_inserter_reads_mode_and_delay_from_the_shared_config():
    cfg = Config(insert_mode="type", paste_restore_delay_ms=10)
    ins = inserter.Inserter(cfg)
    with (
        mock.patch.object(inserter, "accessibility_trusted", return_value=True),
        mock.patch.object(inserter, "post_text", return_value=2) as typed,
        mock.patch.object(inserter, "post_cmd_v") as pasted,
        mock.patch.object(inserter, "get_clipboard", return_value=b""),
        mock.patch.object(inserter, "set_clipboard"),
        mock.patch.object(inserter.time, "sleep") as sleep,
    ):
        ins.insert("a")
        assert typed.call_count == 1 and pasted.call_count == 0
        cfg.insert_mode, cfg.paste_restore_delay_ms = "paste", 300
        ins.insert("b")
        assert typed.call_count == 1 and pasted.call_count == 1
        sleep.assert_called_with(0.3)


def test_recorder_cap_follows_the_config_at_the_next_recording_only(monkeypatch):
    class FakeStream:
        def __init__(self, **kwargs):
            pass

        start = stop = close = lambda self: None

    monkeypatch.setattr("murmur.recorder.sd.InputStream", FakeStream)
    cfg = Config(max_record_s=300 / 16000)
    rec = Recorder(cfg)
    rec.start()
    cfg.max_record_s = 600 / 16000  # changed mid-recording: this recording keeps its cap
    for _ in range(8):
        rec._callback(np.zeros((100, 1), dtype=np.float32), 100, None, None)
    assert rec.stop().shape == (300,)
    rec.start()  # the next recording picks up the new cap
    for _ in range(8):
        rec._callback(np.zeros((100, 1), dtype=np.float32), 100, None, None)
    assert rec.stop().shape == (600,)


def test_hotkey_follows_option_keys_and_still_releases_a_key_held_during_the_change():
    events = []
    cfg = Config(option_keys="both")
    h = HotkeyListener(
        lambda: events.append("press"), lambda: events.append("release"), lambda: None, cfg
    )
    h.handle_press(Key.alt_l)
    cfg.option_keys = "right"  # edited while the left key is down
    h.handle_release(Key.alt_l)
    assert events == ["press", "release"]
    h.handle_press(Key.alt_l)  # left is no longer a trigger
    assert events == ["press", "release"]
    h.handle_press(Key.alt_r)
    assert events == ["press", "release", "press"]


def test_overlay_reads_the_position_each_time_it_is_shown(monkeypatch):
    class Layer:
        def setBackgroundColor_(self, _):
            pass

    class Box:
        def layer(self):
            return Layer()

        def setStringValue_(self, _):
            pass

    class Panel:
        def __init__(self):
            self.origins = []

        def setFrameOrigin_(self, point):
            self.origins.append((point.x, point.y))

        def orderFrontRegardless(self):
            pass

    rects = ((0, 0, 1000, 800), (0, 0, 1000, 760))
    monkeypatch.setattr(indicator, "menu_bar_screen_rects", lambda: rects)
    cfg = Config(indicator_position="top-left")
    overlay = Overlay(cfg)
    overlay.panel, overlay.dot, overlay.label = Panel(), Box(), Box()
    overlay._apply("recording")
    cfg.indicator_position = "top-right"
    overlay._apply("recording")
    left, right = overlay.panel.origins
    assert left == indicator.overlay_origin(*rects, (78, 26), "top-left")
    assert right == indicator.overlay_origin(*rects, (78, 26), "top-right")
    assert left != right


# ---- nothing changes in the middle of an utterance --------------------------------


def test_dictation_keeps_its_settings_until_the_next_key_down(make_dictation):
    r = make_dictation(sounds=False, min_hold_ms=1000, stream=False)
    r.d.on_press()
    r.d.cfg.sounds = True
    r.d.cfg.min_hold_ms = 5000
    r.d.cfg.stream = True
    r.clock.t = 1.0
    r.d.on_release()  # held 1.0 s: long enough under the 1000 ms this dictation started with
    assert r.ins.inserted == ["hello world"] and r.sounds == [] and r.d.streamer is None
    r.d.on_press()  # the next dictation sees the new values
    assert r.d.streamer is not None and r.schedule.pending[-1][0] == 5.0
    r.schedule.fire()
    assert r.sounds == [r.d.cfg.start_sound]
    r.d.on_other_key()


def test_streamer_gets_a_snapshot_of_the_stream_settings(make_dictation):
    r = make_dictation(stream=True, stream_interval=0.5, stream_holdback_words=1)
    r.d.on_press()
    streamer = r.d.streamer
    r.d.cfg.stream_interval = 9.0
    r.d.cfg.stream_holdback_words = 5
    assert streamer.cfg is not r.d.cfg
    assert (streamer.cfg.stream_interval, streamer.cfg.stream_holdback_words) == (0.5, 1)
    r.d.on_other_key()


def test_a_stream_change_takes_effect_on_the_next_utterance(make_dictation):
    rec = FakeRecorder()
    r = make_dictation(recorder=rec, stream=True)
    r.d.transcriber = ScriptedTranscriber([ws("hello", "there")])
    r.d.on_press()
    r.d.cfg.stream = False  # mid-utterance: still streaming
    rec.feed(1.0)
    r.clock.t = 1.0
    r.d.on_release()
    assert r.ins.typed == ["hello there"] and r.ins.inserted == []
    r.d.on_press()  # the next one does not stream
    assert r.d.streamer is None
