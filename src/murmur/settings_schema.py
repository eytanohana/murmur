"""Every setting described once: label, help, group, control kind, range and live or restart.

Validation for the config file, the menu and the settings window all derive from this
table. Defaults are not repeated here; they come from the Config dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

OPTION_KEYS = ("left", "right", "both")
INDICATORS = ("none", "overlay")
INSERT_MODES = ("paste", "type")
POSITIONS = ("top-center", "top-left", "top-right", "bottom-center", "bottom-left", "bottom-right")
SAMPLE_RATES = (16000,)  # Whisper expects 16 kHz audio

GROUPS = ("General", "Streaming", "Output", "Feedback", "Model")

TOGGLE, CHOICE, NUMBER, TEXT, SOUND, MODEL = "toggle", "choice", "number", "text", "sound", "model"

STREAMING_OFF = ("stream", False)  # a `when` condition: only relevant while streaming is off
STREAMING_OFF_NOTE = "Used only when streaming is off; streamed words are always typed."


def _choices(values, labels=None) -> tuple[tuple[object, str], ...]:
    return tuple((v, (labels or {}).get(v, str(v))) for v in values)


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    help: str
    group: str
    kind: str
    live: bool = True  # False: needs a restart
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    integer: bool = False  # number kind: int (True) or float (False)
    choices: tuple[tuple[object, str], ...] = field(default=())  # (value, label)
    when: tuple[str, object] | None = None  # enabled only while config[key] == value
    when_note: str = ""

    @property
    def values(self) -> tuple[object, ...]:
        return tuple(v for v, _ in self.choices)


SCHEMA: tuple[Setting, ...] = (
    # General
    Setting(
        "option_keys",
        "Hotkey",
        "Which Option key starts dictation.",
        "General",
        CHOICE,
        choices=_choices(
            OPTION_KEYS, {"left": "Left Option", "right": "Right Option", "both": "Either Option"}
        ),
    ),
    Setting(
        "min_hold_ms",
        "Hold time",
        "How long to hold Option before dictation starts. Shorter taps are ignored.",
        "General",
        NUMBER,
        unit="ms",
        minimum=0,
        maximum=3000,
        step=50,
        integer=True,
    ),
    Setting(
        "max_record_s",
        "Longest recording",
        "A recording stops and is transcribed after this long.",
        "General",
        NUMBER,
        unit="s",
        minimum=10,
        maximum=300,
        step=10,
    ),
    Setting(
        "silence_rms",
        "Silence threshold",
        "Audio quieter than this counts as silence. Raise it in a noisy room.",
        "General",
        NUMBER,
        minimum=0.001,
        maximum=0.05,
        step=0.001,
    ),
    Setting(
        "launch_at_login",
        "Launch at login",
        "Start murmur when you log in. Only in the murmur.app build.",
        "General",
        TOGGLE,
    ),
    Setting(
        "sample_rate",
        "Sample rate",
        "Whisper needs 16 kHz audio, so this cannot change.",
        "General",
        CHOICE,
        live=False,
        choices=_choices(SAMPLE_RATES, {16000: "16 kHz"}),
    ),
    # Streaming
    Setting(
        "stream",
        "Type while speaking",
        "Type words as they become stable. Off: transcribe the whole clip when you release.",
        "Streaming",
        TOGGLE,
    ),
    Setting(
        "stream_interval",
        "Pass interval",
        "Seconds between transcription passes while Option is held.",
        "Streaming",
        NUMBER,
        unit="s",
        minimum=0.2,
        maximum=3.0,
        step=0.1,
    ),
    Setting(
        "stream_holdback_words",
        "Held-back words",
        "The last words of each pass wait until they are confirmed.",
        "Streaming",
        NUMBER,
        unit="words",
        minimum=0,
        maximum=5,
        step=1,
        integer=True,
    ),
    Setting(
        "stream_trim_s",
        "Trim buffer after",
        "Longer audio is cut after the last typed word to keep passes fast.",
        "Streaming",
        NUMBER,
        unit="s",
        minimum=10,
        maximum=28,
        step=1,
    ),
    Setting(
        "stream_min_speech_s",
        "Speech needed per pass",
        "A pass runs only when this much new speech arrived.",
        "Streaming",
        NUMBER,
        unit="s",
        minimum=0.1,
        maximum=1.0,
        step=0.1,
    ),
    Setting(
        "stream_flush_silence_s",
        "Pause that flushes",
        "A pause this long types the held-back words while Option is still down.",
        "Streaming",
        NUMBER,
        unit="s",
        minimum=0.3,
        maximum=3.0,
        step=0.1,
    ),
    # Output
    Setting(
        "insert_mode",
        "Insert mode",
        "Paste uses the clipboard and Cmd+V (the plain-text clipboard is restored); "
        "type sends key events.",
        "Output",
        CHOICE,
        choices=_choices(INSERT_MODES, {"paste": "Paste", "type": "Type"}),
        when=STREAMING_OFF,
        when_note=STREAMING_OFF_NOTE,
    ),
    Setting(
        "paste_restore_delay_ms",
        "Clipboard restore delay",
        "How long to wait after pasting before the old clipboard comes back.",
        "Output",
        NUMBER,
        unit="ms",
        minimum=0,
        maximum=1000,
        step=10,
        integer=True,
        when=STREAMING_OFF,
        when_note=STREAMING_OFF_NOTE,
    ),
    # Feedback
    Setting(
        "sounds",
        "Sounds",
        "Play a sound when dictation starts and stops.",
        "Feedback",
        TOGGLE,
    ),
    Setting(
        "start_sound",
        "Start sound",
        "Played once the hold time has passed.",
        "Feedback",
        SOUND,
        when=("sounds", True),
        when_note="Turn on Sounds to hear it.",
    ),
    Setting(
        "stop_sound",
        "Stop sound",
        "Played when you release Option.",
        "Feedback",
        SOUND,
        when=("sounds", True),
        when_note="Turn on Sounds to hear it.",
    ),
    Setting(
        "indicator",
        "Recording pill",
        "A small pill that shows when murmur is recording.",
        "Feedback",
        CHOICE,
        live=False,
        choices=_choices(INDICATORS, {"overlay": "Show", "none": "Hide"}),
    ),
    Setting(
        "indicator_position",
        "Pill position",
        "Where the pill appears on the main screen.",
        "Feedback",
        CHOICE,
        choices=_choices(
            POSITIONS,
            {p: p.replace("-", " ").capitalize() for p in POSITIONS},
        ),
    ),
    Setting(
        "menubar_icon",
        "Show icon in the menu bar",
        "If you hide it, open murmur from Spotlight to get back here.",
        "Feedback",
        TOGGLE,
    ),
    # Model
    Setting(
        "model_repo",
        "Model",
        "The Whisper model. Switching loads the new one in the background.",
        "Model",
        MODEL,
    ),
    Setting(
        "language",
        "Language",
        "Language code such as en, de or fr. English-only models always use en.",
        "Model",
        TEXT,
    ),
)

BY_KEY = {s.key: s for s in SCHEMA}
LIVE_KEYS = frozenset(s.key for s in SCHEMA if s.live)
RESTART_KEYS = frozenset(s.key for s in SCHEMA if not s.live)


def expected_type(setting: Setting) -> type:
    if setting.kind == TOGGLE:
        return bool
    if setting.kind == NUMBER:
        return int if setting.integer else float
    if setting.kind == CHOICE and setting.choices and isinstance(setting.choices[0][0], int):
        return int
    return str


def _fmt(n: float) -> str:
    return f"{n:g}"


def validate_setting(key: str, value: object) -> None:
    """Raise ValueError unless `key` is a setting and `value` is valid for it."""
    setting = BY_KEY.get(key)
    if setting is None:
        raise ValueError(f"Unknown config key: {key}")
    expected = expected_type(setting)
    if expected is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, expected)
    if not ok:
        raise ValueError(f"{key} must be {expected.__name__}, got {type(value).__name__}")
    if setting.kind == CHOICE and value not in setting.values:
        options = ", ".join(repr(v) for v in setting.values[:-1])
        text = f"{options} or {setting.values[-1]!r}" if options else repr(setting.values[-1])
        raise ValueError(f"{key} must be {text}")
    if setting.kind == NUMBER and not setting.minimum <= value <= setting.maximum:
        raise ValueError(
            f"{key} must be between {_fmt(setting.minimum)} and {_fmt(setting.maximum)}"
            f"{' ' + setting.unit if setting.unit else ''}"
        )
    if setting.kind in (TEXT, MODEL, SOUND) and not value.strip():
        raise ValueError(f"{key} must not be empty")
