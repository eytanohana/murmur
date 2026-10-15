from types import SimpleNamespace

import numpy as np

from whisper_dictation.app import Dictation, State
from whisper_dictation.config import Config


class FakeRecorder:
    def __init__(self, audio=None):
        self.audio = audio if audio is not None else np.full(16000, 0.1, dtype=np.float32)
        self.started = 0
        self.stopped = 0
        self.on_max = None

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return self.audio


class FakeTranscriber:
    def __init__(self, text="hello world"):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.text


class FakeInserter:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(text="hello world", audio=None):
    clock = Clock()
    rec, tr, ins = FakeRecorder(audio), FakeTranscriber(text), FakeInserter()
    sounds = []
    d = Dictation(Config(stream=False), rec, tr, ins, clock=clock, run_async=False,
                  sound=lambda path, enabled: sounds.append(path))
    return SimpleNamespace(d=d, clock=clock, rec=rec, tr=tr, ins=ins, sounds=sounds)


def test_normal_dictation_inserts_text():
    r = make()
    r.d.on_press()
    assert r.d.state is State.RECORDING
    r.clock.t = 1.0
    r.d.on_release()
    assert r.ins.inserted == ["hello world"]
    assert r.d.state is State.IDLE
    assert r.sounds == []  # sounds are off by default


def test_short_tap_is_ignored():
    r = make()
    r.d.on_press()
    r.clock.t = 0.9
    r.d.on_release()
    assert r.tr.calls == 0
    assert r.ins.inserted == []
    assert r.d.state is State.IDLE


def test_hold_at_threshold_is_accepted():
    r = make()
    r.d.on_press()
    r.clock.t = 1.0
    r.d.on_release()
    assert r.ins.inserted == ["hello world"]


def test_start_sound_plays_only_after_the_hold_threshold():
    import time

    rec, sounds = FakeRecorder(), []
    cfg = Config(stream=False, sounds=True, min_hold_ms=100)
    d = Dictation(cfg, rec, FakeTranscriber(), FakeInserter(), run_async=False,
                  sound=lambda path, enabled: sounds.append(path))
    d.on_press()
    assert sounds == []          # nothing at key down
    time.sleep(0.3)
    assert sounds == [cfg.start_sound]
    d.on_release()
    assert sounds[-1] == cfg.stop_sound


def test_tap_before_threshold_plays_no_sound():
    import time

    rec, sounds = FakeRecorder(), []
    cfg = Config(stream=False, sounds=True, min_hold_ms=200)
    d = Dictation(cfg, rec, FakeTranscriber(), FakeInserter(), run_async=False,
                  sound=lambda path, enabled: sounds.append(path))
    d.on_press()
    d.on_release()
    time.sleep(0.4)
    assert sounds == []


def test_other_key_cancels_recording():
    r = make()
    r.d.on_press()
    r.d.on_other_key()
    assert r.d.state is State.IDLE
    r.clock.t = 2.0
    r.d.on_release()
    assert r.tr.calls == 0
    assert r.ins.inserted == []


def test_empty_transcript_inserts_nothing():
    r = make(text="")
    r.d.on_press()
    r.clock.t = 1.0
    r.d.on_release()
    assert r.tr.calls == 1
    assert r.ins.inserted == []


def test_press_during_transcription_is_ignored():
    r = make()
    r.d.on_press()
    r.clock.t = 1.0
    r.d.state = State.TRANSCRIBING
    r.d.on_press()
    assert r.rec.started == 1


def test_max_length_callback_stops_and_transcribes():
    r = make()
    r.d.on_press()
    r.clock.t = 120.0
    r.rec.on_max()
    assert r.ins.inserted == ["hello world"]
    r.d.on_release()  # later physical release is a no-op
    assert r.tr.calls == 1


def test_transcriber_error_returns_to_idle():
    r = make()
    r.tr.transcribe = lambda audio: (_ for _ in ()).throw(RuntimeError("boom"))
    r.d.on_press()
    r.clock.t = 1.0
    r.d.on_release()
    assert r.d.state is State.IDLE
