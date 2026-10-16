"""Menu bar settings logic that needs no display: the menu model, config writing and restart."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from .agent import LABEL, service_target
from .config import INSERT_MODES, POSITIONS, Config, validate_setting

CONFIG_HEADER = (
    "# murmur config. Every key is optional; see the README for the full list.\n"
    "# Changes apply after murmur restarts.\n"
)

ICON_IDLE = "mic"
ICON_RECORDING = "mic.fill"


def icon_for(state: str) -> str:
    """SF Symbol for a state. Transcribing keeps the idle icon: the pill shows '...'."""
    return ICON_RECORDING if state == "recording" else ICON_IDLE


@dataclass(frozen=True)
class MenuItem:
    """One row of the status menu. kind is header, separator, setting, submenu or action."""

    kind: str
    title: str = ""
    key: str | None = None  # setting: the config key written on click
    value: object = None  # setting: the value written on click
    action: str | None = None  # action: open_config, restart or quit
    checked: bool = False
    enabled: bool = True
    children: tuple[MenuItem, ...] = ()


def build_menu(cfg: Config, changed: bool) -> list[MenuItem]:
    """The menu for the values in `cfg` (pending values included); `changed` marks a restart."""
    positions = tuple(
        MenuItem("setting", p, "indicator_position", p, checked=cfg.indicator_position == p)
        for p in POSITIONS
    )
    modes = tuple(
        MenuItem("setting", m, "insert_mode", m, checked=cfg.insert_mode == m) for m in INSERT_MODES
    )
    return [
        MenuItem("header", "murmur is running", enabled=False),
        MenuItem("header", cfg.model_repo, enabled=False),
        MenuItem("separator"),
        MenuItem("setting", "Streaming", "stream", not cfg.stream, checked=cfg.stream),
        MenuItem("setting", "Sounds", "sounds", not cfg.sounds, checked=cfg.sounds),
        MenuItem("submenu", "Overlay position", children=positions),
        MenuItem("submenu", "Insert mode (when not streaming)", children=modes),
        MenuItem("separator"),
        MenuItem("action", "Open config file", action="open_config"),
        MenuItem(
            "action",
            "Restart to apply changes",
            action="restart",
            checked=changed,
            enabled=changed,
        ),
        MenuItem("action", "Quit murmur", action="quit"),
    ]


def ensure_config_file(path: Path) -> Path:
    """Create the config file with a commented header when it does not exist yet."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CONFIG_HEADER)
    return path


def write_setting(path: Path, key: str, value: object) -> None:
    """Validate and write one key, keeping the file's comments, order and other keys."""
    validate_setting(key, value)
    ensure_config_file(path)
    doc = tomlkit.parse(path.read_text())
    doc[key] = value
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(tomlkit.dumps(doc))
    os.replace(tmp, path)  # a crash mid-write cannot leave a half-written config


def restart_plan(
    environ: Mapping[str, str], executable: str, orig_argv: Sequence[str]
) -> tuple[str, list[str]]:
    """How to restart: ("launchctl", cmd) under the LaunchAgent, else ("exec", argv).

    launchd sets XPC_SERVICE_NAME to the job label. Re-exec replays the original
    interpreter arguments, so `python -m murmur` and the `murmur` script both work.
    """
    if environ.get("XPC_SERVICE_NAME") == LABEL:
        return "launchctl", ["launchctl", "kickstart", "-k", service_target()]
    return "exec", [executable, *orig_argv[1:]]


def restart_now(stop_everything) -> None:  # pragma: no cover - replaces the process
    """Stop the listener and recorder, then restart through launchd or by re-exec."""
    import subprocess

    how, command = restart_plan(os.environ, sys.executable, sys.orig_argv)
    stop_everything()
    if how == "launchctl":
        subprocess.Popen(command)  # kickstart -k stops this process and starts a new one
    else:
        os.execv(command[0], command)
