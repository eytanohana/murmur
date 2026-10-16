"""Start murmur.app at login.

First choice is SMAppService.mainApp (ServiceManagement), the system's own login item
for an app. If it cannot register this app (an ad-hoc signed build may be refused), the
fallback is a small user LaunchAgent that opens the bundle at login. Neither is used
while the developer LaunchAgent com.user.murmur is installed: two copies would both
type, so the user is told to run `murmur uninstall-agent` first and nothing is removed.
"""

from __future__ import annotations

import logging
import plistlib
from dataclasses import dataclass
from pathlib import Path

from .agent import plist_path as dev_agent_plist

log = logging.getLogger("murmur")

LOGIN_LABEL = "com.eytanohana.murmur.login"
# SMAppServiceStatus values
NOT_REGISTERED, ENABLED, REQUIRES_APPROVAL, NOT_FOUND = 0, 1, 2, 3


@dataclass(frozen=True)
class Result:
    ok: bool
    method: str  # "service", "launchagent", "conflict", "unavailable" or "none"
    message: str


def agent_installed(home: Path | None = None) -> bool:
    """True when the developer LaunchAgent (com.user.murmur) is installed."""
    return dev_agent_plist(home or Path.home()).exists()


def fallback_plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LOGIN_LABEL}.plist"


def fallback_plist(bundle: Path) -> dict:
    return {
        "Label": LOGIN_LABEL,
        "ProgramArguments": ["/usr/bin/open", "-a", str(bundle)],
        "RunAtLoad": True,
    }


class MainAppService:
    """SMAppService.mainApp through PyObjC (ServiceManagement is not a pyobjc package)."""

    def __init__(self):
        import objc

        objc.loadBundle(
            "ServiceManagement",
            {},
            bundle_path="/System/Library/Frameworks/ServiceManagement.framework",
        )
        self._service = objc.lookUpClass("SMAppService").mainAppService()

    def register(self) -> None:
        ok, error = self._service.registerAndReturnError_(None)
        if not ok:
            raise RuntimeError(str(error))

    def unregister(self) -> None:
        ok, error = self._service.unregisterAndReturnError_(None)
        if not ok:
            raise RuntimeError(str(error))

    def status(self) -> int:
        return int(self._service.status())


def _remove_fallback(home: Path) -> None:
    path = fallback_plist_path(home)
    if path.exists():
        path.unlink()


def set_launch_at_login(
    enabled: bool, bundle: Path | None, home: Path | None = None, service=None
) -> Result:
    """Turn the login item on or off. `service` is injectable for tests."""
    home = home or Path.home()
    if bundle is None:
        return Result(False, "unavailable", "Launch at login needs the murmur.app build.")
    if enabled and agent_installed(home):
        return Result(
            False,
            "conflict",
            "The developer LaunchAgent com.user.murmur is installed. Run `murmur "
            "uninstall-agent` first, so two copies do not start at login.",
        )
    try:
        service = service or MainAppService()
    except Exception as exc:  # noqa: BLE001  ServiceManagement missing or unusable
        service, load_error = None, exc
    else:
        load_error = None
    if not enabled:
        if service is not None:
            try:
                service.unregister()
            except Exception as exc:  # noqa: BLE001  not registered is not an error
                log.debug("SMAppService unregister: %s", exc)
        _remove_fallback(home)
        return Result(True, "none", "murmur will not start at login.")
    if service is not None:
        try:
            service.register()
            status = service.status()
        except Exception as exc:  # noqa: BLE001  fall back below
            log.info("SMAppService could not register murmur (%s); using a LaunchAgent", exc)
        else:
            if status == ENABLED:
                return Result(True, "service", "murmur will start at login.")
            if status == REQUIRES_APPROVAL:
                return Result(
                    True,
                    "service",
                    "Approve murmur in System Settings > General > Login Items.",
                )
            log.info("SMAppService status %s after register; using a LaunchAgent", status)
    elif load_error is not None:
        log.info("ServiceManagement unavailable (%s); using a LaunchAgent", load_error)
    return _write_fallback(home, bundle)


def _write_fallback(home: Path, bundle: Path) -> Result:
    path = fallback_plist_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            plistlib.dump(fallback_plist(bundle), f)
    except OSError as exc:
        return Result(False, "unavailable", f"Could not write {path}: {exc}")
    return Result(True, "launchagent", "murmur will start at login (a LaunchAgent opens it).")


def apply(enabled: bool) -> Result:
    """Settings hook: apply the toggle for the running app and log the outcome."""
    from .frozen import bundle_path, is_frozen

    result = set_launch_at_login(enabled, bundle_path() if is_frozen() else None)
    (log.info if result.ok else log.warning)("Launch at login: %s", result.message)
    return result
