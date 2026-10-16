"""Getting back to Settings: reopen, second launch, first-launch welcome, icon visibility."""

import pytest

from murmur import reopen, settings
from murmur.config import Config
from murmur.host import Host
from murmur.settings import LiveSettings, hidden_by_user, icon_plan

# ---- decisions -----------------------------------------------------------------------------


def test_a_second_launch_opens_settings_unless_a_dictation_is_running():
    assert reopen.route_open_request(busy=False) is True
    assert reopen.route_open_request(busy=True) is False


@pytest.mark.parametrize(
    ("first", "selftest", "expected"),
    [(True, False, True), (False, False, False), (True, True, False), (False, True, False)],
)
def test_the_welcome_is_only_for_the_very_first_launch(first, selftest, expected):
    assert reopen.should_welcome(first, selftest) is expected


@pytest.mark.parametrize(
    ("enabled", "exists", "visible", "plan"),
    [
        (True, False, None, "create"),
        (True, True, True, "keep"),
        (True, True, False, "show"),  # config says on, but the item is hidden: show it once
        (False, True, True, "remove"),
        (False, True, False, "remove"),
        (False, False, None, "keep"),
    ],
)
def test_icon_plan(enabled, exists, visible, plan):
    assert icon_plan(enabled, exists, visible) == plan


@pytest.mark.parametrize(
    ("configured", "visible", "removing", "expected"),
    [
        (True, False, False, True),  # the user Cmd-dragged it out
        (True, True, False, False),
        (False, False, False, False),  # setting already off
        (True, False, True, False),  # we are removing it ourselves
    ],
)
def test_hidden_by_user(configured, visible, removing, expected):
    assert hidden_by_user(configured, visible, removing) is expected


# ---- the host routes both ways in -----------------------------------------------------------


class FakeWindow:
    def __init__(self):
        self.shown = 0

    def show(self):
        self.shown += 1


def test_the_reopen_event_shows_settings_and_skips_the_default_handling():
    window = FakeWindow()
    host = Host([], window)
    assert host.handle_reopen() is False
    assert window.shown == 1


def test_the_reopen_event_without_a_window_is_harmless():
    assert Host([], None).handle_reopen() is False


def test_a_notification_from_a_second_launch_opens_settings_when_idle():
    window = FakeWindow()
    host = Host([], window)
    host.handle_open_request()
    assert window.shown == 1


def test_a_notification_during_a_dictation_is_ignored(caplog):
    window = FakeWindow()
    host = Host([], window)
    host.is_busy = lambda: True
    with caplog.at_level("INFO", logger="murmur"):
        host.handle_open_request()
    assert window.shown == 0 and "a dictation is running" in caplog.text


def test_the_notification_is_scoped_to_one_lock_path():
    assert reopen.OPEN_SETTINGS_NOTIFICATION == "com.eytanohana.murmur.open-settings"
    assert "scope" in reopen.post_open_request.__code__.co_varnames  # the object limits who listens


# ---- the running copy and the second launch -------------------------------------------------------------


def test_a_second_launch_asks_the_first_to_open_settings(tmp_path, monkeypatch):
    from murmur import __main__ as cli
    from murmur.instance import InstanceLock

    held = InstanceLock(tmp_path / "murmur.lock")
    assert held.acquire()
    posted = []
    monkeypatch.setattr(cli.reopen, "post_open_request", lambda scope: posted.append(scope))
    cli.run_dictation(Config(), tmp_path / "c.toml", lock_path=tmp_path / "murmur.lock")
    assert posted == [str(tmp_path / "murmur.lock")]
    held.release()


# ---- the live toggle and the config staying in sync ------------------------------------------------------------


def test_menubar_icon_is_a_live_key_and_runs_its_hook(tmp_path):
    live = LiveSettings(tmp_path / "c.toml", Config(menubar_icon=True))
    calls = []
    live.key_hooks["menubar_icon"] = calls.append
    live.set_value("menubar_icon", False)
    live.set_value("menubar_icon", True)
    assert calls == [False, True] and live.pending == {}


def test_the_user_hiding_the_icon_writes_false_through_the_validated_path(tmp_path):
    from murmur.statusitem import StatusItem

    live = LiveSettings(tmp_path / "c.toml", Config(menubar_icon=True))
    removed = []
    live.key_hooks["menubar_icon"] = removed.append
    item = StatusItem(live, lambda: None)
    item._on_visible_changed(False)  # macOS reports visible = NO after a Cmd-drag
    assert live.cfg.menubar_icon is False and removed == [False]
    assert "menubar_icon = false" in (tmp_path / "c.toml").read_text()
    item._on_visible_changed(False)  # a repeat changes nothing
    assert removed == [False]


def test_showing_again_is_not_mistaken_for_a_removal(tmp_path):
    from murmur.statusitem import StatusItem

    live = LiveSettings(tmp_path / "c.toml", Config(menubar_icon=True))
    item = StatusItem(live, lambda: None)
    item._on_visible_changed(True)
    assert live.cfg.menubar_icon is True and not (tmp_path / "c.toml").exists()


def test_our_own_removal_does_not_write_the_config(tmp_path):
    from murmur.statusitem import StatusItem

    live = LiveSettings(tmp_path / "c.toml", Config(menubar_icon=True))
    item = StatusItem(live, lambda: None)
    item._removing = True
    item._on_visible_changed(False)
    assert live.cfg.menubar_icon is True


def test_the_schema_text_tells_the_user_how_to_get_back():
    from murmur.settings_schema import BY_KEY

    s = BY_KEY["menubar_icon"]
    assert s.label == "Show icon in the menu bar"
    assert s.help == "If you hide it, open murmur from Spotlight to get back here."
    assert s.live is True
    assert settings.FIRST_RUN_CONFIG.count("menubar_icon = true") == 1
