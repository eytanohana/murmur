"""Thin wrapper over mlx_whisper with model preloading and silence filtering."""

from __future__ import annotations

import numpy as np

# Whisper tends to emit these on silent or near-silent audio.
SILENCE_ARTIFACTS = {
    "thank you.",
    "thank you",
    "thanks for watching.",
    "thanks for watching!",
    "you",
    "bye.",
    "bye",
}


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def clean_text(text: str, clip_rms: float, silence_rms: float) -> str:
    """Trim the transcript and drop known artifacts, only when the clip is quiet."""
    text = text.strip()
    if clip_rms < silence_rms and text.lower() in SILENCE_ARTIFACTS:
        return ""
    return text


class Transcriber:
    def __init__(self, model_repo: str, language: str = "en", silence_rms: float = 0.005):
        self.model_repo = model_repo
        self.language = language
        self.silence_rms = silence_rms

    def _run(self, audio: np.ndarray) -> str:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model_repo,
            language=self.language,
            condition_on_previous_text=False,
            fp16=True,
            verbose=None,
        )
        return result.get("text", "")

    def warm_up(self, sample_rate: int = 16000) -> None:
        """Download (first run) and load the model by transcribing 1 s of silence."""
        self._run(np.zeros(sample_rate, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        audio = np.asarray(audio, dtype=np.float32)
        clip_rms = rms(audio)
        if clip_rms < self.silence_rms:
            return ""
        return clean_text(self._run(audio), clip_rms, self.silence_rms)
