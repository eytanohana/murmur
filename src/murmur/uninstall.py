"""Uninstall murmur from inside the app: the plan, the safety checks and the operations.

Nothing here touches the system by itself. `build_plan` turns the current state and the
user's two checkboxes into an ordered list of steps, `execute` runs them through an
`Operations` object (a fake in tests, `SystemOperations` in the app) and never lets one
failed step stop the others. The app bundle is trashed last.
"""

from __future__ import annotations

import logging
import plistlib
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import models
from .models import CachedModel

log = logging.getLogger("murmur")

BUNDLE_ID = "com.eytanohana.murmur"

LOGIN_OFF, DEV_AGENT, DELETE, TCC_RESET, TRASH = (
    "login_off",
    "dev_agent",
    "delete",
    "tcc_reset",
    "trash",
)


@dataclass(frozen=True)
class Choices:
    delete_data: bool = True  # settings and log
    delete_models: bool = False  # the curated models in the shared Hugging Face cache


@dataclass(frozen=True)
class Context:
    """The facts the plan depends on, gathered once by the caller."""

    frozen: bool
    bundle: Path | None
    config_dir: Path
    log_file: Path
    dev_agent_installed: bool
    models: tuple[CachedModel, ...] = ()
    uninstall_log: Path | None = None  # step timings; kept unless the user deletes the log


@dataclass(frozen=True)
class Step:
    kind: str
    label: str
    path: Path | None = None


@dataclass(frozen=True)
class StepResult:
    step: Step
    ok: bool
    message: str
    seconds: float = 0.0


def available(ctx: Context) -> bool:
    """The item exists only in the .app, where there is something to move to the Trash."""
    return ctx.frozen and ctx.bundle is not None


def build_plan(ctx: Context, choices: Choices) -> list[Step]:
    """Ordered steps; the Trash step is last. Empty when uninstalling is not available."""
    if not available(ctx):
        return []
    steps = [Step(LOGIN_OFF, "Turn off launch at login")]
    if ctx.dev_agent_installed:
        steps.append(Step(DEV_AGENT, "Remove the developer LaunchAgent com.user.murmur"))
    if choices.delete_data:
        steps.append(Step(DELETE, f"Delete {ctx.config_dir}", ctx.config_dir))
        steps.append(Step(DELETE, f"Delete {ctx.log_file}", ctx.log_file))
    if choices.delete_models:
        steps += [Step(DELETE, f"Delete model {m.name}", m.path) for m in ctx.models]
    steps.append(Step(TCC_RESET, "Reset murmur's privacy permissions"))
    steps.append(Step(TRASH, f"Move {ctx.bundle} to the Trash", ctx.bundle))
    return steps


def allowed_delete_paths(ctx: Context) -> frozenset[Path]:
    """The only paths a delete step may remove: exactly the ones the app itself uses."""
    extra = {ctx.uninstall_log} if ctx.uninstall_log is not None else set()
    return frozenset({ctx.config_dir, ctx.log_file, *extra, *(m.path for m in ctx.models)})


def safe_delete_target(path: Path, allowed: Iterable[Path]) -> str | None:
    """None when `path` may be deleted, else why not. No resolving, no globbing."""
    if not path.is_absolute() or ".." in path.parts:
        return f"refusing a relative or odd path: {path}"
    if path in (Path("/"), Path.home()) or len(path.parts) < 3:
        return f"refusing a top-level path: {path}"
    if path not in set(allowed):
        return f"refusing {path}: not one of murmur's own paths"
    return None


def bundle_id_of(bundle: Path) -> str | None:
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as f:
            return plistlib.load(f).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        return None


def check_trash_target(
    path: Path,
    running_bundle: Path | None,
    read_bundle_id: Callable[[Path], str | None] | None = None,
) -> str | None:
    """None when `path` is the running murmur.app, else why it must not be trashed."""
    if running_bundle is None or path != running_bundle:
        return f"refusing to trash {path}: it is not the running app"
    if path.suffix != ".app":
        return f"refusing to trash {path}: not an .app"
    read = read_bundle_id or bundle_id_of  # looked up at call time, so tests can replace it
    if read(path) != BUNDLE_ID:
        return f"refusing to trash {path}: its bundle id is not {BUNDLE_ID}"
    return None


class Operations(Protocol):
    def login_off(self) -> str: ...

    def remove_dev_agent(self) -> str: ...

    def delete_path(self, path: Path) -> str: ...

    def reset_permissions(self) -> str: ...

    def trash(self, path: Path) -> str: ...


STEP_TIMEOUT_S = 30.0


def run_with_timeout(fn: Callable[[], str], timeout: float | None) -> str:
    """Run `fn` on a helper thread so a hung system call cannot hold up the other steps.

    The helper cannot be killed; after the timeout it is abandoned (it is a daemon thread)
    and the step is reported as failed.
    """
    if timeout is None:
        return fn()
    box: list = []

    def target() -> None:
        try:
            box.append((True, fn()))
        except BaseException as exc:  # noqa: BLE001  handed back to the caller below
            box.append((False, exc))

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"timed out after {timeout:g}s, continuing")
    ok, value = box[0]
    if not ok:
        raise value
    return value


def execute(
    plan: list[Step],
    ops: Operations,
    ctx: Context,
    on_result: Callable[[StepResult], None] | None = None,
    on_start: Callable[[Step], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    step_timeout: float | None = STEP_TIMEOUT_S,
) -> list[StepResult]:
    """Run every step in order, timing each. A step that raises, fails or hangs never stops
    the rest."""
    allowed = allowed_delete_paths(ctx)

    def run(step: Step) -> str:
        if step.kind == LOGIN_OFF:
            return ops.login_off()
        if step.kind == DEV_AGENT:
            return ops.remove_dev_agent()
        if step.kind == DELETE:
            problem = safe_delete_target(step.path, allowed)
            if problem:
                raise PermissionError(problem)
            return ops.delete_path(step.path)
        if step.kind == TCC_RESET:
            return ops.reset_permissions()
        if step.kind == TRASH:
            problem = check_trash_target(step.path, ctx.bundle)
            if problem:
                raise PermissionError(problem)
            return ops.trash(step.path)
        raise ValueError(f"unknown step {step.kind}")

    results: list[StepResult] = []
    for step in plan:
        if on_start:
            on_start(step)
        started = clock()
        try:
            ok, message = True, run_with_timeout(lambda step=step: run(step), step_timeout)
        except Exception as exc:  # noqa: BLE001  isolate failures: report and go on
            ok, message = False, str(exc)
        result = StepResult(step, ok, message, clock() - started)
        (log.info if ok else log.warning)(
            "Uninstall: %s: %s in %.2fs (%s)",
            step.label,
            "ok" if ok else "failed",
            result.seconds,
            message,
        )
        results.append(result)
        if on_result:
            on_result(result)
    return results


class TimingLog:
    """Appends one line per step to a file the uninstall does not delete (unless asked to).

    Written and flushed as it goes, because the normal log is usually deleted by the same
    uninstall. A failure to write never fails the uninstall.
    """

    def __init__(self, path: Path | None, clock: Callable[[], float] = time.monotonic):
        self.path = path
        self.clock = clock
        self.started = clock()

    def write(self, text: str) -> None:
        if self.path is None:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(f"{stamp} +{self.clock() - self.started:6.2f}s  {text}\n")
                f.flush()
        except OSError:
            pass

    def step(self, result: StepResult) -> None:
        status = "ok" if result.ok else "FAILED"
        self.write(f"{result.step.label}: {status} in {result.seconds:.2f}s ({result.message})")


def trash_failed(results: Iterable[StepResult]) -> StepResult | None:
    return next((r for r in results if r.step.kind == TRASH and not r.ok), None)


# ---- dialog text (pure, so the wording is tested) --------------------------------------------------------


def data_option(ctx: Context) -> tuple[str, str]:
    """Checkbox title and detail for deleting settings and log."""
    size = sum(models.folder_size(p) for p in (ctx.config_dir,) if p.is_dir())
    size += ctx.log_file.stat().st_size if ctx.log_file.is_file() else 0
    detail = f"{ctx.config_dir} and {ctx.log_file}"
    if ctx.uninstall_log is not None:
        detail += f", and the uninstall log {ctx.uninstall_log}"
    if size:
        detail += f" ({models.format_bytes(size)})"
    return "Also delete my settings and log", detail


def models_option(ctx: Context) -> tuple[str, str, bool]:
    """Checkbox title, detail and whether it can be ticked, for deleting downloaded models."""
    if not ctx.models:
        return "Also delete downloaded models", "None of murmur's models are downloaded.", False
    total = models.format_bytes(sum(m.size_bytes for m in ctx.models))
    names = ", ".join(f"{m.name} {models.format_bytes(m.size_bytes)}" for m in ctx.models)
    detail = (
        f"{names}. Off by default because the Hugging Face cache is shared with other "
        "tools; only these folders are removed, never the whole cache."
    )
    return f"Also delete downloaded models ({total})", detail, True


def always_done(ctx: Context) -> list[str]:
    lines = ["Turn off launch at login (the login item and its LaunchAgent plist)."]
    if ctx.dev_agent_installed:
        lines.append(
            "Remove the developer LaunchAgent com.user.murmur (stop it and delete its plist)."
        )
    lines += [
        f"Reset murmur's privacy permissions (tccutil reset All {BUNDLE_ID}).",
        f"Move {ctx.bundle} to the Trash, then quit.",
    ]
    return lines


def summary_text(ctx: Context) -> str:
    text = "This will always:\n" + "\n".join(f"  - {line}" for line in always_done(ctx))
    if ctx.uninstall_log is not None:
        text += (
            f"\n\nThe time each step takes is written to {ctx.uninstall_log}. "
            "That file is kept unless you tick the first box below."
        )
    return text


# ---- the real operations -----------------------------------------------------------------------------------------


class SystemOperations:
    """Does the real work. Every system call is a replaceable attribute, for tests."""

    def __init__(
        self,
        ctx: Context,
        home: Path | None = None,
        run: Callable = subprocess.run,
        trash_fn: Callable[[Path], None] | None = None,
        agent_runner: Callable | None = None,
    ):
        self.ctx = ctx
        self.home = home or Path.home()
        self._run = run
        self._trash = trash_fn or _recycle
        self._agent_runner = agent_runner  # runs launchctl for the developer agent

    def login_off(self) -> str:
        from . import launchlogin

        result = launchlogin.set_launch_at_login(False, self.ctx.bundle, self.home)
        return result.message

    def remove_dev_agent(self) -> str:
        from . import agent

        lines: list[str] = []
        runner = self._agent_runner or agent.run_command
        agent.uninstall(self.home, runner=runner, out=lines.append)
        return "; ".join(lines) or "removed"

    def delete_path(self, path: Path) -> str:
        problem = safe_delete_target(path, allowed_delete_paths(self.ctx))
        if problem:
            raise PermissionError(problem)
        if path.is_symlink():
            path.unlink()  # remove the link itself, never what it points at
            return "removed (a link)"
        if not path.exists():
            return "already gone"
        if path.is_dir():
            shutil.rmtree(path)  # does not follow symlinks inside the folder
        else:
            path.unlink()
        return "removed"

    def reset_permissions(self) -> str:
        done = self._run(["tccutil", "reset", "All", BUNDLE_ID], capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(f"tccutil exited {done.returncode}: {done.stderr.strip()}")
        return "permissions reset"

    def trash(self, path: Path) -> str:
        problem = check_trash_target(path, self.ctx.bundle)
        if problem:
            raise PermissionError(problem)
        if not _parent_writable(path):
            raise PermissionError(
                f"{path.parent} is not writable; drag murmur to the Trash yourself"
            )
        self._trash(path)
        return "moved to the Trash"


def _parent_writable(path: Path) -> bool:
    import os

    return os.access(path.parent, os.W_OK)


def _recycle(path: Path) -> None:  # pragma: no cover - uses the user's Trash
    """NSWorkspace.recycleURLs, which also works for the running app. Waits for the result."""
    import threading

    import AppKit
    from Foundation import NSURL

    done, box = threading.Event(), []

    def finished(new_urls, error):
        box.append(error)
        done.set()

    AppKit.NSWorkspace.sharedWorkspace().recycleURLs_completionHandler_(
        [NSURL.fileURLWithPath_(str(path))], finished
    )
    if not done.wait(15):
        raise TimeoutError("the Trash did not answer")
    if box and box[0] is not None:
        raise OSError(str(box[0]))
