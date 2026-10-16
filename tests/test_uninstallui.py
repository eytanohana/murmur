"""The uninstall controller (fakes only) and the confirmation alert's wording."""

from pathlib import Path

import pytest

from murmur import settings, uninstall, uninstallui
from murmur.config import Config
from murmur.models import CachedModel
from murmur.modelswitch import ModelSwitcher, SwitchState
from murmur.uninstall import Choices, Context

BUNDLE = Path("/Applications/murmur.app")


def make_ctx(**kw):
    base = {
        "frozen": True,
        "bundle": BUNDLE,
        "config_dir": Path("/Users/x/.config/murmur"),
        "log_file": Path("/Users/x/Library/Logs/murmur.log"),
        "dev_agent_installed": False,
        "models": (
            CachedModel("a/small", "small.en", Path("/Users/x/hub/models--a--small"), 481_000_000),
        ),
    }
    base.update(kw)
    return Context(**base)


class Spy:
    def __init__(self):
        self.events = []


class FakeOps:
    def __init__(self, spy, fail=()):
        self.spy, self.fail = spy, set(fail)

    def _do(self, name, *a):
        self.spy.events.append(name)
        if name in self.fail:
            raise OSError(f"{name} failed")
        return "ok"

    def login_off(self):
        return self._do("login_off")

    def remove_dev_agent(self):
        return self._do("dev_agent")

    def delete_path(self, p):
        return self._do("delete")

    def reset_permissions(self):
        return self._do("tcc")

    def trash(self, p):
        return self._do("trash")


DEFAULT = Choices()


def controller(ctx=None, answer=DEFAULT, busy=False, sw=None, fail=()):
    spy = Spy()
    messages = []
    c = uninstallui.UninstallController(
        sw,
        is_busy=lambda: busy,
        stop_everything=lambda: spy.events.append("stop"),
        context_factory=lambda: ctx or make_ctx(),
        ops_factory=lambda ctx: FakeOps(spy, fail),
        ask=lambda ctx: (spy.events.append("ask"), answer)[1],
        notify=lambda t, m: messages.append((t, m)),
        quit_app=lambda: spy.events.append("quit"),
        run_async=False,
    )
    return c, spy, messages


def test_cancel_does_nothing_at_all():
    c, spy, messages = controller(answer=None)
    c.start()
    assert spy.events == ["ask"] and messages == [] and c.running is False


def test_confirming_stops_dictation_then_runs_the_steps_in_order_and_quits():
    c, spy, _ = controller(answer=Choices(delete_data=True, delete_models=False))
    c.start()
    assert spy.events == ["ask", "stop", "login_off", "delete", "delete", "tcc", "trash", "quit"]
    assert c.running is True and all(r.ok for r in c.results)


def test_nothing_is_offered_outside_the_app():
    c, spy, messages = controller(ctx=make_ctx(frozen=False))
    assert c.available() is False
    c.start()
    assert spy.events == [] and messages == []  # not even the question is asked


def test_a_running_dictation_blocks_it_with_a_message():
    c, spy, messages = controller(busy=True)
    c.start()
    assert (
        spy.events == []
        and messages[0][0] == "Not now"
        and "dictation is running" in messages[0][1]
    )


@pytest.mark.parametrize(
    "state", [SwitchState("loading", "x/y", 0.4), SwitchState("waiting", "x/y")]
)
def test_a_model_load_blocks_it_with_a_message(state):
    sw = ModelSwitcher(Config(), None, None, lambda: False, lambda *a: None)
    sw.state = state
    c, spy, messages = controller(sw=sw)
    c.start()
    assert spy.events == [] and "loading a model" in messages[0][1]


def test_the_first_model_still_downloading_blocks_it_too():
    sw = ModelSwitcher(Config(), None, None, lambda: False, lambda *a: None, ready=False)
    c, spy, messages = controller(sw=sw)
    c.start()
    assert spy.events == [] and messages


def test_a_second_start_while_uninstalling_is_ignored():
    c, spy, _ = controller()
    c.start()
    before = list(spy.events)
    c.start()
    assert spy.events == before


def test_a_failed_trash_tells_the_user_to_drag_the_app_and_still_quits():
    c, spy, messages = controller(fail={"trash"})
    c.start()
    assert messages[0][0] == "Drag murmur to the Trash" and str(BUNDLE) in messages[0][1]
    assert spy.events[-1] == "quit"


def test_other_failed_steps_do_not_stop_the_trash_or_the_quit():
    c, spy, messages = controller(fail={"login_off", "tcc"})
    c.start()
    assert "trash" in spy.events and spy.events[-1] == "quit" and messages == []


# ---- the menu item ------------------------------------------------------------------------------------------


def test_the_menu_item_sits_between_restart_and_quit_and_only_when_asked():
    titles = [i.title for i in settings.build_menu(Config(), uninstall=True)]
    assert titles[-3:] == ["Restart murmur", "Uninstall murmur...", "Quit murmur"]
    assert "Uninstall murmur..." not in [i.title for i in settings.build_menu(Config())]
    item = next(
        i for i in settings.build_menu(Config(), uninstall=True) if i.title.startswith("Uninstall")
    )
    assert item.action == "uninstall"


def test_clicking_the_menu_item_calls_the_controller(tmp_path):
    from murmur.settings import LiveSettings, MenuItem
    from murmur.statusitem import StatusItem

    called = []
    item = StatusItem(
        LiveSettings(tmp_path / "c.toml", Config()),
        lambda: None,
        on_uninstall=lambda: called.append(1),
    )
    item._by_tag[1] = MenuItem("action", action="uninstall")
    item._on_click(1)
    assert called == [1]
    bare = StatusItem(LiveSettings(tmp_path / "c.toml", Config()), lambda: None)
    bare._by_tag[1] = MenuItem("action", action="uninstall")
    bare._on_click(1)  # no controller: must not raise


# ---- the real alert ------------------------------------------------------------------------------------------


def test_the_alert_has_the_warning_wording_the_default_cancel_and_the_two_checkboxes():
    import AppKit

    alert, data_box, models_box = uninstallui.build_alert(make_ctx(dev_agent_installed=True))
    assert alert.messageText() == "Uninstall murmur?"
    assert alert.alertStyle() == AppKit.NSAlertStyleWarning
    text = alert.informativeText()
    assert "launch at login" in text and "com.user.murmur" in text and "tccutil reset All" in text
    assert str(BUNDLE) in text and chr(0x2014) not in text
    buttons = alert.buttons()
    assert [b.title() for b in buttons] == ["Cancel", "Uninstall"]
    assert (
        buttons[0].keyEquivalent() == "\r" and buttons[1].keyEquivalent() == ""
    )  # Cancel is the default
    assert buttons[1].hasDestructiveAction()
    assert data_box.title() == "Also delete my settings and log" and data_box.state() == 1
    assert (
        models_box.title() == "Also delete downloaded models (481 MB)" and models_box.state() == 0
    )
    assert models_box.isEnabled()


def test_the_models_checkbox_is_disabled_when_none_are_downloaded():
    _, _, models_box = uninstallui.build_alert(make_ctx(models=()))
    assert models_box.isEnabled() is False and models_box.state() == 0


def test_the_context_comes_from_the_apps_own_constants(monkeypatch, tmp_path):
    monkeypatch.setattr(uninstallui, "is_frozen", lambda: True)
    monkeypatch.setattr(uninstallui, "bundle_path", lambda: BUNDLE)
    monkeypatch.setattr(uninstallui.models, "curated_cache", list)
    monkeypatch.setattr(uninstallui.launchlogin, "agent_installed", lambda: True)
    ctx = uninstallui.current_context()
    assert ctx.config_dir == uninstallui.CONFIG_PATH.parent and ctx.bundle == BUNDLE
    assert ctx.log_file.name == "murmur.log" and ctx.dev_agent_installed is True
    assert uninstall.available(ctx)
    monkeypatch.setattr(uninstallui, "is_frozen", lambda: False)
    assert uninstallui.current_context().bundle is None
