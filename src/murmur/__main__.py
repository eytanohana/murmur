"""Entry point: `uv run murmur` or `python -m murmur`."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from . import launchlogin, permissions, reopen, selftest
from .agent import main_agent
from .app import Dictation, State
from .config import CONFIG_PATH, Config, load_config
from .frozen import is_frozen
from .host import make_indicator
from .hotkey import HotkeyListener
from .inserter import Inserter
from .instance import InstanceLock
from .mainthread import main_thread
from .models import prepare_offline
from .modelswitch import MlxLoader, ModelSwitcher
from .permissions import check_permissions
from .recorder import Recorder
from .settings import LiveSettings, restart_now, write_first_run_config
from .transcriber import Transcriber

log = logging.getLogger("murmur")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="murmur", description=__doc__)
    parser.add_argument(
        "--config", type=str, default=None, help=f"config path (default {CONFIG_PATH})"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", metavar="command")
    inst = sub.add_parser("install-agent", help="install and start the login LaunchAgent")
    inst.add_argument("--dry-run", action="store_true", help="print what would happen")
    inst.add_argument(
        "--no-start", action="store_true", help="write the plist but do not run launchctl"
    )
    uninst = sub.add_parser("uninstall-agent", help="stop and remove the LaunchAgent")
    uninst.add_argument("--dry-run", action="store_true", help="print what would happen")
    sub.add_parser("agent-status", help="show LaunchAgent state, plist and log paths")
    return parser


def setup_logging(verbose: bool, log_file: Path | None) -> None:
    """Log to stderr; the app has no terminal, so it also appends to ~/Library/Logs."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def run_dictation(
    cfg: Config, config_path: Path, lock_path: Path | None = None, first_launch: bool = False
) -> None:
    """Load the model, start the hotkey listener and hand the main thread to the indicator."""
    lock = InstanceLock(lock_path or config_path.parent / "murmur.lock")
    if not lock.acquire():
        log.warning("murmur is already running (pid %s); this copy exits", lock.holder_pid())
        reopen.post_open_request(str(lock.path))  # the running copy opens its Settings window
        return
    selftest_wav = selftest.requested()
    if not selftest_wav:
        check_permissions()
    cached = prepare_offline(cfg.model_repo)  # offline only when the model is cached
    transcriber = Transcriber(cfg)
    # A cached model loads in a few seconds before the app shows. An uncached one downloads
    # in the background with progress in the menu, so the app is never silent while it waits.
    background_load = not cached
    if not background_load:
        log.info("Loading model %s ...", cfg.model_repo)
        t0 = time.monotonic()
        transcriber.warm_up(cfg.sample_rate)
        log.info("Model loaded and warmed in %.1fs", time.monotonic() - t0)

    def restart() -> None:
        restart_now(lambda: (listener.stop(), recorder.stop()))

    live = LiveSettings(config_path, cfg)
    switcher = ModelSwitcher(
        cfg,
        MlxLoader(transcriber.warm_up),
        transcriber.lock,
        is_busy=lambda: dictation.state is not State.IDLE,
        on_commit=live.commit_model,
        on_failed=live.revert_model,
        ready=not background_load,  # nothing may type before the first model is in
    )
    live.model_hook = switcher.request
    live.key_hooks["launch_at_login"] = launchlogin.apply
    indicator = make_indicator(live, restart, switcher)
    indicator.open_scope = str(lock.path)
    indicator.stop_everything = lambda: (listener.stop(), recorder.stop())
    indicator.is_busy = lambda: dictation.state is not State.IDLE
    indicator.show_settings_on_start = reopen.should_welcome(first_launch, bool(selftest_wav))
    recorder = Recorder(cfg)
    inserter = Inserter(cfg)
    dictation = Dictation(
        cfg,
        recorder,
        transcriber,
        inserter,
        indicator,
        refresh=live.refresh,
        ready=lambda: switcher.ready,
    )
    listener = HotkeyListener(dictation.on_press, dictation.on_release, dictation.on_other_key, cfg)
    if selftest_wav:
        threading.Thread(
            target=selftest.run,
            args=(selftest_wav, transcriber, lambda: switcher.ready),
            daemon=True,
        ).start()
    else:
        # The snapshot is taken right before the listener is created, so it describes the
        # permissions the listener's event tap starts with. Only a later grant is a flip.
        initial = permissions.probe()
        listener.start()
        watcher = permissions.PermissionWatcher(
            permissions.probe,
            initial,
            permissions.PermissionActions(
                restart=lambda: main_thread(restart),
                is_busy=lambda: dictation.state is not State.IDLE,
                recreate_listener=listener.recreate,
                recheck_microphone=permissions.recheck_microphone,
                guard=permissions.RestartGuard(config_path.parent / "permission-restart"),
            ),
        )
        watcher.start()  # nothing is polled when every permission was already granted
    if background_load:
        switcher.start_initial(cfg.model_repo)
    log.info(
        "Ready. Hold Option (%s) for %.1f s, speak, release. Indicator: %s. Ctrl+C to quit.",
        cfg.option_keys,
        cfg.min_hold_ms / 1000,
        cfg.indicator,
    )
    try:
        # The indicator owns the main thread (AppKit); SIGTERM and SIGINT end it with status 0.
        indicator.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        listener.stop()
        lock.release()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command:
        sys.exit(main_agent(args.command, args))

    frozen = is_frozen()
    log_file = Path.home() / "Library" / "Logs" / "murmur.log" if frozen else None
    setup_logging(args.verbose, log_file)
    config_path = Path(args.config) if args.config else CONFIG_PATH
    first_launch = frozen and not args.config and write_first_run_config(config_path)
    if first_launch:
        log.info("First launch: wrote %s with the menu bar icon on", config_path)
    cfg = load_config(config_path)

    run_dictation(cfg, config_path, first_launch=first_launch)


if __name__ == "__main__":
    main()
