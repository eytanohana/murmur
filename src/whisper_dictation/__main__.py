"""Entry point: `uv run whisper-dictation` or `python -m whisper_dictation`."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from .agent import real_python
from .app import Dictation
from .config import CONFIG_PATH, load_config
from .hotkey import HotkeyListener
from .indicator import make_indicator
from .inserter import Inserter, accessibility_trusted
from .recorder import Recorder
from .transcriber import Transcriber

log = logging.getLogger("whisper_dictation")


def check_microphone(sample_rate: int = 16000) -> None:
    """Open the input briefly so macOS shows the Microphone prompt at startup.

    Under launchd there is no terminal and no first recording to trigger it.
    A denied microphone delivers only exact zeros.
    """
    import numpy as np
    import sounddevice as sd

    chunks: list = []
    try:
        with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32",
                            callback=lambda indata, *_: chunks.append(indata.copy())):
            time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not open the microphone (%s). Grant Microphone access to %s in "
                    "System Settings > Privacy & Security.", exc, real_python(Path(sys.executable)))
        return
    if not chunks or not np.any(np.concatenate(chunks)):
        log.warning("The microphone returned only silence. If a permission prompt appeared, "
                    "allow it and restart. Otherwise grant Microphone access to %s in System "
                    "Settings > Privacy & Security.", real_python(Path(sys.executable)))


def check_permissions() -> None:
    """Warn about missing macOS permissions and trigger the system prompts.

    The prompts are requested for this process, so they also appear when it runs
    from launchd with no terminal attached. Warnings name the real python binary.
    """
    binary = real_python(Path(sys.executable))
    if not accessibility_trusted(prompt=True):
        log.warning("Accessibility not granted (AXIsProcessTrusted is false). Typing and "
                    "pasting are silently dropped without it. Add %s (or the terminal app "
                    "running this) under System Settings > Privacy & Security > "
                    "Accessibility, then restart.", binary)
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
            log.warning("Input Monitoring not granted. Add %s (or the terminal app) under "
                        "System Settings > Privacy & Security > Input Monitoring, then "
                        "restart.", binary)
    except (ImportError, AttributeError):
        pass
    check_microphone()


def test_insert(cfg) -> None:
    """Type a phrase from a worker thread, like the streaming path does."""
    phrase = "hello from whisper-dictation"
    if not accessibility_trusted(prompt=True):
        log.warning("Accessibility is not granted; the text will probably not appear.")
    log.info("Click into a text field now. Typing in 3 seconds ...")
    time.sleep(3)
    inserter = Inserter(cfg.insert_mode, cfg.paste_restore_delay_ms)
    worker = threading.Thread(target=inserter.type_text, args=(phrase,))
    worker.start()
    worker.join()
    log.info("Done. If nothing appeared, check Accessibility for your terminal app.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="whisper-dictation", description=__doc__)
    parser.add_argument("--menubar", action="store_true",
                        help="alias for indicator = \"menubar\" (overrides the config)")
    parser.add_argument("--config", type=str, default=None, help=f"config path (default {CONFIG_PATH})")
    parser.add_argument("--test-insert", action="store_true",
                        help="wait 3 s, type a test phrase at the cursor, then exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", metavar="command")
    inst = sub.add_parser("install-agent", help="install and start the login LaunchAgent")
    inst.add_argument("--dry-run", action="store_true", help="print what would happen")
    inst.add_argument("--no-start", action="store_true",
                      help="write the plist but do not run launchctl")
    uninst = sub.add_parser("uninstall-agent", help="stop and remove the LaunchAgent")
    uninst.add_argument("--dry-run", action="store_true", help="print what would happen")
    sub.add_parser("agent-status", help="show LaunchAgent state, plist and log paths")
    args = parser.parse_args(argv)

    if args.command:
        from .agent import main_agent

        sys.exit(main_agent(args.command, args))

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(Path(args.config) if args.config else None)
    kind = "menubar" if args.menubar else cfg.indicator

    if args.test_insert:
        test_insert(load_config(Path(args.config) if args.config else None))
        return

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
    listener = HotkeyListener(dictation.on_press, dictation.on_release,
                              dictation.on_other_key, cfg.option_keys)
    listener.start()
    log.info("Ready. Hold Option (%s) for %.1f s, speak, release. Indicator: %s. Ctrl+C to quit.",
             cfg.option_keys, cfg.min_hold_ms / 1000, kind)
    try:
        # The indicator owns the main thread (AppKit); SIGTERM and SIGINT end it with status 0.
        indicator.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        listener.stop()


if __name__ == "__main__":
    main()
