"""Configuration: defaults in a dataclass, overridden by an optional TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

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
    # Visible recording indicator: "overlay" (floating pill), "menubar" or "none".
    indicator: str = "overlay"
    # Overlay position: top-center, top-left, top-right, bottom-center, bottom-left, bottom-right
    indicator_position: str = "top-center"
    # Deprecated alias for indicator = "menubar".
    menubar: bool = False
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
        setattr(cfg, key, value)
    if cfg.option_keys not in ("left", "right", "both"):
        raise ValueError("option_keys must be 'left', 'right' or 'both'")
    if cfg.menubar:
        cfg.indicator = "menubar"
    if cfg.indicator not in ("none", "menubar", "overlay"):
        raise ValueError("indicator must be 'none', 'menubar' or 'overlay'")
    if cfg.indicator_position not in POSITIONS:
        raise ValueError(f"indicator_position must be one of {POSITIONS}")
    if cfg.insert_mode not in ("paste", "type"):
        raise ValueError("insert_mode must be 'paste' or 'type'")
    return cfg
