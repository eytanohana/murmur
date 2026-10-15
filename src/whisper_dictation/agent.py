"""Install, remove and inspect the launchd LaunchAgent that runs dictation at login."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

LABEL = "com.user.whisper-dictation"
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def run_command(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def project_dir() -> Path:
    """Repo root: the folder that holds pyproject.toml and .venv."""
    root = Path(__file__).resolve().parents[2]
    if not (root / "pyproject.toml").exists():
        raise SystemExit(f"Cannot find the project root at {root}; run from a checkout "
                         "installed with `uv sync`.")
    return root


def plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path(home: Path) -> Path:
    return home / "Library" / "Logs" / "whisper-dictation.log"


def build_plist(python: Path, project: Path, home: Path) -> dict:
    """LaunchAgent definition. Runs the venv python directly, not `uv run`.

    KeepAlive with SuccessfulExit false restarts the job only after a non-zero
    exit (a crash); a clean exit stays stopped. HF_HUB_OFFLINE keeps login
    startup from depending on the network once the model is cached.
    """
    log = str(log_path(home))
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "whisper_dictation"],
        "WorkingDirectory": str(project),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {
            "PATH": f"{python.parent}:{DEFAULT_PATH}",
            "PYTHONUNBUFFERED": "1",
            "HF_HUB_OFFLINE": "1",
        },
    }


def real_python(python: Path) -> Path:
    """The binary macOS privacy settings are granted to (symlinks resolved)."""
    return Path(os.path.realpath(python))


def permission_instructions(python: Path) -> str:
    real = real_python(python)
    return f"""\
Grant these three permissions to the Python binary that the agent runs:

    {real}

In System Settings > Privacy & Security, open Accessibility, Input Monitoring
and Microphone, click +, press Cmd+Shift+G in the file chooser, paste the path
above and add it. The Microphone list may only show the binary after it first
asks; if a prompt appears at startup, allow it. Then restart the agent:

    launchctl kickstart -k gui/{os.getuid()}/{LABEL}

This path points into uv's managed Python (usually under ~/.local/share/uv/python).
It changes when uv installs a newer Python patch release for this project; repeat
the grants then. Running from a terminal uses the terminal's permissions instead.
"""


def domain() -> str:
    return f"gui/{os.getuid()}"


def install(home: Path, runner: Runner = run_command, dry_run: bool = False,
            no_start: bool = False, out: Callable[[str], None] = print) -> int:
    project = project_dir()
    python = project / ".venv" / "bin" / "python"
    if not python.exists():
        out(f"{python} not found. Run `uv sync` in {project} first.")
        return 1
    data = build_plist(python, project, home)
    path = plist_path(home)
    if dry_run:
        out(f"[dry run] would write {path}:")
        out(plistlib.dumps(data).decode())
        out(f"[dry run] would run: launchctl bootout {domain()}/{LABEL} (ignore errors)")
        out(f"[dry run] would run: launchctl bootstrap {domain()} {path}")
        out(permission_instructions(python))
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path(home).parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(data, f)
    out(f"Wrote {path}")
    lint = runner(["plutil", "-lint", str(path)])
    if lint.returncode != 0:
        out(f"plutil -lint failed: {lint.stdout}{lint.stderr}")
        return 1
    if no_start:
        out("Skipped launchctl (--no-start).")
    else:
        runner(["launchctl", "bootout", f"{domain()}/{LABEL}"])  # replace a loaded copy
        result = runner(["launchctl", "bootstrap", domain(), str(path)])
        if result.returncode != 0:
            out(f"launchctl bootstrap failed: {result.stdout}{result.stderr}")
            return 1
        out(f"Started {LABEL}. Log: {log_path(home)}")
    out(permission_instructions(python))
    return 0


def uninstall(home: Path, runner: Runner = run_command, dry_run: bool = False,
              out: Callable[[str], None] = print) -> int:
    path = plist_path(home)
    if dry_run:
        out(f"[dry run] would run: launchctl bootout {domain()}/{LABEL}")
        out(f"[dry run] would delete {path}")
        return 0
    result = runner(["launchctl", "bootout", f"{domain()}/{LABEL}"])
    if result.returncode == 0:
        out(f"Stopped {LABEL}.")
    else:
        out(f"{LABEL} was not loaded.")
    if path.exists():
        path.unlink()
        out(f"Removed {path}")
    return 0


def status(home: Path, runner: Runner = run_command,
           out: Callable[[str], None] = print) -> int:
    path = plist_path(home)
    out(f"plist: {path} ({'present' if path.exists() else 'missing'})")
    out(f"log:   {log_path(home)}")
    result = runner(["launchctl", "print", f"{domain()}/{LABEL}"])
    if result.returncode != 0:
        out("agent: not loaded")
        return 1
    wanted = ("state =", "pid =", "last exit code", "runs =", "program =")
    for line in result.stdout.splitlines():
        if any(line.strip().startswith(w) for w in wanted):
            out("agent: " + line.strip())
    return 0


def main_agent(command: str, args, home: Path | None = None) -> int:
    home = home or Path.home()
    if command == "install-agent":
        return install(home, dry_run=args.dry_run, no_start=args.no_start)
    if command == "uninstall-agent":
        return uninstall(home, dry_run=args.dry_run)
    return status(home)


if __name__ == "__main__":
    sys.exit(main_agent("agent-status", None))
