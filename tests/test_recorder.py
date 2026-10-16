"""Recorder buffer logic, driven through the audio callback (no input device)."""

import threading

import numpy as np

from murmur.recorder import Recorder

SR = 16000


def feed(rec: Recorder, values: list[float], frames: int = 100) -> None:
    for v in values:
        rec._callback(np.full((frames, 1), v, dtype=np.float32), frames, None, None)


def test_stop_returns_everything_captured_as_mono_float32():
    rec = Recorder(SR, 10)
    feed(rec, [0.1, 0.2, 0.3])
    audio = rec.stop()
    assert audio.dtype == np.float32 and audio.shape == (300,)
    assert list(audio[[0, 100, 200]]) == [np.float32(0.1), np.float32(0.2), np.float32(0.3)]


def test_stop_with_nothing_captured_gives_an_empty_array():
    assert Recorder(SR, 10).stop().size == 0


def test_snapshot_is_a_copy_and_recording_continues():
    rec = Recorder(SR, 10)
    feed(rec, [0.1])
    snap = rec.snapshot()
    snap[:] = 9.0  # a caller scribbling on its copy must not touch the buffer
    feed(rec, [0.2])
    assert np.allclose(rec.snapshot()[:100], 0.1)
    assert rec.snapshot().shape == (200,)
    assert Recorder(SR, 10).snapshot().size == 0


def test_trim_drops_leading_samples_and_keeps_later_audio():
    rec = Recorder(SR, 10)
    feed(rec, [0.1, 0.2])
    rec.trim(100)
    assert np.allclose(rec.snapshot(), 0.2) and rec.snapshot().shape == (100,)
    rec.trim(100)
    assert rec.snapshot().size == 0
    rec.trim(5)  # trimming an empty buffer is harmless


def test_capture_stops_at_the_cap_and_on_max_fires_once():
    fired = threading.Event()
    calls = []
    rec = Recorder(SR, max_seconds=300 / SR, on_max=lambda: (calls.append(1), fired.set()))
    feed(rec, [0.1] * 5)  # 500 samples offered, cap is 300
    assert fired.wait(2)
    assert rec.stop().shape == (300,)
    assert len(calls) == 1


def test_trim_does_not_extend_the_cap():
    rec = Recorder(SR, max_seconds=300 / SR)
    feed(rec, [0.1] * 3)
    rec.trim(200)
    feed(rec, [0.2])  # total captured is already at the cap: ignored
    assert rec.snapshot().shape == (100,)


def test_a_second_recording_starts_from_a_clean_buffer(monkeypatch):
    class FakeStream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("murmur.recorder.sd.InputStream", FakeStream)
    rec = Recorder(SR, max_seconds=300 / SR, on_max=lambda: None)
    feed(rec, [0.1] * 4)
    rec.stop()
    rec.start()
    feed(rec, [0.2] * 2)
    assert np.allclose(rec.stop(), 0.2) and rec.snapshot().size == 0
    rec.start()
    feed(rec, [0.3] * 3)  # a full cap again: the counter was reset by start()
    assert rec.stop().shape == (300,)


def test_input_status_warns_once_per_recording(monkeypatch, caplog):
    class FakeStream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("murmur.recorder.sd.InputStream", FakeStream)
    rec = Recorder(SR, 10)
    block = np.zeros((100, 1), dtype=np.float32)
    with caplog.at_level("WARNING", logger="murmur"):
        rec.start()
        rec._callback(block, 100, None, None)  # clean buffer: no warning
        assert caplog.text == ""
        rec._callback(block, 100, None, "input overflow")
        rec._callback(block, 100, None, "input overflow")
        assert caplog.text.count("Audio input problem (input overflow)") == 1
        rec.stop()
        rec.start()
        rec._callback(block, 100, None, "input overflow")  # a new recording warns again
    assert caplog.text.count("Audio input problem") == 2
