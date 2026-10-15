# whisper-dictation design

Push-to-talk dictation for macOS. Hold the Option key, speak, release; the transcript is typed into whichever app has focus. Everything runs locally.

Target machine: 16-inch MacBook Pro, M4 Pro, 48 GB unified memory, macOS (Darwin 24.x). English only.

## Model choice

Use Whisper large-v3-turbo through `mlx-whisper` (Apple MLX port of OpenAI Whisper), with `language="en"` forced.

- 48 GB of memory fits large-v3 class models with no pressure (the turbo model is about 1.6 GB in fp16).
- large-v3-turbo has 4 decoder layers instead of 32, so it decodes several times faster than large-v3 at close to the same accuracy. For short dictation clips this keeps latency low.
- The OpenAI `.en` checkpoints stop at medium.en. large-v3-turbo is multilingual, but forcing English gives better accuracy than medium.en in practice and avoids language-detection errors on short clips.
- The reference `openai-whisper` PyTorch package runs on CPU on Apple Silicon (MPS support is incomplete), so MLX is the right runtime. It still runs the OpenAI Whisper weights.

Default model repo: `mlx-community/whisper-large-v3-turbo`. Make the repo a config value. Fallback if quality or speed disappoints: `mlx-community/whisper-medium.en-mlx`. The implementer must verify these repo ids exist on Hugging Face before hard-coding them.

## Behavior

1. A background process listens globally for the Option key.
2. Option down starts recording from the default input device (16 kHz, mono, float32).
3. Option up stops recording and transcribes the buffer.
4. The text is inserted at the cursor of the focused app.

## Streaming (default)

Whisper is not a streaming model, so while Option is held the app re-transcribes the growing buffer and types words once they are stable. `stream = false` restores the release-only behavior above.

1. Every `stream_interval` seconds (0.5) a pass runs on the current utterance buffer, on one worker thread. Passes never overlap; a slow pass delays the next.
2. Words that match in the same position on two consecutive passes (case and punctuation ignored) are stable. Only new stable words are typed, immediately. Typed text is never deleted or edited.
3. The last `stream_holdback_words` words (1) of the newest pass are held back until they stabilize with more audio or Option is released.
4. On release, one final pass runs on the full buffer. Only the words after the committed prefix are typed. If the final text disagrees with the committed prefix, the typed text stays, only the tail is appended, and the mismatch is logged.
5. When the buffer exceeds `stream_trim_s` (20 s), it is cut at the end of the last committed word (word timestamps), the audio before the cut is dropped, and the committed text is passed as `initial_prompt` for later passes.
6. Streamed chunks use type mode (no clipboard use), with a leading space between chunks. The non-stream path keeps `insert_mode`.
7. Silence gate, minimum hold, combo cancel and max length still apply. Combo cancel stops streaming; text already typed stays. The first pass runs right after the minimum hold.
8. A pass is skipped unless the audio added since the last pass has at least `stream_min_speech_s` (0.3 s) of frames above the silence threshold.
9. Pause flush: when the buffer ends in `stream_flush_silence_s` (0.7 s) of silence and there is unflushed speech, a pass commits every word it heard (no holdback, no second pass), so a sentence end is typed while Option is held. Leading silence is cut to 0.2 s on every pass, and trailing silence on flush and final passes, because Whisper invents words over long silence.

Passes decode plain text; word timestamps (0.4 s extra) are requested only for the pass that trims a long buffer. See the README for the latency measurements and the rejected tail-only and fast-commit variants.

Known limits: punctuation on the last committed word can still change in the final pass and is not corrected, because typed text is never edited. Each pass costs a full decode of the buffer, so the GPU stays busy while Option is held.

Edge cases to handle:

- Minimum hold: ignore holds shorter than 1000 ms (`min_hold_ms`). A tap on Option must never start dictation. Audio is buffered from key down so the first second is kept, but nothing is typed and no sound plays before the threshold; a shorter hold discards the audio.
- Combo cancel: if any other key is pressed while Option is held (Option+Arrow, Option+letter), cancel the recording and discard it.
- Maximum length: cap a recording at 120 s, then stop and transcribe.
- Silence: if the clip is near silent (RMS below a threshold) or the transcript is empty, insert nothing.
- Hallucinations: pass `condition_on_previous_text=False` and trim the result. Drop known Whisper silence artifacts such as "Thank you." only when the clip RMS is below the silence threshold.
- Left and right Option both trigger by default; configurable.
- A new press during transcription queues behind the running job, or is ignored with a log line. Pick one and document it.

## Text insertion

Default: save the clipboard, set the clipboard to the transcript, send Cmd+V, wait about 150 ms, restore the previous clipboard. This is reliable across apps and handles Unicode. Config option `insert_mode = "type"` to type characters with synthetic key events instead.

Both modes post Quartz keyboard events (`CGEventPost`), not pynput's `Controller`, because pynput typing is unsafe off the main thread on recent macOS. Text goes out as Unicode strings in chunks of 20 UTF-16 units with flags cleared so a held Option key has no effect; Cmd+V carries only the Command flag. The hotkey listener ignores injected key events and hands callbacks to a dispatcher thread so the event tap is never blocked. Accessibility trust is checked with `AXIsProcessTrusted` and logged, since macOS drops posted events silently without it.

## Feedback

- Start and stop sounds using the system sounds in `/System/Library/Sounds` (`afplay`, non-blocking). `sounds` defaults to false because the macOS mic indicator shows recording. The start sound plays when the hold threshold passes, the stop sound on release.
- Optional menu bar item (`rumps`) showing idle, recording, transcribing. Build it second, behind a flag, after the core loop works.

## Architecture

```
src/whisper_dictation/
  __main__.py     entry point, `python -m whisper_dictation` and `uv run whisper-dictation`
  config.py       dataclass plus optional ~/.config/whisper-dictation/config.toml
  hotkey.py       global Option key listener (pynput or Quartz event tap), emits press/release/other-key events
  recorder.py     sounddevice InputStream into a numpy buffer
  transcriber.py  mlx_whisper.transcribe wrapper, model preloaded and warmed at startup
  inserter.py     clipboard paste and type modes
  app.py          state machine: IDLE -> RECORDING -> TRANSCRIBING -> IDLE
  streaming.py    stable-prefix commit logic and the per-utterance Streamer
  feedback.py     sounds, menu bar
  agent.py        LaunchAgent plist and launchctl commands
tests/            pytest; unit tests for state machine, min-hold, combo cancel, silence gate, with hotkey/audio/inserter mocked
```

Notes:

- Load and warm the model once at startup (run a 1 s silent transcription) so the first dictation is not slow.
- Transcription runs on a worker thread; the hotkey listener thread never blocks.
- Hotkey detection for a modifier alone needs modifier flag changes. With pynput use `Key.alt`, `Key.alt_l`, `Key.alt_r`.
- macOS permissions needed by the terminal app running this: Accessibility (synthetic Cmd+V), Input Monitoring (global key listening), Microphone. The app should detect a missing permission where possible and print which one to grant in System Settings > Privacy & Security.

## Run at login

`whisper-dictation install-agent`, `uninstall-agent` and `agent-status` manage a LaunchAgent (`com.user.whisper-dictation`) built by `agent.py`. It runs the venv python directly so the privacy permission target is one stable binary, restarts only after a non-zero exit, logs to `~/Library/Logs/whisper-dictation.log` and sets `HF_HUB_OFFLINE=1`. Permissions attach to the resolved python binary when there is no terminal; the app requests the prompts at startup and names the binary in warnings. See the README for idle cost and the re-grant steps after a Python upgrade.

## Tooling

- Python managed by uv: `uv init`, `uv add`, `uv run`. Pin Python 3.12 via `.python-version`.
- Dependencies: `mlx-whisper`, `sounddevice`, `numpy`, `pynput`, `pyobjc-framework-Quartz` if needed, `rumps` (optional extra). Dev: `pytest`, `ruff`.
- `uv.lock` is committed. `.venv/`, `__pycache__/`, model caches are git ignored.
- `ffmpeg` is not required if audio is passed to mlx-whisper as a numpy array; avoid the dependency.

## Git

Repo is already initialized on branch `main` in this folder. Commit in small, logical steps (scaffold, config, recorder, transcriber, hotkey, inserter, app loop, tests, README). Messages state what changed and why in plain prose, no emdashes.

## Acceptance

- `uv run whisper-dictation` starts, prints that the model is loaded, then dictation works in TextEdit and a browser text field.
- Timing log per dictation: record duration, transcription time.
- `uv run pytest` and `uv run ruff check` pass.
- README covers setup, permissions, config options, and how to swap the model.
