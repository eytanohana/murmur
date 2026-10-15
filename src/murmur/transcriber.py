"""Thin wrapper over mlx_whisper with model preloading and silence filtering."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import rms


@dataclass
class Word:
    text: str
    start: float
    end: float


class Transcriber:
    def __init__(self, model_repo: str, language: str = "en", silence_rms: float = 0.005):
        self.model_repo = model_repo
        self.language = language
        self.silence_rms = silence_rms

    def _run(self, audio: np.ndarray) -> str:
        return self._decode(audio)["text"]

    def _decode(
        self, audio: np.ndarray, word_timestamps: bool = False, initial_prompt: str | None = None
    ) -> dict:
        import mlx_whisper

        return mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model_repo,
            language=self.language,
            condition_on_previous_text=False,
            fp16=True,
            verbose=None,
            word_timestamps=word_timestamps,
            initial_prompt=initial_prompt,
        )

    def warm_up(self, sample_rate: int = 16000) -> None:
        """Download (first run) and load the model by transcribing 1 s of silence."""
        silence = np.zeros(sample_rate, dtype=np.float32)
        self._run(silence)
        self._decode(silence, word_timestamps=True)

    def transcribe(self, audio: np.ndarray) -> str:
        audio = np.asarray(audio, dtype=np.float32)
        clip_rms = rms(audio)
        if clip_rms < self.silence_rms:
            return ""
        return self._run(audio).strip()

    def transcribe_words(
        self, audio: np.ndarray, initial_prompt: str | None = None, timestamps: bool = True
    ) -> list[Word]:
        """Transcribe into words. Silent clips and artifacts give no words.

        Word timestamps cost about 0.4 s per call on top of a 0.4 s base, so callers
        that do not need them pass timestamps=False; start and end are then 0.
        """
        audio = np.asarray(audio, dtype=np.float32)
        clip_rms = rms(audio)
        if clip_rms < self.silence_rms:
            return []
        result = self._decode(audio, word_timestamps=timestamps, initial_prompt=initial_prompt)
        text = result.get("text", "").strip()
        if not text:
            return []
        if not timestamps:
            return [Word(t, 0.0, 0.0) for t in text.split()]
        words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                raw = w["word"]
                piece = raw.strip()
                if not piece:
                    continue
                if words and not raw.startswith(" "):
                    # Pieces such as "-call" join the previous word, matching the
                    # whitespace split used when timestamps are off.
                    words[-1] = Word(words[-1].text + piece, words[-1].start, float(w["end"]))
                else:
                    words.append(Word(piece, float(w["start"]), float(w["end"])))
        return words
