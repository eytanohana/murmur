import time

import pytest

from whisper_dictation.app import Dictation, State
from whisper_dictation.config import Config, load_config
from whisper_dictation.indicator import (
    LOOKS,
    NullIndicator,
    Overlay,
    make_indicator,
    overlay_origin,
)

FRAME = (0.0, 0.0, 1800.0, 1169.0)
VISIBLE = (0.0, 76.0, 1800.0, 1056.0)  # Dock at the bottom, menu bar and notch at the top
SIZE = (78.0, 26.0)


def test_top_center_sits_below_the_menu_bar():
    x, y = overlay_origin(FRAME, VISIBLE, SIZE, "top-center", margin=6)
    assert x == (1800 - 78) / 2
    assert y == 76 + 1056 - 26 - 6


def test_corner_positions():
    assert overlay_origin(FRAME, VISIBLE, SIZE, "top-left", 6)[0] == 6
    assert overlay_origin(FRAME, VISIBLE, SIZE, "top-right", 6)[0] == 1800 - 78 - 6
    assert overlay_origin(FRAME, VISIBLE, SIZE, "bottom-left", 6) == (6, 76 + 6)
    assert overlay_origin(FRAME, VISIBLE, SIZE, "bottom-center", 6)[1] == 82


def test_states_map_to_looks_and_idle_hides():
    assert LOOKS["recording"] == ("REC", "red")
    assert LOOKS["transcribing"][0] == "..."
    assert "idle" not in LOOKS


def test_make_indicator_kinds():
    assert isinstance(make_indicator("overlay", "top-left"), Overlay)
    assert make_indicator("overlay", "top-left").position == "top-left"
    assert isinstance(make_indicator("none"), NullIndicator)


def test_overlay_set_before_run_is_ignored():
    Overlay().set("recording")  # no panel yet; must not raise or touch AppKit


def test_config_indicator_defaults_and_alias(tmp_path):
    assert load_config(tmp_path / "none.toml").indicator == "overlay"
    p = tmp_path / "c.toml"
    p.write_text("menubar = true\n")
    assert load_config(p).indicator == "menubar"
    p.write_text('indicator = "none"\nindicator_position = "bottom-right"\n')
    cfg = load_config(p)
    assert (cfg.indicator, cfg.indicator_position) == ("none", "bottom-right")


@pytest.mark.parametrize("line", ['indicator = "dock"', 'indicator_position = "middle"'])
def test_config_rejects_bad_indicator_values(tmp_path, line):
    p = tmp_path / "c.toml"
    p.write_text(line + "\n")
    with pytest.raises(ValueError):
        load_config(p)


class RecordingStatus:
    def __init__(self):
        self.states = []

    def set(self, state):
        self.states.append(state)


class Rec:
    on_max = None

    def start(self):
        pass

    def stop(self):
        import numpy as np

        return np.full(16000, 0.1, dtype=np.float32)


class Tr:
    def transcribe(self, audio):
        return "hi"


class Ins:
    def insert(self, text):
        pass


def make(status, min_hold_ms=80):
    cfg = Config(stream=False, min_hold_ms=min_hold_ms)
    return Dictation(cfg, Rec(), Tr(), Ins(), status, run_async=False)


def test_indicator_shows_recording_only_after_the_hold_threshold():
    status = RecordingStatus()
    d = make(status)
    d.on_press()
    assert status.states == []            # nothing at key down
    time.sleep(0.25)
    assert status.states == ["recording"]
    time.sleep(0.0)
    d.clock = lambda: d._started_at + 1.0
    d.on_release()
    assert status.states == ["recording", "transcribing", "idle"]
    assert d.state is State.IDLE


def test_tap_never_shows_the_indicator():
    status = RecordingStatus()
    d = make(status, min_hold_ms=200)
    d.on_press()
    d.on_release()
    time.sleep(0.4)
    assert "recording" not in status.states
    assert status.states[-1] == "idle"


def test_combo_cancel_after_threshold_hides_the_indicator():
    status = RecordingStatus()
    d = make(status)
    d.on_press()
    time.sleep(0.25)
    d.on_other_key()
    assert status.states == ["recording", "idle"]
