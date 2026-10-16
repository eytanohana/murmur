"""Self-check for the frozen app: transcribe a wav file through the bundled interpreter.

Set MURMUR_SELFTEST_WAV=/path/to/clip.wav (16 kHz mono). The app then skips the permission
prompts and the hotkey listener, starts normally otherwise (so the status item can be
inspected), prints the transcript, stays up for MURMUR_SELFTEST_HOLD seconds (default 3)
and quits through SIGTERM like a launchd stop. It exists because nothing else exercises
mlx, numpy and PortAudio inside the frozen bundle without a microphone.
"""

from __future__ import annotations

import logging
import os
import signal
import time
import wave

import numpy as np

log = logging.getLogger("murmur")

ENV_WAV = "MURMUR_SELFTEST_WAV"
ENV_HOLD = "MURMUR_SELFTEST_HOLD"


def requested(environ=os.environ) -> str | None:
    return environ.get(ENV_WAV) or None


def read_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return raw.astype(np.float32) / 32768


def run(wav: str, transcriber, environ=os.environ) -> None:
    """Thread body: transcribe, report, wait, then ask the app to quit."""
    try:
        text = transcriber.transcribe(read_wav(wav))
        log.info("SELFTEST transcript: %s", text)
        print(f"SELFTEST transcript: {text}", flush=True)
    except Exception:
        log.exception("SELFTEST failed")
        print("SELFTEST failed", flush=True)
    time.sleep(float(environ.get(ENV_HOLD, "3")))
    os.kill(os.getpid(), signal.SIGTERM)
