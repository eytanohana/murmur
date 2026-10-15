import numpy as np

from whisper_dictation.transcriber import Transcriber, clean_text, rms


def test_rms_of_empty_and_constant():
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0
    assert abs(rms(np.full(100, 0.5, dtype=np.float32)) - 0.5) < 1e-6


def test_artifact_dropped_only_when_quiet():
    assert clean_text(" Thank you. ", clip_rms=0.001, silence_rms=0.005) == ""
    assert clean_text(" Thank you. ", clip_rms=0.05, silence_rms=0.005) == "Thank you."


def test_text_is_trimmed():
    assert clean_text("  hi there \n", 0.1, 0.005) == "hi there"


def test_silent_clip_skips_model():
    class T(Transcriber):
        def _run(self, audio):
            raise AssertionError("model must not run on silence")

    assert T("repo", silence_rms=0.005).transcribe(np.zeros(16000, dtype=np.float32)) == ""


def test_loud_clip_runs_model_and_cleans():
    class T(Transcriber):
        def _run(self, audio):
            return "  hello "

    assert T("repo").transcribe(np.full(16000, 0.1, dtype=np.float32)) == "hello"
