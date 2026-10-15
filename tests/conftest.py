from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import (
    FakeInserter,
    FakeRecorder,
    FakeTranscriber,
    ManualClock,
    ManualScheduler,
    RecordingStatus,
    ScriptedTranscriber,
)
from murmur.app import Dictation
from murmur.config import Config
from murmur.streaming import Streamer


@pytest.fixture
def make_dictation():
    """Build a Dictation wired to fakes. Returns a namespace with every collaborator."""

    def build(text: str = "hello world", recorder: FakeRecorder | None = None, **cfg):
        cfg.setdefault("stream", False)
        clock, schedule, status = ManualClock(), ManualScheduler(), RecordingStatus()
        rec = recorder or FakeRecorder()
        tr, ins, sounds = FakeTranscriber(text), FakeInserter(), []
        dictation = Dictation(
            Config(**cfg),
            rec,
            tr,
            ins,
            status,
            clock=clock,
            run_async=False,
            sound=sounds.append,
            schedule=schedule,
        )
        return SimpleNamespace(
            d=dictation,
            clock=clock,
            schedule=schedule,
            status=status,
            rec=rec,
            tr=tr,
            ins=ins,
            sounds=sounds,
        )

    return build


@pytest.fixture
def make_streamer():
    """Build a Streamer over a scripted transcriber. Returns (streamer, recorder, tr, ins)."""

    def build(results, on_call=None, **cfg):
        rec, ins = FakeRecorder(), FakeInserter()
        tr = ScriptedTranscriber(results, on_call)
        return Streamer(Config(**cfg), rec, tr, ins), rec, tr, ins

    return build
