"""Menu bar settings logic that needs no display: the menu model, config writing and restart."""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path

import tomlkit

from .agent import LABEL, service_target
from .config import (
    INSERT_MODES,
    LIVE_KEYS,
    POSITIONS,
    Config,
    load_config,
    validate_setting,
)

CONFIG_HEADER = (
    "# murmur config. Every key is optional; see the README for the full list.\n"
    "# Changes apply on the next Option press (a few keys need a restart).\n"
)

log = logging.getLogger("murmur")

ICON_IDLE = "mic"
ICON_RECORDING = "mic.fill"


def icon_for(state: str) -> str:
    """SF Symbol for a state. Transcribing keeps the idle icon: the pill shows '...'."""
    return ICON_RECORDING if state == "recording" else ICON_IDLE


ICON_DESCRIPTIONS = {
    "idle": "murmur is running",
    "recording": "murmur is recording",
    "transcribing": "murmur is transcribing",
}


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


def build_menu(
    cfg: Config, pending: Sequence[str] = (), error: str | None = None
) -> list[MenuItem]:
    """The menu for the live `cfg`.

    `pending` names RESTART keys changed in the file but not yet applied; `error` is set
    when the file on disk is invalid and the old values are being kept.
    """
    positions = tuple(
        MenuItem("setting", p, "indicator_position", p, checked=cfg.indicator_position == p)
        for p in POSITIONS
    )
    modes = tuple(
        MenuItem("setting", m, "insert_mode", m, checked=cfg.insert_mode == m) for m in INSERT_MODES
    )
    return [
        MenuItem(
            "header", "Config error: see log" if error else "murmur is running", enabled=False
        ),
        MenuItem("header", cfg.model_repo, enabled=False),
        MenuItem("separator"),
        MenuItem("setting", "Streaming", "stream", not cfg.stream, checked=cfg.stream),
        MenuItem("setting", "Sounds", "sounds", not cfg.sounds, checked=cfg.sounds),
        MenuItem("submenu", "Overlay position", children=positions),
        MenuItem(
            "submenu",
            "Insert mode (used when streaming is off)",
            enabled=not cfg.stream,
            children=modes,
        ),
        MenuItem("separator"),
        MenuItem("action", "Open config file", action="open_config"),
        MenuItem(
            "action",
            f"Restart to apply changes ({', '.join(sorted(pending))})"
            if pending
            else "Restart murmur",
            action="restart",
            checked=bool(pending),
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


def file_mtime(path: Path) -> int | None:
    """Modification time in ns, or None when the file is missing or unreadable."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class LiveSettings:
    """The one live Config plus what changed on disk that still needs a restart.

    The file is checked only when refresh() is called (a key press, a menu opening):
    one stat, and a reload only when the mtime moved. Our own writes update the
    remembered mtime so they are not reloaded.
    """

    def __init__(self, path: Path, cfg: Config, stat: Callable[[Path], int | None] = file_mtime):
        self.path = path
        self.cfg = cfg
        # (repo, from_file): a model_repo change is not applied here; the model switcher loads
        # the model first and calls commit_model once it is ready.
        self.model_hook: Callable[[str, bool], None] | None = None
        self.pending: dict[str, object] = {}  # RESTART keys whose file value differs
        self.error: str | None = None  # set while the file on disk is invalid
        self._stat = stat
        self._seen = stat(path)

    def _apply(self, key: str, value: object, from_file: bool = False) -> None:
        if key == "model_repo" and self.model_hook is not None:
            if value != self.cfg.model_repo:
                self.model_hook(value, from_file)
            return
        if key in LIVE_KEYS:
            setattr(self.cfg, key, value)
        elif value != getattr(self.cfg, key):
            self.pending[key] = value
        else:
            self.pending.pop(key, None)

    def refresh(self) -> bool:
        """Reload if the file changed since we last saw it. True when it was (re)read."""
        mtime = self._stat(self.path)
        if mtime == self._seen:
            return False
        self._seen = mtime
        try:
            new = load_config(self.path)
        except (ValueError, tomllib.TOMLDecodeError, OSError) as exc:
            self.error = str(exc)
            log.warning("Ignoring %s, keeping the previous settings: %s", self.path, exc)
            return True
        self.error = None
        changed = [k for k in LIVE_KEYS if getattr(new, k) != getattr(self.cfg, k)]
        for f in fields(Config):
            self._apply(f.name, getattr(new, f.name), from_file=True)
        if changed:
            log.info(
                "Config reloaded: %s (applies on the next Option press)",
                ", ".join(f"{k}={getattr(new, k)!r}" for k in sorted(changed)),
            )
        if self.pending:
            log.info("Restart murmur to apply: %s", ", ".join(sorted(self.pending)))
        return True

    def set_value(self, key: str, value: object) -> None:
        """Validate, write and apply one setting. Raises ValueError or OSError."""
        if key == "model_repo" and self.model_hook is not None:
            validate_setting(key, value)
            self._apply(key, value)  # the file is written by commit_model after the load
            return
        write_setting(self.path, key, value)
        self._seen = self._stat(self.path)  # our own write: do not reload it
        self.error = None
        self._apply(key, value)

    def commit_model(self, repo: str, language: str | None = None) -> None:
        """The new model is installed: make it the live value and persist it."""
        self.cfg.model_repo = repo
        updates = {"model_repo": repo}
        if language is not None:
            self.cfg.language = language
            updates["language"] = language
        try:
            for key, value in updates.items():
                write_setting(self.path, key, value)
            self._seen = self._stat(self.path)
        except (ValueError, OSError) as exc:  # the swap happened; only the file is behind
            log.warning("Could not save the model to %s: %s", self.path, exc)

    def revert_model(self, failed_repo: str, old_repo: str, from_file: bool) -> None:
        """A switch failed. A hand-edited file still names the failed model: put the old one back."""
        if not from_file:
            return
        try:
            write_setting(self.path, "model_repo", old_repo)
            self._seen = self._stat(self.path)
            log.warning("Restored model_repo = %r in %s", old_repo, self.path)
        except (ValueError, OSError) as exc:
            log.warning("Could not restore model_repo in %s: %s", self.path, exc)
