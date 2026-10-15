# whisper-dictation

Push-to-talk dictation for macOS. Hold Option and speak; words are typed at the cursor of the focused app as they become stable, and the rest is typed when you release. Transcription runs locally with Whisper large-v3-turbo on MLX. See DESIGN.md for the reasoning.

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

Streaming is on by default. While Option is held, the buffer is re-transcribed about every second. Words that match in the same position on two consecutive passes are typed; the last two words wait until they stabilize or you release. Typed text is never edited, so a word that Whisper revises after it was typed stays as typed, and only the remaining tail is appended on release (mismatches are logged). Streamed text is typed with key events, not pasted. Set `stream = false` to transcribe only on release and use `insert_mode`.

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
| `insert_mode` | `"paste"` | `"paste"` or `"type"`; used only when `stream = false` |
| `paste_restore_delay_ms` | `150` | Wait before restoring the clipboard |
| `sounds` | `true` | Start and stop sounds |
| `start_sound`, `stop_sound` | Tink, Pop | Paths under `/System/Library/Sounds` |
| `stream` | `true` | Type stable words while Option is held |
| `stream_interval` | `1.0` | Seconds between streaming passes |
| `stream_holdback_words` | `2` | Words held back until stable or release |
| `stream_trim_s` | `20` | Buffer length that triggers a trim at the last committed word |
| `stream_min_speech_s` | `0.5` | New speech needed to run a pass |
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
