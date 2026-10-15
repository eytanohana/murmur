"""Dictation state machine: IDLE -> RECORDING -> TRANSCRIBING -> IDLE.

Policy for a press during transcription: it is ignored and a log line is
printed. Jobs are never queued, so a slow clip cannot build a backlog.
"""

from __future__ import annotations

import enum
import logging
import threading
import time

from .config import Config
from .feedback import NullStatus, play_sound

log = logging.getLogger("whisper_dictation")


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


class Dictation:
    def __init__(self, cfg: Config, recorder, transcriber, inserter, status=None,
                 clock=time.monotonic, run_async: bool = True, sound=play_sound):
        self.cfg = cfg
        self.recorder = recorder
        self.transcriber = transcriber
        self.inserter = inserter
        self.status = status or NullStatus()
        self.clock = clock
        self.run_async = run_async
        self.sound = sound
        self.state = State.IDLE
        self._lock = threading.Lock()
        self._started_at = 0.0
        # Max-length stop is wired here so the recorder can call back into us.
        recorder.on_max = self.on_release

    def _set_state(self, state: State) -> None:
        self.state = state
        self.status.set(state.value)

    def on_press(self) -> None:
        with self._lock:
            if self.state is State.TRANSCRIBING:
                log.info("Option pressed during transcription; ignored")
                return
            if self.state is not State.IDLE:
                return
            try:
                self.recorder.start()
            except Exception as exc:  # noqa: BLE001
                log.error("Could not start recording (%s). Check Microphone permission "
                          "in System Settings > Privacy & Security.", exc)
                return
            self._started_at = self.clock()
            self._set_state(State.RECORDING)
        self.sound(self.cfg.start_sound, self.cfg.sounds)

    def on_other_key(self) -> None:
        with self._lock:
            if self.state is not State.RECORDING:
                return
            self.recorder.stop()
            self._set_state(State.IDLE)
        log.info("Other key pressed with Option held; recording cancelled")

    def on_release(self) -> None:
        with self._lock:
            if self.state is not State.RECORDING:
                return
            held = self.clock() - self._started_at
            audio = self.recorder.stop()
            if held * 1000 < self.cfg.min_hold_ms:
                self._set_state(State.IDLE)
                log.debug("Hold of %.0f ms below minimum; ignored", held * 1000)
                return
            self._set_state(State.TRANSCRIBING)
        self.sound(self.cfg.stop_sound, self.cfg.sounds)
        if self.run_async:
            threading.Thread(target=self._process, args=(audio, held), daemon=True).start()
        else:
            self._process(audio, held)

    def _process(self, audio, held: float) -> None:
        try:
            t0 = self.clock()
            text = self.transcriber.transcribe(audio)
            took = self.clock() - t0
            log.info("recorded %.2fs, transcribed in %.2fs, %d chars",
                     len(audio) / self.cfg.sample_rate, took, len(text))
            if text:
                self.inserter.insert(text)
            else:
                log.info("Silence or empty transcript; nothing inserted")
        except Exception:
            log.exception("Transcription or insertion failed")
        finally:
            with self._lock:
                self._set_state(State.IDLE)
