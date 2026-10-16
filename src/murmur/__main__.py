"""Entry point: `uv run murmur` or `python -m murmur`."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .agent import main_agent
from .app import Dictation, State
from .config import CONFIG_PATH, Config, load_config
from .host import make_indicator
from .hotkey import HotkeyListener
from .inserter import Inserter
from .models import prepare_offline
from .modelswitch import MlxLoader, ModelSwitcher
from .permissions import check_permissions
from .recorder import Recorder
from .settings import LiveSettings, restart_now
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


def run_dictation(cfg: Config, config_path: Path) -> None:
    """Load the model, start the hotkey listener and hand the main thread to the indicator."""
    check_permissions()
    prepare_offline(cfg.model_repo)  # offline only when the model is cached; else it downloads
    log.info("Loading model %s ...", cfg.model_repo)
    transcriber = Transcriber(cfg)
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
    )
    live.model_hook = switcher.request
    indicator = make_indicator(live, restart, switcher)
    recorder = Recorder(cfg)
    inserter = Inserter(cfg)
    dictation = Dictation(cfg, recorder, transcriber, inserter, indicator, refresh=live.refresh)
    listener = HotkeyListener(dictation.on_press, dictation.on_release, dictation.on_other_key, cfg)
    listener.start()
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


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command:
        sys.exit(main_agent(args.command, args))

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    config_path = Path(args.config) if args.config else CONFIG_PATH
    cfg = load_config(config_path)

    run_dictation(cfg, config_path)


if __name__ == "__main__":
    main()
