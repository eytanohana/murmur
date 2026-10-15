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

Edge cases to handle:

- Minimum hold: ignore holds shorter than 300 ms (configurable). Option is used for typing symbols, so short taps must do nothing.
- Combo cancel: if any other key is pressed while Option is held (Option+Arrow, Option+letter), cancel the recording and discard it.
- Maximum length: cap a recording at 120 s, then stop and transcribe.
- Silence: if the clip is near silent (RMS below a threshold) or the transcript is empty, insert nothing.
- Hallucinations: pass `condition_on_previous_text=False` and trim the result. Drop known Whisper silence artifacts such as "Thank you." only when the clip RMS is below the silence threshold.
- Left and right Option both trigger by default; configurable.
- A new press during transcription queues behind the running job, or is ignored with a log line. Pick one and document it.

## Text insertion

Default: save the clipboard, set the clipboard to the transcript, send Cmd+V, wait about 150 ms, restore the previous clipboard. This is reliable across apps and handles Unicode. Config option `insert_mode = "type"` to type characters with synthetic key events instead.

## Feedback

- Start and stop sounds using the system sounds in `/System/Library/Sounds` (`afplay`, non-blocking). Configurable off.
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
  feedback.py     sounds, menu bar
tests/            pytest; unit tests for state machine, min-hold, combo cancel, silence gate, with hotkey/audio/inserter mocked
```

Notes:

- Load and warm the model once at startup (run a 1 s silent transcription) so the first dictation is not slow.
- Transcription runs on a worker thread; the hotkey listener thread never blocks.
- Hotkey detection for a modifier alone needs modifier flag changes. With pynput use `Key.alt`, `Key.alt_l`, `Key.alt_r`.
- macOS permissions needed by the terminal app running this: Accessibility (synthetic Cmd+V), Input Monitoring (global key listening), Microphone. The app should detect a missing permission where possible and print which one to grant in System Settings > Privacy & Security.

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
