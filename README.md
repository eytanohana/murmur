# murmur

Push-to-talk dictation for macOS. Hold Option and speak; words are typed at the cursor of the focused app as they become stable, and the rest is typed when you release. Transcription runs locally with Whisper large-v3-turbo on MLX. See DESIGN.md for the reasoning.

## Install the app

murmur.app is a self-contained build for Apple Silicon Macs on macOS 15 or newer (the macOS version the bundled mlx build needs). It has no Python or uv requirement.

1. Download `murmur-<version>-arm64.dmg` from the [GitHub release](https://github.com/eytanohana/murmur/releases) and open it.
2. Drag murmur to Applications.
3. First open: the app is only ad-hoc signed, not signed with an Apple Developer ID and not notarized, so Gatekeeper blocks a double-click the first time. Right-click murmur in Applications and choose Open, then Open again in the dialog. If macOS only offers "Move to Trash", open System Settings > Privacy & Security and press "Open Anyway" next to the murmur message. Or remove the quarantine flag in a terminal: `xattr -d com.apple.quarantine /Applications/murmur.app`.
4. Grant three permissions when macOS asks (System Settings > Privacy & Security):
   - Microphone: murmur records while you hold Option.
   - Accessibility: murmur types the transcript into the app you are using.
   - Input Monitoring: murmur sees the Option key press and release.
   If you dismissed a prompt, turn murmur on in the matching list, then choose Restart murmur from the menu bar icon.
5. First launch: with no config file the app writes `~/.config/murmur/config.toml` with `menubar_icon = true`, because without a terminal the menu bar icon is the only sign the app is running and its menu holds Settings and Quit. murmur then downloads the default model (about 1.6 GB, tiny to large-v3 are in the Model tab) in the background; the menu header reads "Downloading large-v3 turbo 37%..." and holding Option is ignored until it finishes. If the download fails (for example offline), the header says "Model load failed: see log", the error is in `~/Library/Logs/murmur.log`, and choosing the model again in the Model tab (or relaunching) retries. A model that is already cached loads in a few seconds with no download.
6. Launch at login: turn on "Launch at login" in the menu or in Settings > General. murmur registers with macOS as a login item; if macOS refuses an ad-hoc signed app it writes a small LaunchAgent (`~/Library/LaunchAgents/com.eytanohana.murmur.login.plist`) that opens the app instead. If you installed the developer LaunchAgent below (`com.user.murmur`), the toggle is disabled until you run `murmur uninstall-agent`, so two copies never start.

Only one copy runs: starting murmur while it is already running (including the developer LaunchAgent) logs a line and the new copy exits, because two copies would both type every word. Restart murmur relaunches the app and waits for the old copy to exit first.

**Ad-hoc signing limits.** macOS ties the permissions to the app's code signature. An ad-hoc signature changes with every build, so after installing a new version macOS may show the permission prompts again, or show murmur as already enabled while it no longer works; turn murmur off and on in the three lists (or remove it with the minus button and add it back) and choose Restart murmur. Gatekeeper also asks again for a new download. A Developer ID signature and notarization would remove both; this project does not have one.

**Uninstall.** Quit murmur from the menu bar icon, drag murmur from Applications to the Trash, and delete `~/.config/murmur` and `~/Library/Logs/murmur.log`. Remove murmur from the three Privacy lists if you like, and turn off Launch at login first (it also removes the fallback LaunchAgent). The downloaded models stay in `~/.cache/huggingface` (delete the `models--mlx-community--whisper-*` folders to reclaim the space).

## Build from source

`scripts/build_app.sh` builds `dist/murmur.app` (ad-hoc signed) and `dist/murmur-<version>-arm64.dmg` from a fresh checkout. It needs only uv and the macOS command line tools on an Apple Silicon Mac, and takes under a minute once the dependencies are cached.

```
./scripts/build_app.sh
```

It cleans `build/` and `dist/` (both git ignored), installs the `build` dependency group (PyInstaller and dmgbuild; the runtime dependencies of the PyPI package are unchanged), draws the icon and the disk image background with `scripts/make_assets.py`, bundles with PyInstaller (`packaging/murmur.spec`), signs ad hoc, and builds the disk image with dmgbuild (`packaging/dmg_settings.py`). The model is not bundled.

PyInstaller, not py2app: py2app could not find mlx (a namespace package, after a recursion error scanning modules), while PyInstaller bundles mlx's libraries and metallib, PortAudio, PyObjC and the mlx-whisper data files. torch is excluded (mlx-whisper depends on it but only its conversion script imports it), which keeps the app at about 390 MB and the dmg at about 144 MB. `LSMinimumSystemVersion` is read from the mlx wheel that uv installed, so build on the oldest macOS you want to support (the release workflow uses `macos-15`).

The release workflow builds the dmg next to the PyPI job and attaches it, with a SHA256SUMS file, to the release; a pull request that touches the sources, packaging or lock file builds the app and runs a frozen self-check. To run that self-check yourself: `MURMUR_SELFTEST_WAV=clip.wav dist/murmur.app/Contents/MacOS/murmur` makes the app transcribe a 16 kHz mono wav, skip the permission prompts and the hotkey listener, print the transcript and quit (`MURMUR_SELFTEST_HOLD` sets how long it stays up).

## Developer setup

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

- A status line ("murmur is running", "Loading <model> 42%...", "Model load failed: see log" or "Config error: see log") and the current model, as disabled lines.
- Streaming and Sounds toggles with checkmarks.
- Model: a submenu with a checkmark on the current model; each entry shows its size, language scope and whether it is already downloaded.
- Settings...: opens the settings window (below).
- Open config file: creates `~/.config/murmur/config.toml` (or the `--config` path) with a short commented header when it is missing, then opens it with `open -t`.
- "Restart murmur", which becomes "Restart to apply changes (keys)" and is checked when a restart-only key was changed in the file.
- Quit murmur.

Changing a setting validates the value and writes just that key to the config file with tomlkit, so your comments and other keys stay. The change applies on the next Option press, no restart needed.

**Restart.** Under the LaunchAgent (detected by `XPC_SERVICE_NAME` equal to the job label) it runs `launchctl kickstart -k gui/<uid>/com.user.murmur`. In a terminal it stops the hotkey listener and the recorder, then re-executes the same interpreter with the original arguments (`python -m murmur ...` or the `murmur` script) in the same process, so two copies never run. Limits: the re-exec is only for runs outside launchd; a failure to exec is not retried and shows up in the log. A restart reloads the model, so the app is deaf for the few seconds that takes. Both paths were exercised with a status item in a scratch run (the process kept its PID across the re-exec), but not with a real Option key press.

**Quit** exits with status 0. The LaunchAgent restarts only after a crash, so a quit stays quit until you start it again: `launchctl kickstart gui/$(id -u)/com.user.murmur` (or log in again).

## Settings window

Settings... in the menu bar menu opens one window, "murmur Settings", with a tab per group: General (hotkey, hold time, longest recording, silence threshold, sample rate), Streaming, Output (insert mode, clipboard delay), Feedback (sounds with a picker and Play button for each system sound, the pill, its position, the menu bar icon) and Model. Every setting has a label, a one-line help text, and a control that fits it: a switch, a slider with the live value and unit, a stepper, a popup or a text field. The ranges come from one schema, for example hold time 0 to 3000 ms and pass interval 0.2 to 3.0 s, and the config file is checked against the same ranges.

A change is validated, written to the config file with tomlkit and applied at once, like a menu click. Settings that cannot apply live (sample rate, the pill on or off, the menu bar icon) show an orange "Restart needed" badge with a Restart now button. A Reset button appears next to any value that differs from its default, and Reset all (with a confirmation) restores every default. Insert mode and the clipboard delay are greyed out while streaming is on, the sound pickers while Sounds is off and Language while an English-only model is loaded, each with the reason under the control. The window re-reads the config file when it regains focus, and closing it hands focus back to the app you were typing in.

## Models

The Model tab and the Model submenu list models that were checked against the Hugging Face API for existence and size and loaded with mlx_whisper:

| Model | Size | Languages | Notes |
| --- | --- | --- | --- |
| tiny.en | 74 MB | English | fastest, least accurate |
| base.en | 144 MB | English | |
| small.en | 481 MB | English | |
| medium.en | 1525 MB | English | |
| large-v3 turbo | 1614 MB | multilingual | default |
| large-v3 | 3084 MB | multilingual | slowest, most accurate |
| distil large-v3 | 1509 MB | English | output has no punctuation or capitals |

The 4-bit and 8-bit turbo conversions are not listed: they ship `model.safetensors`, which the installed mlx_whisper cannot load. You can still put any other `mlx-community` Whisper repo in `model_repo`; it works if mlx_whisper can load it, and the dropdown shows it as "(custom)".

**Switching without a restart.** Picking a model downloads it if needed (with progress in the Model tab and "Loading ..." in the menu header) and loads it on a background thread while the current model keeps serving. The swap happens under the transcriber lock and only between dictations; if you are dictating when the load finishes, it waits for the release. The config file changes only after the load succeeded. If anything fails the old model stays, the error shows in the Model tab and the log, and a hand-edited `model_repo` in the file is put back. The old weights are freed and the MLX cache cleared. Switching to an English-only model sets `language = "en"`; the Language control is disabled for those models.

In a scratch run with the real models (temp Hugging Face cache, `HF_HUB_OFFLINE=1` exported to imitate an old plist), switching from turbo to tiny.en took 2.1 s including the 74 MB download, small.en 7.9 s including 481 MB, turbo again 1.5 s from cache and base.en 3.3 s. Resident memory went from 2086 MB (turbo) to 565 MB (tiny.en) after the swap, and the test clip transcribed correctly after every swap.

**Offline behavior.** At startup murmur goes offline only when the configured model is already in the Hugging Face cache, so logging in without a network still works. If the model is not cached it downloads it. The LaunchAgent plist no longer sets `HF_HUB_OFFLINE`. If you installed the agent with an older version, its plist still exports `HF_HUB_OFFLINE=1`; that keeps working, because startup and model switches turn offline mode off before they download. Run `murmur install-agent` again whenever you like to write the new plist.

## Run at login with a LaunchAgent (developer path)

This is for running from a checkout. If you use murmur.app, use its Launch at login toggle instead.

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

**After editing the config:** nothing for most keys, they apply on the next Option press. After changing `sample_rate`, `indicator` or `menubar_icon`: `launchctl kickstart -k gui/$(id -u)/com.user.murmur`

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

Changes to the file apply on the next Option press, no restart needed. murmur checks the file's modification time once when you press Option (and when the menu bar menu opens) and only re-reads it when that changed. A key press picks up the new values for that dictation and they stay fixed until it ends. Only `sample_rate`, `indicator` and `menubar_icon` need a restart (`model_repo` loads the new model in the background and `language` applies at once); murmur logs them as pending and the menu names them. If the file is invalid, murmur keeps the previous settings, logs one warning per change and shows "Config error: see log" in the menu.

Optional file at `~/.config/murmur/config.toml`. Any key can be omitted. Unknown keys and values of the wrong type (for example `min_hold_ms = "1000"`) stop the app at startup with an error naming the key.

| Key | Default | Meaning |
| --- | --- | --- |
| `model_repo` | `mlx-community/whisper-large-v3-turbo` | Hugging Face MLX Whisper repo; a change loads the new model in the background |
| `language` | `"en"` | Forced language; English-only models always use en |
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

Use the Model submenu or the Model tab, or set `model_repo` in the config file; murmur loads it in the background, see Models above. A repo outside the list works if mlx_whisper can load it. Set `language` to match multilingual models or leave `"en"` for English.

## Development

```
uv run pytest
uv run ruff check
```

## License and credits

murmur is released under the MIT License (see `LICENSE`).

It runs [OpenAI Whisper](https://github.com/openai/whisper) models (code and weights are MIT licensed) through [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) (MIT), using the `mlx-community/whisper-large-v3-turbo` conversion from Hugging Face. The model is downloaded at first run and is not bundled with this package.

Other dependencies keep their own licenses: pynput (LGPL-3.0), sounddevice (MIT), numpy (BSD), PyObjC (MIT) and tomlkit (MIT).
