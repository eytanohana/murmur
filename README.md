# whisper-dictation

Push-to-talk dictation for macOS. Hold Option, speak, release; the transcript is inserted at the cursor of the focused app. Transcription runs locally with Whisper large-v3-turbo on MLX. See DESIGN.md for the reasoning.

## Setup

Requires macOS on Apple Silicon and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run whisper-dictation
```

The first run downloads the model (about 1.6 GB) into the Hugging Face cache. Later starts load it in a few seconds. Add `--menubar` for a status item (needs `uv sync --extra menubar`). Use `-v` for debug logs.

## Permissions

Grant these to the terminal app that runs the command, in System Settings > Privacy & Security, then restart the terminal:

- Accessibility: sends the synthetic Cmd+V.
- Input Monitoring: global Option key listening.
- Microphone: prompted on first recording.

The app prints a warning at startup for Accessibility and Input Monitoring if they look missing.

## Behavior

- Holds shorter than `min_hold_ms` (300) do nothing, so Option symbol typing still works.
- Pressing any other key while Option is held cancels the recording.
- Recordings stop at `max_record_s` (120) and are transcribed.
- Near-silent clips and empty transcripts insert nothing.
- Pressing Option while a clip is transcribing is ignored with a log line.
- Each dictation logs the recorded duration and the transcription time.
- Paste mode restores the previous clipboard as plain text only; images or rich content on the clipboard are lost.

## Config

Optional file at `~/.config/whisper-dictation/config.toml`. Any key can be omitted.

| Key | Default | Meaning |
| --- | --- | --- |
| `model_repo` | `mlx-community/whisper-large-v3-turbo` | Hugging Face MLX Whisper repo |
| `language` | `"en"` | Forced language |
| `min_hold_ms` | `300` | Minimum hold to count |
| `max_record_s` | `120` | Maximum recording length |
| `silence_rms` | `0.005` | RMS below this counts as silence |
| `option_keys` | `"both"` | `"left"`, `"right"` or `"both"` |
| `insert_mode` | `"paste"` | `"paste"` or `"type"` |
| `paste_restore_delay_ms` | `150` | Wait before restoring the clipboard |
| `sounds` | `true` | Start and stop sounds |
| `start_sound`, `stop_sound` | Tink, Pop | Paths under `/System/Library/Sounds` |
| `menubar` | `false` | Same as `--menubar` |

## Swapping the model

Set `model_repo` in the config file, for example:

```
model_repo = "mlx-community/whisper-medium.en-mlx"
```

Any `mlx-community` Whisper repo works. `language` is passed through, so set it to match multilingual models or leave `"en"` for English.

## Development

```
uv run pytest
uv run ruff check
```
