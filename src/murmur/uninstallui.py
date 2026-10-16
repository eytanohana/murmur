"""The Uninstall murmur... confirmation dialog and the controller that runs the plan.

The plan and the operations live in uninstall.py. This module asks first (never uninstall
without the confirmation), stops dictation, runs the steps on a background thread and quits.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from . import launchlogin, models, uninstall
from .agent import log_path
from .config import CONFIG_PATH
from .frozen import bundle_path, is_frozen
from .mainthread import main_thread
from .modelswitch import ModelSwitcher
from .uninstall import Choices, Context

log = logging.getLogger("murmur")

ALERT_WIDTH = 440


def current_context() -> Context:
    """The state the plan depends on, from the same constants the app itself uses."""
    return Context(
        frozen=is_frozen(),
        bundle=bundle_path() if is_frozen() else None,
        config_dir=CONFIG_PATH.parent,
        log_file=log_path(Path.home()),
        dev_agent_installed=launchlogin.agent_installed(),
        models=tuple(models.curated_cache()),
    )


def build_alert(ctx: Context):
    """The warning alert with its two checkboxes. Returns (alert, data_box, models_box)."""
    import AppKit

    alert = AppKit.NSAlert.alloc().init()
    alert.setAlertStyle_(AppKit.NSAlertStyleWarning)
    alert.setMessageText_("Uninstall murmur?")
    alert.setInformativeText_(uninstall.summary_text(ctx))
    cancel = alert.addButtonWithTitle_("Cancel")  # the first button is the default
    cancel.setKeyEquivalent_("\r")
    remove = alert.addButtonWithTitle_("Uninstall")
    remove.setHasDestructiveAction_(True)
    remove.setKeyEquivalent_("")

    def option(title: str, detail: str, checked: bool, enabled: bool = True):
        box = AppKit.NSButton.checkboxWithTitle_target_action_(title, None, None)
        box.setState_(1 if checked else 0)
        box.setEnabled_(enabled)
        label = AppKit.NSTextField.wrappingLabelWithString_(detail)
        label.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        label.setPreferredMaxLayoutWidth_(ALERT_WIDTH - 22)
        indent = AppKit.NSStackView.stackViewWithViews_([label])
        indent.setEdgeInsets_(AppKit.NSEdgeInsets(0, 20, 0, 0))
        return box, [box, indent]

    data_title, data_detail = uninstall.data_option(ctx)
    models_title, models_detail, models_enabled = uninstall.models_option(ctx)
    data_box, data_views = option(data_title, data_detail, True)
    models_box, models_views = option(models_title, models_detail, False, models_enabled)
    stack = AppKit.NSStackView.stackViewWithViews_([*data_views, *models_views])
    stack.setOrientation_(1)
    stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
    stack.setSpacing_(6)
    stack.setFrameSize_(AppKit.NSMakeSize(ALERT_WIDTH, stack.fittingSize().height))
    alert.setAccessoryView_(stack)
    return alert, data_box, models_box


def confirm(ctx: Context) -> Choices | None:  # pragma: no cover - modal dialog
    """Ask. Returns the choices, or None when the user cancels."""
    import AppKit

    alert, data_box, models_box = build_alert(ctx)
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    if alert.runModal() != AppKit.NSAlertSecondButtonReturn:
        return None
    return Choices(
        delete_data=data_box.state() == 1,
        delete_models=models_box.isEnabled() and models_box.state() == 1,
    )


def show_message(title: str, text: str) -> None:  # pragma: no cover - modal dialog
    import AppKit

    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(text)
    alert.addButtonWithTitle_("OK")
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    alert.runModal()


def terminate() -> None:  # pragma: no cover - ends the process
    import AppKit

    AppKit.NSApp.terminate_(None)  # a clean exit, status 0


class UninstallController:
    """Runs on the main thread: checks, asks, then works on a background thread."""

    def __init__(
        self,
        switcher: ModelSwitcher | None,
        is_busy: Callable[[], bool],
        stop_everything: Callable[[], None],
        context_factory: Callable[[], Context] = current_context,
        ops_factory: Callable[[Context], uninstall.Operations] = uninstall.SystemOperations,
        ask: Callable[[Context], Choices | None] = confirm,
        notify: Callable[[str, str], None] = show_message,
        quit_app: Callable[[], None] = terminate,
        run_async: bool = True,
    ):
        self.switcher = switcher
        self.is_busy = is_busy
        self.stop_everything = stop_everything
        self.context_factory = context_factory
        self.ops_factory = ops_factory
        self.ask = ask
        self.notify = notify
        self.quit_app = quit_app
        self.run_async = run_async
        self.running = False  # True from the confirmation on; blocks restart
        self.results: list[uninstall.StepResult] = []

    def available(self) -> bool:
        return uninstall.available(self.context_factory())

    def start(self) -> None:
        ctx = self.context_factory()
        if not uninstall.available(ctx) or self.running:
            return
        if self.is_busy():
            self.notify("Not now", "A dictation is running. Try again when it has finished.")
            return
        if self.switcher is not None and (self.switcher.state.busy or not self.switcher.ready):
            self.notify("Not now", "murmur is still loading a model. Try again in a moment.")
            return
        choices = self.ask(ctx)
        if choices is None:
            return  # Cancel does nothing
        self.running = True
        self.stop_everything()  # no listener, no recording, nothing that could relaunch us
        plan = uninstall.build_plan(ctx, choices)
        ops = self.ops_factory(ctx)
        if self.run_async:
            threading.Thread(target=self._work, args=(plan, ops, ctx), daemon=True).start()
        else:
            self._work(plan, ops, ctx)

    def _work(self, plan, ops, ctx) -> None:
        self.results = uninstall.execute(plan, ops, ctx)
        main_thread(self._finish) if self.run_async else self._finish()

    def _finish(self) -> None:
        failed = uninstall.trash_failed(self.results)
        if failed is not None:
            self.notify(
                "Drag murmur to the Trash",
                f"murmur could not move itself to the Trash ({failed.message}). Everything "
                f"else is done. Drag it there yourself:\n{failed.step.path}",
            )
        self.quit_app()
