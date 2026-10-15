"""Configuration: defaults in a dataclass, overridden by an optional TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "whisper-dictation" / "config.toml"


@dataclass
class Config:
    model_repo: str = "mlx-community/whisper-large-v3-turbo"
    language: str = "en"
    sample_rate: int = 16000
    min_hold_ms: int = 300
    max_record_s: float = 120.0
    silence_rms: float = 0.005
    # "left", "right" or "both"
    option_keys: str = "both"
    # "paste" or "type"
    insert_mode: str = "paste"
    paste_restore_delay_ms: int = 150
    sounds: bool = True
    start_sound: str = "/System/Library/Sounds/Tink.aiff"
    stop_sound: str = "/System/Library/Sounds/Pop.aiff"
    menubar: bool = False
    # Streaming: type stable words while Option is held. false = transcribe on release.
    stream: bool = True
    stream_interval: float = 1.0
    stream_holdback_words: int = 2
    stream_trim_s: float = 20.0
    stream_min_speech_s: float = 0.5


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
    if cfg.insert_mode not in ("paste", "type"):
        raise ValueError("insert_mode must be 'paste' or 'type'")
    return cfg
