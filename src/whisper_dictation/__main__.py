"""Entry point: `uv run whisper-dictation` or `python -m whisper_dictation`."""

from __future__ import annotations

import argparse
import logging
import threading
import time

from .app import Dictation
from .config import CONFIG_PATH, load_config
from .feedback import make_menubar_status
from .hotkey import HotkeyListener
from .inserter import Inserter, accessibility_trusted
from .recorder import Recorder
from .transcriber import Transcriber

log = logging.getLogger("whisper_dictation")


def check_permissions() -> None:
    """Print which macOS permissions look missing, where the OS can tell us."""
    if not accessibility_trusted():
        log.warning("Accessibility not granted (AXIsProcessTrusted is false). Typing and "
                    "pasting are silently dropped without it. Add your terminal app under "
                    "System Settings > Privacy & Security > Accessibility and restart it.")
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            log.warning("Input Monitoring not granted. Add your terminal app under System "
                        "Settings > Privacy & Security > Input Monitoring.")
    except (ImportError, AttributeError):
        pass
    log.info("Microphone permission is requested on first recording; allow it when asked.")


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
    parser.add_argument("--menubar", action="store_true", help="show a menu bar status item")
    parser.add_argument("--config", type=str, default=None, help=f"config path (default {CONFIG_PATH})")
    parser.add_argument("--test-insert", action="store_true",
                        help="wait 3 s, type a test phrase at the cursor, then exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    from pathlib import Path

    cfg = load_config(Path(args.config) if args.config else None)
    use_menubar = args.menubar or cfg.menubar

    if args.test_insert:
        test_insert(load_config(Path(args.config) if args.config else None))
        return

    check_permissions()
    log.info("Loading model %s ...", cfg.model_repo)
    transcriber = Transcriber(cfg.model_repo, cfg.language, cfg.silence_rms)
    t0 = time.monotonic()
    transcriber.warm_up(cfg.sample_rate)
    log.info("Model loaded and warmed in %.1fs", time.monotonic() - t0)

    status = make_menubar_status() if use_menubar else None
    recorder = Recorder(cfg.sample_rate, cfg.max_record_s)
    inserter = Inserter(cfg.insert_mode, cfg.paste_restore_delay_ms)
    dictation = Dictation(cfg, recorder, transcriber, inserter, status)
    listener = HotkeyListener(dictation.on_press, dictation.on_release,
                              dictation.on_other_key, cfg.option_keys)
    listener.start()
    log.info("Ready. Hold Option (%s), speak, release. Ctrl+C to quit.", cfg.option_keys)
    try:
        if status is not None:
            status.run()  # rumps run loop owns the main thread
        else:
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


if __name__ == "__main__":
    main()
