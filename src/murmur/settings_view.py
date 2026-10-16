"""View-model helpers for the settings window and menu: no AppKit, so they are testable."""

from __future__ import annotations

import math
from collections.abc import Callable, Collection
from dataclasses import dataclass, fields
from pathlib import Path

from . import models
from .config import Config
from .modelswitch import SwitchState
from .settings_schema import SCHEMA, Setting

SOUNDS_DIR = Path("/System/Library/Sounds")


def settings_in(group: str) -> list[Setting]:
    return [s for s in SCHEMA if s.group == group]


def decimals(step: float) -> int:
    """Digits after the point needed to show multiples of `step`."""
    return max(0, -math.floor(math.log10(step))) if step < 1 else 0


def snap(setting: Setting, raw: float) -> float | int:
    """The nearest allowed value (a multiple of the step from the minimum), clamped."""
    steps = round((raw - setting.minimum) / setting.step)
    value = min(max(setting.minimum + steps * setting.step, setting.minimum), setting.maximum)
    if setting.integer:
        return round(value)
    return float(round(value, decimals(setting.step) + 2))


def format_value(setting: Setting, value: float) -> str:
    text = str(int(value)) if setting.integer else f"{value:.{decimals(setting.step)}f}"
    unit = "word" if setting.unit == "words" and value == 1 else setting.unit
    return f"{text} {unit}" if unit else text


@dataclass(frozen=True)
class RowState:
    enabled: bool
    note: str  # why the control is disabled, empty when enabled
    restart_needed: bool
    is_default: bool


def row_state(setting: Setting, cfg: Config, pending: Collection[str]) -> RowState:
    enabled, note = True, ""
    if setting.when is not None:
        key, wanted = setting.when
        if getattr(cfg, key) != wanted:
            enabled, note = False, setting.when_note
    if setting.key == "language" and models.is_english_only(cfg.model_repo):
        enabled, note = False, "The current model is English only, so it always uses en."
    return RowState(
        enabled=enabled,
        note=note,
        restart_needed=setting.key in pending,
        is_default=getattr(cfg, setting.key) == getattr(Config(), setting.key),
    )


def sound_options(directory: Path = SOUNDS_DIR) -> list[tuple[str, str]]:
    """(path, name) for each system sound, sorted by name."""
    return sorted(((str(p), p.stem) for p in directory.glob("*.aiff")), key=lambda t: t[1])


def sound_choices(current: str, directory: Path = SOUNDS_DIR) -> list[tuple[str, str]]:
    """Sound options, with a configured path outside the folder kept so it is not lost."""
    options = sound_options(directory)
    if current and current not in {p for p, _ in options}:
        options.append((current, Path(current).stem or current))
    return options


def model_options(current: str, cached: Callable[[str], bool]) -> list[tuple[str, str]]:
    """(repo, label) for the dropdown; a configured repo outside the catalog is added."""
    options = [(m.repo, models.model_label(m, cached(m.repo))) for m in models.CATALOG]
    if current not in models.BY_REPO:
        options.insert(0, (current, f"{models.short_name(current)} (custom)"))
    return options


def model_status_text(cfg: Config, state: SwitchState, ready: bool = True) -> str:
    """One line for the Model tab and the menu: what is loading, what failed, what is active."""
    target = models.short_name(state.target) if state.target else ""
    if state.status == "loading":
        if state.progress is None:
            return f"Loading {target}..."
        return f"Downloading {target}: {round(state.progress * 100)}%"
    if state.status == "waiting":
        return f"{target} is ready and will be used after the current dictation."
    if state.status == "failed":
        if not ready:  # nothing is loaded yet, so there is no old model to fall back to
            return f"Could not load {target}: {state.error}. Choose the model again to retry."
        return f"Could not load {target}: {state.error}. Still using {models.short_name(cfg.model_repo)}."
    return f"Using {models.short_name(cfg.model_repo)}."


def header_text(state: SwitchState, error: str | None) -> str:
    """First menu line: a config error wins, then a model load in progress or failed."""
    if error:
        return "Config error: see log"
    target = models.short_name(state.target) if state.target else ""
    if state.status == "loading":
        if state.progress is not None:
            return f"Downloading {target} {round(state.progress * 100)}%..."
        return f"Loading {target}..."
    if state.status == "waiting":
        return f"Switching to {target} after this dictation..."
    if state.status == "failed":
        return "Model load failed: see log"
    return "murmur is running"


def changed_from_defaults(cfg: Config) -> list[tuple[str, object]]:
    """(key, default) for each setting that differs from its default; the model comes last
    so a language reset happens before a model switch re-derives it."""
    defaults = Config()
    out = [
        (f.name, getattr(defaults, f.name))
        for f in fields(Config)
        if getattr(cfg, f.name) != getattr(defaults, f.name)
    ]
    out.sort(key=lambda kv: kv[0] == "model_repo")
    return out
