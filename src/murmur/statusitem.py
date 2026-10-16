"""Native menu bar icon (NSStatusItem) with a small settings menu.

A part of the Host. The icon follows the live menubar_icon setting: turning it on creates
the item, turning it off removes it, and if the user takes it out of the menu bar the
setting is switched off to match (KVO on `visible`). The pure parts (menu rows, config writing, icon names) live in
settings.py; this module only turns them into AppKit objects. Setting changes are
written to the config file and applied at once (RESTART keys wait for a restart).
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable

from . import models
from .frozen import is_frozen
from .launchlogin import agent_installed
from .mainthread import main_thread
from .modelswitch import ModelSwitcher
from .settings import (
    ICON_DESCRIPTIONS,
    LiveSettings,
    MenuItem,
    build_menu,
    ensure_config_file,
    hidden_by_user,
    icon_for,
    icon_plan,
)
from .settingswindow import SettingsWindow

log = logging.getLogger("murmur")

CHECKED = 1  # NSControlStateValueOn
AUTOSAVE_NAME = "murmur"  # lets macOS remember where the icon sits and that it was removed


class StatusItem:
    def __init__(
        self,
        live: LiveSettings,
        on_restart: Callable[[], None],
        switcher: ModelSwitcher | None = None,
        window: SettingsWindow | None = None,
    ):
        self.live = live
        self.on_restart = on_restart
        self.switcher = switcher
        self.window = window
        self.item = None
        self.menu = None
        self.target = None
        self._state = "idle"
        self._removing = False  # True while we remove the item ourselves
        self._by_tag: dict[int, MenuItem] = {}

    # main thread only

    def build(self) -> None:
        """Create the menu and, when the setting is on, the status item."""
        import AppKit

        class MenuTarget(AppKit.NSObject):
            def onItem_(self, sender):
                self.handler(sender.tag())

            def menuWillOpen_(self, menu):  # NSMenuDelegate: show current values
                self.on_open()

            def observeValueForKeyPath_ofObject_change_context_(self, key, obj, change, ctx):
                # KVO on NSStatusItem.visible: the user took the icon out of the menu bar
                self.on_visible(bool(change[AppKit.NSKeyValueChangeNewKey]))

        self.target = MenuTarget.alloc().init()
        self.target.handler = self._on_click
        self.target.on_open = self._on_open
        self.target.on_visible = self._on_visible_changed
        self.menu = AppKit.NSMenu.alloc().initWithTitle_("murmur")
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self.target)
        self._rebuild_menu()
        self.set_enabled(self.live.cfg.menubar_icon)

    def set_enabled(self, enabled: bool) -> None:
        """Make the status item match menubar_icon: create, show or remove it."""
        item = self.item
        plan = icon_plan(enabled, item is not None, item.isVisible() if item is not None else None)
        if plan == "create":
            self._create_item()
        elif plan == "show":
            item.setVisible_(True)
        elif plan == "remove":
            self._remove_item()

    def _create_item(self) -> None:
        import AppKit

        bar = AppKit.NSStatusBar.systemStatusBar()
        item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        item.setAutosaveName_(AUTOSAVE_NAME)  # macOS remembers a Cmd-drag removal under this name
        item.setBehavior_(AppKit.NSStatusItemBehaviorRemovalAllowed)
        item.setVisible_(True)  # the config is the truth: undo a remembered removal once
        item.setMenu_(self.menu)
        item.addObserver_forKeyPath_options_context_(
            self.target, "visible", AppKit.NSKeyValueObservingOptionNew, None
        )
        self.item = item
        self._apply(self._state)
        log.info("Menu bar icon shown")

    def _remove_item(self) -> None:
        import AppKit

        item, self.item = self.item, None
        if item is None:
            return
        self._removing = True
        try:
            item.removeObserver_forKeyPath_(self.target, "visible")
            AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(item)
        finally:
            self._removing = False
        log.info("Menu bar icon removed")

    def _on_visible_changed(self, visible: bool) -> None:
        """The user hid the icon: write menubar_icon = false so the setting shows the truth."""
        if not hidden_by_user(self.live.cfg.menubar_icon, visible, self._removing):
            return
        log.info("The menu bar icon was removed from the menu bar; setting menubar_icon = false")
        try:
            self.live.set_value("menubar_icon", False)  # also removes the item via the hook
        except (ValueError, OSError) as exc:
            log.error("Could not save menubar_icon = false: %s", exc)
            return
        if self.window is not None:
            self.window.refresh()

    def _apply(self, state: str) -> None:
        import AppKit

        self._state = state
        if self.item is None:
            return
        description = ICON_DESCRIPTIONS.get(state, ICON_DESCRIPTIONS["idle"])
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            icon_for(state), description
        )
        button = self.item.button()
        if image is None:  # no SF Symbols: fall back to text rather than an invisible item
            button.setTitle_("mic")
            return
        image.setTemplate_(True)  # adapts to light and dark menu bars
        button.setImage_(image)
        button.setToolTip_(description)

    def _on_open(self) -> None:
        """The menu is about to open: re-read a changed config file, then redraw."""
        self.live.refresh()
        self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        self._by_tag.clear()
        self.menu.removeAllItems()
        state = self.switcher.state if self.switcher else None
        cached = {m.repo for m in models.CATALOG if models.is_cached(m.repo)}
        items = build_menu(
            self.live.cfg,
            list(self.live.pending),
            self.live.error,
            state,
            cached,
            launch_login=is_frozen() and not agent_installed(),
        )
        self._fill(self.menu, items)

    def _fill(self, menu, items) -> None:
        import AppKit

        for it in items:
            if it.kind == "separator":
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            clickable = it.kind in ("setting", "action")
            mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                it.title, "onItem:" if clickable else None, ""
            )
            mi.setEnabled_(it.enabled)
            mi.setState_(CHECKED if it.checked else 0)
            if clickable:
                mi.setTarget_(self.target)
                tag = len(self._by_tag) + 1
                mi.setTag_(tag)
                self._by_tag[tag] = it
            if it.kind == "submenu":
                sub = AppKit.NSMenu.alloc().initWithTitle_(it.title)
                sub.setAutoenablesItems_(False)
                self._fill(sub, it.children)
                mi.setSubmenu_(sub)
            menu.addItem_(mi)

    def _on_click(self, tag: int) -> None:
        it = self._by_tag.get(tag)
        if it is None:
            return
        if it.kind == "setting":
            self._change(it.key, it.value)
        elif it.action == "open_config":
            ensure_config_file(self.live.path)
            subprocess.Popen(["open", "-t", str(self.live.path)])
        elif it.action == "settings":
            if self.window is not None:
                self.window.show()
        elif it.action == "restart":
            self.on_restart()
        elif it.action == "quit":
            import AppKit

            AppKit.NSApplication.sharedApplication().terminate_(None)

    def _change(self, key: str, value: object) -> None:
        try:
            self.live.set_value(key, value)
        except (ValueError, OSError) as exc:
            log.error("Could not save %s = %r to %s: %s", key, value, self.live.path, exc)
            return
        log.info("Saved %s = %r (applies on the next Option press)", key, value)
        self._rebuild_menu()

    # any thread

    def set(self, state: str) -> None:
        main_thread(self._apply, state)

    def enable(self, enabled: bool) -> None:
        """Settings hook for menubar_icon: from any thread, applied on the main thread."""
        main_thread(self.set_enabled, enabled)
