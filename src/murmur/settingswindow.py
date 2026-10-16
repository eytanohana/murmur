"""The native settings window (AppKit). Layout and wiring only; the rules live in
settings_schema.py and settings_view.py. Every control writes through LiveSettings, so a
change validates, lands in the config file and applies at once."""

from __future__ import annotations

import logging
from collections.abc import Callable

from . import models
from .config import Config
from .mainthread import main_thread
from .modelswitch import ModelSwitcher
from .settings import LiveSettings
from .settings_schema import (
    CHOICE,
    GROUPS,
    MODEL,
    NUMBER,
    SOUND,
    TOGGLE,
    Setting,
)
from .settings_view import (
    changed_from_defaults,
    format_value,
    model_options,
    model_status_text,
    row_state,
    settings_in,
    snap,
    sound_choices,
)

log = logging.getLogger("murmur")

AppKit_PRIORITY_DEFAULT_HIGH = 750  # NSLayoutPriorityDefaultHigh
LABEL_WIDTH = 170
RESET_WIDTH = 64
MARGIN = 24
TAB_SPACING = 20
ROW_SPACING = 4
STEPPER_MAX_RANGE = 10  # integer settings with a small range get a stepper, not a slider


def _make_classes():
    """Objective-C helper classes, created once AppKit is available."""
    import AppKit

    class Flipped(AppKit.NSView):
        def isFlipped(self):
            return True

    class Target(AppKit.NSObject):
        def act_(self, sender):
            self.handlers[sender.tag()](sender)

    class WindowDelegate(AppKit.NSObject):
        def windowDidBecomeKey_(self, note):
            self.on_focus()

        def windowWillClose_(self, note):
            self.on_close()

    return Flipped, Target, WindowDelegate


class Row:
    """One setting: label, control, help text, restart badge and reset button."""

    def __init__(self, setting: Setting):
        self.setting = setting
        self.view = None
        self.control = None
        self.help = None
        self.badge = None
        self.reset = None
        self.value_label = None
        self.extra = None  # model tab: (progress bar, status label)
        self.options: list[tuple[object, str]] = []


class SettingsWindow:
    def __init__(self, live: LiveSettings, switcher: ModelSwitcher, on_restart: Callable[[], None]):
        self.live = live
        self.switcher = switcher
        self.on_restart = on_restart
        self.window = None
        self.rows: dict[str, Row] = {}
        self._handlers: dict[int, Callable] = {}
        self._sound = None
        self._previous_app = None  # the app that was frontmost when the window opened
        switcher.listeners.append(lambda state: main_thread(self.refresh))

    # ---- public (main thread) ------------------------------------------------------------

    def show(self) -> None:
        import AppKit

        if self.window is None:
            self._build()
        self._remember_frontmost()
        self.live.refresh()
        self.refresh()
        AppKit.NSApp.activateIgnoringOtherApps_(
            True
        )  # an accessory app must activate to take input
        self.window.makeKeyAndOrderFront_(None)

    def refresh(self) -> None:
        """Show the live values, restart flags, reset buttons and model status."""
        if self.window is None:
            return
        cfg, pending = self.live.cfg, set(self.live.pending)
        state = self.switcher.state
        for row in self.rows.values():
            self._update_row(row, cfg, pending, state)
        self.restart_button.setHidden_(not pending)
        self.status.setStringValue_(
            "Restart needed for: " + ", ".join(sorted(pending))
            if pending
            else "Changes apply immediately."
        )
        self.reset_all.setEnabled_(bool(changed_from_defaults(cfg)))

    # ---- building ------------------------------------------------------------------------

    def _handler(self, control, fn: Callable) -> None:
        tag = len(self._handlers) + 1
        self._handlers[tag] = fn
        control.setTag_(tag)
        control.setTarget_(self.target)
        control.setAction_("act:")

    def _label(self, text, size=13, bold=False, secondary=False, wrap=False):
        import AppKit

        label = (
            AppKit.NSTextField.wrappingLabelWithString_(text)
            if wrap
            else AppKit.NSTextField.labelWithString_(text)
        )
        font = AppKit.NSFont.boldSystemFontOfSize_ if bold else AppKit.NSFont.systemFontOfSize_
        label.setFont_(font(size))
        if secondary:
            label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        return label

    def _stack(self, views, vertical=False, spacing=8, top=False):
        import AppKit

        stack = AppKit.NSStackView.stackViewWithViews_(views)
        stack.setOrientation_(1 if vertical else 0)
        stack.setSpacing_(spacing)
        if vertical:
            stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        elif top:
            stack.setAlignment_(AppKit.NSLayoutAttributeTop)
        else:
            stack.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        return stack

    def _width(self, view, width, flexible=True):
        """Fix a control's width. Flexible widths give way before the window clips a row."""
        c = view.widthAnchor().constraintEqualToConstant_(width)
        if flexible:
            c.setPriority_(AppKit_PRIORITY_DEFAULT_HIGH)
        c.setActive_(True)

    def _build(self) -> None:
        import AppKit

        self.Flipped, Target, WindowDelegate = _make_classes()
        self.target = Target.alloc().init()
        self.target.handlers = self._handlers

        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 720, 600), style, AppKit.NSBackingStoreBuffered, False
        )
        window.setTitle_("murmur Settings")
        window.setReleasedWhenClosed_(False)  # closing hides it; show() reuses the same window
        window.setMinSize_(AppKit.NSMakeSize(620, 460))
        window.center()
        window.setFrameAutosaveName_("murmurSettings")
        self.delegate = WindowDelegate.alloc().init()
        self.delegate.on_focus = self._on_focus
        self.delegate.on_close = self._on_close
        window.setDelegate_(self.delegate)

        tabs = AppKit.NSTabView.alloc().init()
        tabs.setTranslatesAutoresizingMaskIntoConstraints_(False)
        tabs.setContentCompressionResistancePriority_forOrientation_(
            250, 0
        )  # never widens the window
        for group in GROUPS:
            item = AppKit.NSTabViewItem.alloc().initWithIdentifier_(group)
            item.setLabel_(group)
            item.setView_(self._tab(group))
            tabs.addTabViewItem_(item)

        self.status = self._label("", 12, secondary=True)
        self.restart_button = AppKit.NSButton.buttonWithTitle_target_action_(
            "Restart now", None, None
        )
        self._handler(self.restart_button, lambda s: self.on_restart())
        self.reset_all = AppKit.NSButton.buttonWithTitle_target_action_("Reset all...", None, None)
        self._handler(self.reset_all, lambda s: self._confirm_reset_all())
        spacer = AppKit.NSView.alloc().init()
        spacer.setTranslatesAutoresizingMaskIntoConstraints_(False)
        spacer.setContentHuggingPriority_forOrientation_(1, 0)
        bar = self._stack([self.status, spacer, self.restart_button, self.reset_all], spacing=12)

        content = window.contentView()
        root = self._stack([tabs, bar], vertical=True, spacing=12)
        root.setAlignment_(AppKit.NSLayoutAttributeLeading)
        for view in (tabs, bar):  # explicit: the tab view must not size itself from its content
            view.widthAnchor().constraintEqualToAnchor_(root.widthAnchor()).setActive_(True)
        content.addSubview_(root)
        for attr in ("leading", "trailing", "top", "bottom"):
            pad = {"leading": 16, "trailing": -16, "top": 14, "bottom": -14}[attr]
            getattr(root, f"{attr}Anchor")().constraintEqualToAnchor_constant_(
                getattr(content, f"{attr}Anchor")(), pad
            ).setActive_(True)
        self.window = window
        self.tabs = tabs

    def _tab(self, group: str):
        import AppKit

        rows = []
        for setting in settings_in(group):
            row = self._row(setting)
            self.rows[setting.key] = row
            rows.append(row.view)
        stack = self._stack(rows, vertical=True, spacing=TAB_SPACING)
        stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        doc = self.Flipped.alloc().init()
        doc.setTranslatesAutoresizingMaskIntoConstraints_(False)
        doc.addSubview_(stack)
        pad = {"leading": MARGIN, "trailing": -MARGIN, "top": MARGIN, "bottom": -MARGIN}
        for attr, constant in pad.items():
            getattr(stack, f"{attr}Anchor")().constraintEqualToAnchor_constant_(
                getattr(doc, f"{attr}Anchor")(), constant
            ).setActive_(True)
        for row in rows:  # every row spans the tab so help text wraps at the window width
            row.widthAnchor().constraintEqualToAnchor_(stack.widthAnchor()).setActive_(True)
        scroll = AppKit.NSScrollView.alloc().init()
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setDocumentView_(doc)
        doc.widthAnchor().constraintEqualToAnchor_(scroll.contentView().widthAnchor()).setActive_(
            True
        )
        return scroll

    def _row(self, setting: Setting) -> Row:
        import AppKit

        row = Row(setting)
        name = self._label(setting.label, 13, bold=True)
        self._width(name, LABEL_WIDTH, flexible=False)
        control = self._control(row)
        row.help = self._label(setting.help, 11, secondary=True, wrap=True)
        row.badge = self._label("Restart needed", 11)
        row.badge.setTextColor_(AppKit.NSColor.systemOrangeColor())
        restart = AppKit.NSButton.buttonWithTitle_target_action_("Restart now", None, None)
        restart.setControlSize_(AppKit.NSControlSizeSmall)
        restart.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self._handler(restart, lambda s: self.on_restart())
        row.badge_line = self._stack([row.badge, restart], spacing=8)
        parts = [control]
        if row.extra:
            parts.extend(row.extra)
        parts += [row.help, row.badge_line]
        column = self._stack(parts, vertical=True, spacing=ROW_SPACING)
        row.reset = AppKit.NSButton.buttonWithTitle_target_action_("Reset", None, None)
        row.reset.setBezelStyle_(AppKit.NSBezelStyleInline)
        row.reset.setControlSize_(AppKit.NSControlSizeSmall)
        row.reset.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self._width(row.reset, RESET_WIDTH, flexible=False)
        default = getattr(Config(), setting.key)
        self._handler(row.reset, lambda s, k=setting.key, d=default: self._commit(row, k, d))
        row.view = self._stack([name, column, row.reset], spacing=14, top=True)
        row.view.setDetachesHiddenViews_(False)
        row.view.setDistribution_(
            AppKit.NSStackViewDistributionFill
        )  # the control column stretches
        column.setContentHuggingPriority_forOrientation_(1, 0)
        return row

    # ---- controls ------------------------------------------------------------------------

    def _control(self, row: Row):
        import AppKit

        s = row.setting
        if s.kind == TOGGLE:
            ctl = AppKit.NSSwitch.alloc().init()
            self._handler(ctl, lambda sender: self._commit(row, s.key, sender.state() == 1))
        elif s.kind == NUMBER:
            ctl = self._number_control(row)
        elif s.kind in (CHOICE, SOUND, MODEL):
            ctl = self._popup_control(row)
        else:  # TEXT
            ctl = AppKit.NSTextField.alloc().init()
            ctl.cell().setSendsActionOnEndEditing_(True)
            ctl.setTranslatesAutoresizingMaskIntoConstraints_(False)
            self._width(ctl, 120)
            self._handler(
                ctl, lambda sender: self._commit(row, s.key, sender.stringValue().strip())
            )
        ctl.setTranslatesAutoresizingMaskIntoConstraints_(False)
        row.control = ctl
        if s.kind == MODEL:
            bar = AppKit.NSProgressIndicator.alloc().init()
            bar.setStyle_(AppKit.NSProgressIndicatorStyleBar)
            bar.setMinValue_(0)
            bar.setMaxValue_(1)
            bar.setTranslatesAutoresizingMaskIntoConstraints_(False)
            self._width(bar, 320)
            row.extra = (bar, self._label("", 11, secondary=True, wrap=True))
        if s.kind == SOUND:
            play = AppKit.NSButton.buttonWithTitle_target_action_("Play", None, None)
            self._handler(play, lambda sender: self._play(row))
            row.play = play
            return self._stack([ctl, play], spacing=8)
        if s.kind == NUMBER:
            return self._stack([ctl, row.value_label], spacing=10)
        return ctl

    def _number_control(self, row: Row):
        import AppKit

        s = row.setting
        small = s.integer and (s.maximum - s.minimum) <= STEPPER_MAX_RANGE
        row.value_label = self._label("", 12)
        row.value_label.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(12, 0))
        self._width(row.value_label, 90)
        if small:
            ctl = AppKit.NSStepper.alloc().init()
            ctl.setMinValue_(s.minimum)
            ctl.setMaxValue_(s.maximum)
            ctl.setIncrement_(s.step)
            ctl.setValueWraps_(False)
            self._handler(
                ctl, lambda sender: self._commit(row, s.key, snap(s, sender.doubleValue()))
            )
        else:
            ctl = AppKit.NSSlider.alloc().init()
            ctl.setMinValue_(s.minimum)
            ctl.setMaxValue_(s.maximum)
            ctl.setContinuous_(True)
            self._width(ctl, 260)
            self._handler(ctl, lambda sender: self._slide(row, sender))
        return ctl

    def _slide(self, row: Row, slider) -> None:
        """Update the label while dragging; write to the config when the mouse is released."""
        import AppKit

        s = row.setting
        value = snap(s, slider.doubleValue())
        row.value_label.setStringValue_(format_value(s, value))
        event = AppKit.NSApp.currentEvent()
        if event is not None and event.type() == AppKit.NSEventTypeLeftMouseDragged:
            return
        self._commit(row, s.key, value)

    def _popup_control(self, row: Row):
        import AppKit

        popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            AppKit.NSMakeRect(0, 0, 220, 26), False
        )
        self._width(popup, 280 if row.setting.kind == MODEL else 200)

        def chosen(sender):
            index = sender.indexOfSelectedItem()
            if 0 <= index < len(row.options):
                self._commit(row, row.setting.key, row.options[index][0])

        self._handler(popup, chosen)
        return popup

    def _play(self, row: Row) -> None:
        import AppKit

        path = getattr(self.live.cfg, row.setting.key)
        self._sound = AppKit.NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
        if self._sound is not None:
            self._sound.play()

    # ---- state -> controls -----------------------------------------------------------------

    def _update_row(self, row: Row, cfg: Config, pending, state) -> None:
        s = row.setting
        value = getattr(cfg, s.key)
        rs = row_state(s, cfg, pending)
        ctl = row.control
        if s.kind == TOGGLE:
            ctl.setState_(1 if value else 0)
        elif s.kind == NUMBER:
            ctl.setDoubleValue_(float(value))
            row.value_label.setStringValue_(format_value(s, value))
        elif s.kind == CHOICE:
            row.options = list(s.choices)
            self._fill_popup(ctl, row.options, value)
        elif s.kind == SOUND:
            row.options = sound_choices(value)
            self._fill_popup(ctl, row.options, value)
            row.play.setEnabled_(rs.enabled)
        elif s.kind == MODEL:
            row.options = model_options(value, models.is_cached)
            shown = state.target if state.busy and state.target else value
            self._fill_popup(ctl, row.options, shown)
            bar, label = row.extra
            label.setStringValue_(model_status_text(cfg, state))
            bar.setHidden_(not state.busy)
            if state.busy:
                determinate = state.progress is not None
                bar.setIndeterminate_(not determinate)
                if determinate:
                    bar.stopAnimation_(None)
                    bar.setDoubleValue_(state.progress)
                else:
                    bar.startAnimation_(None)
        else:
            if ctl.currentEditor() is None:
                ctl.setStringValue_(str(value))
        ctl.setEnabled_(rs.enabled)
        help_text = s.help + (f"\n{rs.note}" if rs.note else "")
        row.help.setStringValue_(help_text)
        row.badge_line.setHidden_(not rs.restart_needed)
        row.reset.setEnabled_(not rs.is_default)
        row.reset.setHidden_(
            rs.is_default
        )  # detachesHiddenViews is off: the column keeps its place

    def _fill_popup(self, popup, options, current) -> None:
        popup.removeAllItems()
        popup.addItemsWithTitles_([label for _, label in options])
        for i, (value, _) in enumerate(options):
            if value == current:
                popup.selectItemAtIndex_(i)
                break

    # ---- actions -------------------------------------------------------------------------------

    def _commit(self, row: Row, key: str, value) -> None:
        try:
            self.live.set_value(key, value)
        except (ValueError, OSError) as exc:
            log.error("Could not save %s = %r to %s: %s", key, value, self.live.path, exc)
            row.help.setStringValue_(f"Not saved: {exc}")
            import AppKit

            row.help.setTextColor_(AppKit.NSColor.systemRedColor())
            main_thread(self._restore_help_color, row)
            self._reselect(row)
            return
        self.refresh()

    def _restore_help_color(self, row: Row) -> None:
        import AppKit

        row.help.setTextColor_(AppKit.NSColor.secondaryLabelColor())

    def _reselect(self, row: Row) -> None:
        self._update_row(row, self.live.cfg, set(self.live.pending), self.switcher.state)

    def _confirm_reset_all(self) -> None:
        import AppKit

        changes = changed_from_defaults(self.live.cfg)
        if not changes:
            return
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Reset all settings to their defaults?")
        alert.setInformativeText_(
            f"{len(changes)} setting{'s' if len(changes) != 1 else ''} will go back to "
            "the default value. The model switches back to the default model, which may "
            "download it. Settings that need a restart wait for one."
        )
        alert.addButtonWithTitle_("Reset all")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
            return
        for key, default in changes:
            try:
                self.live.set_value(key, default)
            except (ValueError, OSError) as exc:
                log.error("Could not reset %s: %s", key, exc)
        self.refresh()

    def _on_focus(self) -> None:
        """The window regained focus: pick up edits made to the file meanwhile."""
        self.live.refresh()
        self.refresh()

    def _remember_frontmost(self) -> None:
        import os

        import AppKit

        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None and front.processIdentifier() != os.getpid():
            self._previous_app = front

    def _on_close(self) -> None:
        """Hand focus back to the app the user was typing in; an accessory app with no
        window would otherwise stay frontmost and swallow keystrokes."""
        import AppKit

        previous, self._previous_app = self._previous_app, None
        if previous is not None and not previous.isTerminated():
            main_thread(
                previous.activateWithOptions_, AppKit.NSApplicationActivateIgnoringOtherApps
            )
        else:
            main_thread(AppKit.NSApp.deactivate)
