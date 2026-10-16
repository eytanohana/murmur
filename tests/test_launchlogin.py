"""Launch at login: SMAppService first, a LaunchAgent as fallback, never next to com.user.murmur."""

import plistlib

import pytest

from murmur import launchlogin as ll
from murmur import settings, settings_view
from murmur.agent import plist_path as dev_plist
from murmur.config import Config
from murmur.settings_schema import BY_KEY


class FakeService:
    def __init__(self, status=ll.ENABLED, fail_register=None):
        self.status_value, self.fail_register = status, fail_register
        self.calls = []

    def register(self):
        self.calls.append("register")
        if self.fail_register:
            raise self.fail_register

    def unregister(self):
        self.calls.append("unregister")

    def status(self):
        return self.status_value


@pytest.fixture
def bundle(tmp_path):
    return tmp_path / "Applications" / "murmur.app"


def test_enabling_uses_the_system_login_item_when_it_works(tmp_path, bundle):
    svc = FakeService()
    result = ll.set_launch_at_login(True, bundle, tmp_path, svc)
    assert (result.ok, result.method) == (True, "service") and svc.calls == ["register"]
    assert not ll.fallback_plist_path(tmp_path).exists()


def test_an_app_that_needs_approval_tells_the_user_where(tmp_path, bundle):
    result = ll.set_launch_at_login(True, bundle, tmp_path, FakeService(ll.REQUIRES_APPROVAL))
    assert result.ok and "Login Items" in result.message


@pytest.mark.parametrize(
    "service",
    [FakeService(fail_register=RuntimeError("Operation not permitted")), FakeService(ll.NOT_FOUND)],
)
def test_a_refused_registration_falls_back_to_a_launch_agent(tmp_path, bundle, service):
    result = ll.set_launch_at_login(True, bundle, tmp_path, service)
    assert (result.ok, result.method) == (True, "launchagent")
    plist = plistlib.loads(ll.fallback_plist_path(tmp_path).read_bytes())
    assert plist["Label"] == ll.LOGIN_LABEL and plist["RunAtLoad"] is True
    assert plist["ProgramArguments"] == ["/usr/bin/open", "-a", str(bundle)]


def test_a_missing_servicemanagement_also_falls_back(tmp_path, bundle, monkeypatch):
    def broken():
        raise ImportError("no ServiceManagement")

    monkeypatch.setattr(ll, "MainAppService", broken)
    result = ll.set_launch_at_login(True, bundle, tmp_path)
    assert result.method == "launchagent" and ll.fallback_plist_path(tmp_path).exists()


def test_disabling_unregisters_and_removes_the_fallback(tmp_path, bundle):
    ll.set_launch_at_login(True, bundle, tmp_path, FakeService(fail_register=RuntimeError("x")))
    assert ll.fallback_plist_path(tmp_path).exists()
    svc = FakeService()
    result = ll.set_launch_at_login(False, bundle, tmp_path, svc)
    assert result.ok and svc.calls == ["unregister"]
    assert not ll.fallback_plist_path(tmp_path).exists()


def test_the_developer_launch_agent_blocks_enabling_and_is_left_alone(tmp_path, bundle):
    dev = dev_plist(tmp_path)
    dev.parent.mkdir(parents=True)
    dev.write_text("<plist/>")
    svc = FakeService()
    result = ll.set_launch_at_login(True, bundle, tmp_path, svc)
    assert (result.ok, result.method) == (False, "conflict")
    assert "uninstall-agent" in result.message and svc.calls == []
    assert dev.read_text() == "<plist/>"  # never removed automatically
    assert ll.agent_installed(tmp_path) is True


def test_without_a_bundle_nothing_happens(tmp_path):
    result = ll.set_launch_at_login(True, None, tmp_path, FakeService())
    assert (result.ok, result.method) == (False, "unavailable")


# ---- wiring: setting, menu, window ------------------------------------------------------------


def test_launch_at_login_is_a_live_general_toggle_off_by_default():
    s = BY_KEY["launch_at_login"]
    assert (s.group, s.kind, s.live) == ("General", "toggle", True)
    assert Config().launch_at_login is False


def test_changing_the_setting_runs_the_hook_only_on_a_real_change(tmp_path):
    live = settings.LiveSettings(tmp_path / "c.toml", Config())
    seen = []
    live.key_hooks["launch_at_login"] = seen.append
    live.set_value("launch_at_login", True)
    live.set_value("launch_at_login", True)  # unchanged: no second call
    live.set_value("launch_at_login", False)
    assert seen == [True, False]


def test_the_menu_shows_the_toggle_only_for_the_app_build():
    def titles(**kw):
        return [i.title for i in settings.build_menu(Config(), **kw)]

    assert "Launch at login" not in titles()
    assert "Launch at login" in titles(launch_login=True)
    item = next(
        i
        for i in settings.build_menu(Config(launch_at_login=True), launch_login=True)
        if i.title == "Launch at login"
    )
    assert (item.key, item.value, item.checked) == ("launch_at_login", False, True)


def test_the_window_row_is_disabled_with_a_reason_outside_the_app_and_with_the_dev_agent():
    s = BY_KEY["launch_at_login"]
    cfg = Config()
    ok = settings_view.row_state(s, cfg, [], frozen=True, agent_installed=False)
    dev = settings_view.row_state(s, cfg, [], frozen=False)
    conflict = settings_view.row_state(s, cfg, [], frozen=True, agent_installed=True)
    assert ok.enabled is True and ok.note == ""
    assert dev.enabled is False and "murmur.app build" in dev.note
    assert conflict.enabled is False and "uninstall-agent" in conflict.note
