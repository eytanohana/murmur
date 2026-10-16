# murmur

Push-to-talk dictation for macOS. Hold Option and speak; words are typed at the cursor of the focused app as they become stable, and the rest is typed when you release. Transcription runs locally with Whisper large-v3-turbo on MLX. See DESIGN.md for the reasoning.

## Setup

Requires macOS on Apple Silicon and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run murmur
```

The first run downloads the model (about 1.6 GB) into the Hugging Face cache. Later starts load it in a few seconds. Use `-v` for debug logs.

## Permissions

Grant these to the terminal app that runs the command, in System Settings > Privacy & Security, then restart the terminal:

- Accessibility: sends the synthetic Cmd+V.
- Input Monitoring: global Option key listening.
- Microphone: prompted on first recording.

The app prints a warning at startup for Accessibility and Input Monitoring if they look missing.

## Recording indicator

The macOS mic indicator does not appear for a LaunchAgent, so the app draws its own. Set `indicator` in the config (default `"overlay"`):

- `overlay`: a small dark pill with a red dot and "REC" at the top center of the main screen, just below the menu bar and notch. It appears when the 1 s hold threshold passes (not at key down), shows an orange dot and "..." while the final pass runs, then hides. It is a borderless non-activating panel at status window level on all Spaces; it ignores the mouse and never takes focus, so typing continues in the app you are using. No sounds. `indicator_position` moves it: `top-center`, `top-left`, `top-right`, `bottom-center`, `bottom-left`, `bottom-right`.
- `none`: no pill. Combine it with `menubar_icon = true` for a menu bar icon only.

AppKit runs on the main thread and dictation on other threads. The app uses the accessory activation policy, so there is no Dock icon. SIGTERM and Ctrl+C end the app with exit status 0 with or without the icon. After changing `indicator` under the agent, restart as described in the menu bar icon section.

## Menu bar icon

Experimental and off by default, because the menu bar may be full. Set `menubar_icon = true` in the config. It adds a native status item that only says murmur is running: a microphone symbol (`mic`), which becomes `mic.fill` while recording, from the same moment the pill appears. It is a template image, so it follows light and dark menu bars. It works with `indicator = "none"` for an icon without the pill. The icon stays `mic` during the final pass; the pill shows "...".

On a Mac with a notch, macOS hides status items that do not fit. On the development Mac the bar was full, the item was created but macOS did not draw it, and it only appeared once there was room. If the icon is missing, remove or Cmd-drag another icon to make space.

Click the icon for the menu:

- "murmur is running" (or "Config error: see log" when the file on disk is invalid) and the model name, as disabled lines.
- Streaming and Sounds toggles with checkmarks; Overlay position and Insert mode submenus with a checkmark on the current value. The Insert mode submenu is greyed out while Streaming is on, because it only matters when streaming is off.
- Open config file: creates `~/.config/murmur/config.toml` (or the `--config` path) with a short commented header when it is missing, then opens it with `open -t`.
- "Restart murmur", which becomes "Restart to apply changes (keys)" and is checked when a restart-only key was changed in the file.
- Quit murmur.

Changing a setting validates the value and writes just that key to the config file with tomlkit, so your comments and other keys stay. The change applies on the next Option press, no restart needed.

**Restart.** Under the LaunchAgent (detected by `XPC_SERVICE_NAME` equal to the job label) it runs `launchctl kickstart -k gui/<uid>/com.user.murmur`. In a terminal it stops the hotkey listener and the recorder, then re-executes the same interpreter with the original arguments (`python -m murmur ...` or the `murmur` script) in the same process, so two copies never run. Limits: the re-exec is only for runs outside launchd; a failure to exec is not retried and shows up in the log. A restart reloads the model, so the app is deaf for the few seconds that takes. Both paths were exercised with a status item in a scratch run (the process kept its PID across the re-exec), but not with a real Option key press.

**Quit** exits with status 0. The LaunchAgent restarts only after a crash, so a quit stays quit until you start it again: `launchctl kickstart gui/$(id -u)/com.user.murmur` (or log in again).

## Run at login

A LaunchAgent starts dictation at login and keeps it running; hold Option anywhere.

```
uv sync
uv run murmur install-agent --dry-run   # print the plist and commands only
uv run murmur install-agent             # write the plist, lint it, start the agent
uv run murmur agent-status
uv run murmur uninstall-agent
```

`install-agent` writes `~/Library/LaunchAgents/com.user.murmur.plist` and runs `launchctl bootstrap gui/$UID`. The agent runs `.venv/bin/python -m murmur` directly (not `uv run`) from the project folder, with `RunAtLoad`, `ProcessType Interactive`, and `KeepAlive` set to restart only after a crash (non-zero exit); a clean exit or Ctrl+C-style stop stays stopped. The plist does not set `HF_HUB_OFFLINE`; see Offline behavior below. `--no-start` writes the plist without running launchctl.

**Logs:** `tail -f ~/Library/Logs/murmur.log` (stdout and stderr, including permission warnings).

**After editing the config:** nothing for most keys, they apply on the next Option press. After changing `model_repo`, `language`, `sample_rate`, `indicator` or `menubar_icon`: `launchctl kickstart -k gui/$(id -u)/com.user.murmur`

**Permissions:** with no terminal attached, macOS attributes the permissions to the Python binary itself, not to your terminal. `install-agent` prints its path after installing, with symlinks resolved (for example `~/.local/share/uv/python/cpython-3.12.x-macos-aarch64-none/bin/python3.12`). In System Settings > Privacy & Security add that binary to Accessibility, Input Monitoring and Microphone (click +, press Cmd+Shift+G, paste the path), then run the kickstart command above. At startup the app requests the prompts for itself and warns in the log, naming the binary, when Accessibility or Input Monitoring is missing or the microphone returns only silence.

**After a Python upgrade:** uv-managed Python lives in a versioned folder, so a new patch release is a new binary and loses the grants. Run `uv run murmur install-agent` again to print the new path, add it to the three lists, remove the stale entry, and kickstart. The trade-off: this path is stable until uv upgrades Python for the project (`uv python pin` or a fixed `.python-version` such as 3.12.13 limits that), whereas a system or Homebrew Python resolves to a versioned path as well, and a signed app bundle would give a permanent identity at the cost of packaging work this project does not do.

**Idle cost** with large-v3-turbo loaded, measured on the M4 Pro as a normal foreground process (`.venv/bin/python -m murmur`, no input):

| Measure | Value |
| --- | --- |
| Resident memory (RSS) | 1927 MB, unchanged after 60 s |
| Memory footprint (`footprint`) | 2595 MB, of which 2397 MB is GPU buffers (IOAccelerator) |
| CPU over 60 s | 0.09% of one core (0.05 s of CPU time) |
| Threads | 28 |
| Peak during streaming (43 s clip, real-time feed) | RSS 2017 MB, footprint 4101 MB |

The footprint peak includes MLX's GPU buffer cache growing across passes of different lengths; it is not released while the process runs.

## Behavior

Streaming is on by default. While Option is held, the buffer is re-transcribed about every half second. Words that match in the same position on two consecutive passes are typed; the last word waits until it stabilizes or you release. Typed text is never edited, so a word that Whisper revises after it was typed stays as typed, and only the remaining tail is appended on release (mismatches are logged). Streamed text is typed with key events, not pasted. Set `stream = false` to transcribe only on release and use `insert_mode`.

- Holds shorter than `min_hold_ms` (1000) do nothing, so a tap or Option symbol typing never starts dictation. Audio is buffered from key down, so the first second is kept once the threshold passes. Nothing is typed and no start sound plays before the threshold; a shorter hold discards the audio.
- Pressing any other key while Option is held cancels the recording.
- Recordings stop at `max_record_s` (120) and are transcribed.
- Near-silent clips and empty transcripts insert nothing.
- Pressing Option while a clip is transcribing is ignored with a log line.
- Each dictation logs the recorded duration and the transcription time.
- Paste mode restores the previous clipboard as plain text only; images or rich content on the clipboard are lost.

## Config

Changes to the file apply on the next Option press, no restart needed. murmur checks the file's modification time once when you press Option (and when the menu bar menu opens) and only re-reads it when that changed. A key press picks up the new values for that dictation and they stay fixed until it ends. Only `model_repo`, `language`, `sample_rate`, `indicator` and `menubar_icon` need a restart; murmur logs them as pending and the menu names them. If the file is invalid, murmur keeps the previous settings, logs one warning per change and shows "Config error: see log" in the menu.

Optional file at `~/.config/murmur/config.toml`. Any key can be omitted. Unknown keys and values of the wrong type (for example `min_hold_ms = "1000"`) stop the app at startup with an error naming the key.

| Key | Default | Meaning |
| --- | --- | --- |
| `model_repo` | `mlx-community/whisper-large-v3-turbo` | Hugging Face MLX Whisper repo |
| `language` | `"en"` | Forced language |
| `min_hold_ms` | `1000` | Minimum hold to count |
| `max_record_s` | `120` | Maximum recording length |
| `silence_rms` | `0.005` | RMS below this counts as silence |
| `option_keys` | `"both"` | `"left"`, `"right"` or `"both"` |
| `insert_mode` | `"paste"` | `"paste"` or `"type"`. With `stream = true` every chunk is typed with key events and this setting does nothing; with `stream = false` the whole clip is inserted on release, either by clipboard and Cmd+V (the previous plain-text clipboard is restored) or by typing key events with no clipboard. |
| `paste_restore_delay_ms` | `150` | Wait before restoring the clipboard |
| `sounds` | `false` | Start sound (when the hold threshold passes) and stop sound (on release); off because the macOS mic indicator shows recording |
| `start_sound`, `stop_sound` | Tink, Pop | Paths under `/System/Library/Sounds` |
| `stream` | `true` | Type stable words while Option is held |
| `stream_interval` | `0.5` | Seconds between pass starts; a pass takes about 0.45 s, so passes run nearly back to back |
| `stream_holdback_words` | `1` | Words held back until stable or release |
| `stream_trim_s` | `20` | Buffer length that triggers a trim at the last committed word |
| `stream_min_speech_s` | `0.3` | New speech needed to run a pass |
| `stream_flush_silence_s` | `0.7` | Pause that flushes held-back words and the unsettled tail while Option is held |
| `indicator` | `"overlay"` | `"overlay"` or `"none"` |
| `menubar_icon` | `false` | Show the menu bar icon and settings menu |
| `indicator_position` | `"top-center"` | Overlay position |

## Pause flush

When the buffer ends in at least `stream_flush_silence_s` of silence (RMS at or below `silence_rms`) and there was speech since the last flush, one pass runs and every word it heard is typed, so the end of a sentence appears while Option is still held. Text already typed is not edited. Passes cut leading silence to 0.2 s and flush and final passes cut trailing silence to 0.2 s, because Whisper invents a word such as "Okay," over long silence. In a noisy room where the background is above `silence_rms`, no pause is detected and words are typed on release as before.

Measured on a 48 s clip (the 43 s clip with 1.5 s pauses after "priorities." and "Tuesday." and 2 s of silence before release), real-time feed, real model:

| Setting | Lag of the last word before a pause | Mean lag | Typed before release | Similarity to spoken text |
| --- | --- | --- | --- | --- |
| flush off | 3.26 s and 3.98 s | 1.21 s | 132 of 133 | 1.000 |
| flush on (default) | 1.25 s and 1.62 s | 1.16 s | 133 of 133 | 1.000 |

The lag at a pause includes the 0.7 s wait. The 1 s hold threshold moves the first text on the 43 s clip from 1.2 s to 1.9 s.

## Streaming latency

Measured with a 43 s `say` clip fed in real time through the real `Dictation` loop and model on an M4 Pro (lag is the time from a word's spoken end, per Whisper word timestamps, to it being typed):

| Setting | First text | Mean lag | 90th percentile | Release to done |
| --- | --- | --- | --- | --- |
| interval 1.0, holdback 2, speech 0.5 | 2.8 s | 2.33 s | 2.73 s | 1.67 s |
| interval 0.5, holdback 1, speech 0.5 | 1.7 s | 1.5 s | 2.1 s | 0.45 s |
| interval 0.5, holdback 1, speech 0.3 (default) | 1.2 s | 1.22 s | 1.56 s | 0.57 s |

Word similarity to the spoken text was 1.000 in all three rows (0.989 against Whisper's own full-clip transcript, which splits "on-call" differently). Lag is bounded by the pass time (about 0.45 s, fixed because Whisper always encodes a 30 s window) times the two passes a word needs to be seen, plus the held-back word. Intervals below 0.5 s gave no meaningful gain (mean lag 1.45 s at 0.3 s). Holdback 0 gave no gain either. Lowering the interval raises GPU load while Option is held.

Tried and rejected: decoding only the uncommitted tail plus 3 s of overlap did not make passes faster (cost is flat in audio length) and lost words at the window edge (115 of 134 words typed, similarity 0.92). Committing after one pass for words followed by three more words with probability of at least 0.9 needs word timestamps (0.4 s more per pass), and typed 107 of 134 words (similarity 0.88). Lowering the temperature or the decode length changed nothing measurable.

## Troubleshooting

Logs go to the terminal at INFO level: Option down and up, every streaming pass with its text, and every insert with the text and the number of key events posted. If a pass or an insert runs longer than 10 s, all thread stacks are logged as an error; include that in a bug report.

Text is typed with Quartz keyboard events (flags cleared, 20 UTF-16 units per event), so a held Option key does not alter the characters. Key events injected by software never cancel a recording.

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

## License and credits

murmur is released under the MIT License (see `LICENSE`).

It runs [OpenAI Whisper](https://github.com/openai/whisper) models (code and weights are MIT licensed) through [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) (MIT), using the `mlx-community/whisper-large-v3-turbo` conversion from Hugging Face. The model is downloaded at first run and is not bundled with this package.

Other dependencies keep their own licenses: pynput (LGPL-3.0), sounddevice (MIT), numpy (BSD), PyObjC (MIT) and tomlkit (MIT).
