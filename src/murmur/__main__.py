"""Entry point: `uv run murmur` or `python -m murmur`."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from .agent import main_agent
from .app import Dictation
from .config import CONFIG_PATH, Config, load_config
from .hotkey import HotkeyListener
from .indicator import make_indicator
from .inserter import Inserter, accessibility_trusted
from .permissions import check_permissions
from .recorder import Recorder
from .transcriber import Transcriber

log = logging.getLogger("murmur")


def test_insert(cfg: Config) -> None:
    """Type a phrase from a worker thread, like the streaming path does."""
    phrase = "hello from murmur"
    if not accessibility_trusted(prompt=True):
        log.warning("Accessibility is not granted; the text will probably not appear.")
    log.info("Click into a text field now. Typing in 3 seconds ...")
    time.sleep(3)
    inserter = Inserter(cfg.insert_mode, cfg.paste_restore_delay_ms)
    worker = threading.Thread(target=inserter.type_text, args=(phrase,))
    worker.start()
    worker.join()
    log.info("Done. If nothing appeared, check Accessibility for your terminal app.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="murmur", description=__doc__)
    parser.add_argument(
        "--menubar",
        action="store_true",
        help='alias for indicator = "menubar" (overrides the config)',
    )
    parser.add_argument(
        "--config", type=str, default=None, help=f"config path (default {CONFIG_PATH})"
    )
    parser.add_argument(
        "--test-insert",
        action="store_true",
        help="wait 3 s, type a test phrase at the cursor, then exit",
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


def run_dictation(cfg: Config, kind: str) -> None:
    """Load the model, start the hotkey listener and hand the main thread to the indicator."""
    check_permissions()
    log.info("Loading model %s ...", cfg.model_repo)
    transcriber = Transcriber(cfg.model_repo, cfg.language, cfg.silence_rms)
    t0 = time.monotonic()
    transcriber.warm_up(cfg.sample_rate)
    log.info("Model loaded and warmed in %.1fs", time.monotonic() - t0)

    indicator = make_indicator(kind, cfg.indicator_position)
    recorder = Recorder(cfg.sample_rate, cfg.max_record_s)
    inserter = Inserter(cfg.insert_mode, cfg.paste_restore_delay_ms)
    dictation = Dictation(cfg, recorder, transcriber, inserter, indicator)
    listener = HotkeyListener(
        dictation.on_press, dictation.on_release, dictation.on_other_key, cfg.option_keys
    )
    listener.start()
    log.info(
        "Ready. Hold Option (%s) for %.1f s, speak, release. Indicator: %s. Ctrl+C to quit.",
        cfg.option_keys,
        cfg.min_hold_ms / 1000,
        kind,
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
    cfg = load_config(Path(args.config) if args.config else None)
    kind = "menubar" if args.menubar else cfg.indicator

    if args.test_insert:
        test_insert(cfg)
        return

    run_dictation(cfg, kind)


if __name__ == "__main__":
    main()
