import numpy as np

from murmur.transcriber import Transcriber, clean_text, rms


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


class Fake(Transcriber):
    def __init__(self, result):
        super().__init__("repo")
        self.result = result
        self.word_timestamps = None

    def _decode(self, audio, word_timestamps=False, initial_prompt=None):
        self.word_timestamps = word_timestamps
        return self.result


LOUD = np.full(16000, 0.1, dtype=np.float32)


def test_transcribe_words_without_timestamps_splits_text():
    t = Fake({"text": " the on-call handbook, ok", "segments": []})
    words = t.transcribe_words(LOUD, timestamps=False)
    assert [w.text for w in words] == ["the", "on-call", "handbook,", "ok"]
    assert t.word_timestamps is False


def test_transcribe_words_with_timestamps_joins_split_pieces():
    raw = [{"word": " the", "start": 0.0, "end": 0.2}, {"word": " on", "start": 0.2, "end": 0.4},
           {"word": "-call", "start": 0.4, "end": 0.7}, {"word": " now.", "start": 0.7, "end": 1.0}]
    t = Fake({"text": " the on-call now.", "segments": [{"words": raw}]})
    words = t.transcribe_words(LOUD, timestamps=True)
    assert [w.text for w in words] == ["the", "on-call", "now."]
    assert (words[1].start, words[1].end) == (0.2, 0.7)
    assert t.word_timestamps is True
