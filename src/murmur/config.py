"""Configuration: defaults in a dataclass, overridden by an optional TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from .settings_schema import (  # noqa: F401  re-exported: the schema owns these values
    INDICATORS,
    INSERT_MODES,
    LIVE_KEYS,
    OPTION_KEYS,
    POSITIONS,
    RESTART_KEYS,
    validate_setting,
)

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
    # "paste" or "type". Used only when stream = false: streamed chunks are always typed.
    # paste = clipboard plus Cmd+V (plain-text clipboard restored); type = key events.
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
