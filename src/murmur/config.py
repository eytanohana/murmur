"""Configuration: defaults in a dataclass, overridden by an optional TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

OPTION_KEYS = ("left", "right", "both")
INDICATORS = ("none", "overlay")
INSERT_MODES = ("paste", "type")
POSITIONS = ("top-center", "top-left", "top-right", "bottom-center", "bottom-left", "bottom-right")
CONFIG_PATH = Path.home() / ".config" / "murmur" / "config.toml"


@dataclass
class Config:
    model_repo: str = "mlx-community/whisper-large-v3-turbo"
    language: str = "en"
    sample_rate: int = 16000
    min_hold_ms: int = 1000
    max_record_s: float = 120.0
    silence_rms: float = 0.005
    # "left", "right" or "both"
    option_keys: str = "both"
    # "paste" or "type"
    insert_mode: str = "paste"
    paste_restore_delay_ms: int = 150
    # Start and stop sounds. Off by default; the macOS mic indicator shows recording.
    sounds: bool = False
    start_sound: str = "/System/Library/Sounds/Tink.aiff"
    stop_sound: str = "/System/Library/Sounds/Pop.aiff"
    # Recording indicator: "overlay" (floating pill) or "none".
    indicator: str = "overlay"
    # Overlay position: top-center, top-left, top-right, bottom-center, bottom-left, bottom-right
    indicator_position: str = "top-center"
    # Menu bar icon (also works with indicator = "none"); off because the bar may be full.
    menubar_icon: bool = False
    # Streaming: type stable words while Option is held. false = transcribe on release.
    stream: bool = True
    stream_interval: float = 0.5
    stream_holdback_words: int = 1
    stream_trim_s: float = 20.0
    stream_min_speech_s: float = 0.3
    # A pause this long flushes held-back words and the unsettled tail while Option is held.
    stream_flush_silence_s: float = 0.7


def check_choice(key: str, value: str, valid: tuple[str, ...]) -> None:
    if value not in valid:
        options = ", ".join(repr(v) for v in valid[:-1]) + f" or {valid[-1]!r}"
        raise ValueError(f"{key} must be {options}")


def check_type(key: str, value: object, default: object) -> None:
    """Require the value to have the type of the field's default.

    bool fields accept only bool, int fields reject bool, float fields take int or
    float but not bool (TOML has no implicit conversions and bool is an int in Python).
    """
    expected = type(default)
    if expected is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, expected)
    if not ok:
        raise ValueError(f"{key} must be {expected.__name__}, got {type(value).__name__}")


CHOICES = {
    "option_keys": OPTION_KEYS,
    "indicator": INDICATORS,
    "indicator_position": POSITIONS,
    "insert_mode": INSERT_MODES,
}


def validate_setting(key: str, value: object) -> None:
    """Raise ValueError unless `key` is a config key and `value` is valid for it."""
    defaults = Config()
    if not hasattr(defaults, key):
        raise ValueError(f"Unknown config key: {key}")
    check_type(key, value, getattr(defaults, key))
    if key in CHOICES:
        check_choice(key, value, CHOICES[key])


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML. Missing file gives defaults; unknown keys raise."""
    path = path or CONFIG_PATH
    cfg = Config()
    if not path.exists():
        return cfg
    with path.open("rb") as f:
        data = tomllib.load(f)
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown config keys in {path}: {sorted(unknown)}")
    for key, value in data.items():
        validate_setting(key, value)
        setattr(cfg, key, value)
    return cfg
