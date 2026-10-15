"""Dictation state machine: IDLE -> RECORDING -> TRANSCRIBING -> IDLE.

Policy for a press during transcription: it is ignored and a log line is
printed. Jobs are never queued, so a slow clip cannot build a backlog.

Audio is buffered from key down, but nothing is typed and no start sound plays
until the key has been held for min_hold_ms; a shorter hold discards everything.

With cfg.stream (default) a Streamer types stable words while Option is held
and the release only types the tail. With stream = false the whole clip is
transcribed on release and inserted with insert_mode.
"""

from __future__ import annotations

import enum
import logging
import threading
import time

from .config import Config
from .feedback import NullStatus, play_sound
from .streaming import Streamer

log = logging.getLogger("murmur")


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


class Dictation:
    def __init__(
        self,
        cfg: Config,
        recorder,
        transcriber,
        inserter,
        status=None,
        clock=time.monotonic,
        run_async: bool = True,
        sound=play_sound,
    ):
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
        self.streamer: Streamer | None = None
        self._press_id = 0
        # Max-length stop is wired here so the recorder can call back into us.
        recorder.on_max = self.on_release

    def _set_state(self, state: State) -> None:
        self.state = state
        # The indicator shows "recording" only once the hold threshold passes
        # (see _threshold_passed), not at key down.
        if state is not State.RECORDING:
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
                log.error(
                    "Could not start recording (%s). Check Microphone permission "
                    "in System Settings > Privacy & Security.",
                    exc,
                )
                return
            self._started_at = self.clock()
            self._set_state(State.RECORDING)
            if self.cfg.stream:
                self.streamer = Streamer(
                    self.cfg, self.recorder, self.transcriber, self.inserter, self.clock
                )
                self.streamer.start()
            self._press_id += 1
            # Marks that the hold threshold passed (indicator and optional sound).
            timer = threading.Timer(
                self.cfg.min_hold_ms / 1000, self._threshold_passed, args=(self._press_id,)
            )
            timer.daemon = True
            timer.start()

    def _threshold_passed(self, press_id: int) -> None:
        with self._lock:
            if self.state is not State.RECORDING or press_id != self._press_id:
                return
        self.status.set("recording")
        if self.cfg.sounds:
            self.sound(self.cfg.start_sound, True)

    def on_other_key(self) -> None:
        with self._lock:
            if self.state is not State.RECORDING:
                return
            self.recorder.stop()
            if self.streamer:
                self.streamer.cancel()
                self.streamer = None
            self._set_state(State.IDLE)
        log.info("Other key pressed with Option held; recording cancelled")

    def on_release(self) -> None:
        with self._lock:
            if self.state is not State.RECORDING:
                return
            held = self.clock() - self._started_at
            audio = self.recorder.stop()
            if held * 1000 < self.cfg.min_hold_ms:
                if self.streamer:
                    self.streamer.cancel()
                    self.streamer = None
                self._set_state(State.IDLE)
                log.debug("Hold of %.0f ms below minimum; ignored", held * 1000)
                return
            self._set_state(State.TRANSCRIBING)
        if self.cfg.sounds:
            self.sound(self.cfg.stop_sound, True)
        streamer, self.streamer = self.streamer, None
        job = self._process_stream if streamer else self._process
        args = (streamer, audio) if streamer else (audio, held)
        if self.run_async:
            threading.Thread(target=job, args=args, daemon=True).start()
        else:
            job(*args)

    def _process_stream(self, streamer: Streamer, audio) -> None:
        try:
            t0 = self.clock()
            streamer.finish(audio)
            passes = streamer.pass_times
            log.info(
                "streamed %d words, %d passes, final pass %.2fs, release wait %.2fs",
                streamer.total_committed,
                len(passes),
                passes[-1] if passes else 0.0,
                self.clock() - t0,
            )
        except Exception:
            log.exception("Streaming finish failed")
        finally:
            with self._lock:
                self._set_state(State.IDLE)

    def _process(self, audio, held: float) -> None:
        try:
            t0 = self.clock()
            text = self.transcriber.transcribe(audio)
            took = self.clock() - t0
            log.info(
                "recorded %.2fs, transcribed in %.2fs, %d chars",
                len(audio) / self.cfg.sample_rate,
                took,
                len(text),
            )
            if text:
                self.inserter.insert(text)
            else:
                log.info("Silence or empty transcript; nothing inserted")
        except Exception:
            log.exception("Transcription or insertion failed")
        finally:
            with self._lock:
                self._set_state(State.IDLE)
