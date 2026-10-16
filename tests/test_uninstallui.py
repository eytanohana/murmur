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


class FakeProgress:
    def __init__(self, spy):
        self.spy = spy

    def show(self, title, detail=""):
        self.spy.events.append(f"progress:{title}")

    def update(self, detail):
        self.spy.events.append(f"detail:{detail}")

    def close(self):
        self.spy.events.append("progress closed")


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
        begin_ui=lambda: spy.events.append("begin_ui"),
        progress_factory=lambda: FakeProgress(spy),
    )
    return c, spy, messages


def test_cancel_does_nothing_at_all():
    c, spy, messages = controller(answer=None)
    c.start()
    assert spy.events == ["ask"] and messages == [] and c.running is False


def test_confirming_stops_dictation_then_runs_the_steps_in_order_and_quits():
    c, spy, _ = controller(answer=Choices(delete_data=True, delete_models=False))
    c.start()
    work = [e for e in spy.events if not e.startswith(("detail:", "progress", "begin_ui"))]
    assert work == ["ask", "stop", "login_off", "delete", "delete", "tcc", "trash", "quit"]
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


# ---- the confirmation is answered at once ---------------------------------------------------------------------


def test_the_window_goes_away_and_the_panel_shows_before_any_step_runs():
    c, spy, _ = controller()
    c.start()
    e = spy.events
    assert e.index("begin_ui") < e.index("progress:Uninstalling murmur...") < e.index("login_off")
    assert e.index("ask") < e.index("begin_ui")  # only after the confirmation
    assert e.index("progress closed") < e.index("quit")  # nothing is left on screen at quit


def test_the_panel_names_each_step_as_it_starts():
    c, spy, _ = controller(answer=Choices(delete_data=False, delete_models=False))
    c.start()
    details = [e for e in spy.events if e.startswith("detail:")]
    assert details[0] == "detail:Turn off launch at login"
    assert details[-1].startswith("detail:Move ")


def test_cancel_leaves_the_window_and_the_icon_alone():
    c, spy, _ = controller(answer=None)
    c.start()
    assert "begin_ui" not in spy.events and not any(e.startswith("progress") for e in spy.events)


def test_the_panel_is_closed_before_the_drag_it_yourself_message():
    c, spy, messages = controller(fail={"trash"})
    c.start()
    assert spy.events.index("progress closed") < spy.events.index("quit") and messages


# ---- the timing log -------------------------------------------------------------------------------------------------


def test_each_step_is_timed_into_a_log_that_is_kept(tmp_path):
    log_file = tmp_path / "murmur-uninstall.log"
    ctx = make_ctx(uninstall_log=log_file)
    c, _spy, _ = controller(ctx=ctx, answer=Choices(delete_data=False, delete_models=False))
    c.start()
    text = log_file.read_text()
    assert "uninstall started (3 steps)" in text and "stop listener and recorder" in text
    for label in ("Turn off launch at login", "Reset murmur's privacy permissions", "to the Trash"):
        assert label in text
    assert text.count(" in ") >= 3 and "all steps finished" in text
    assert log_file.exists()  # kept when the user did not ask to delete the log


def test_the_uninstall_log_is_deleted_last_when_the_user_deletes_the_log(tmp_path):
    log_file = tmp_path / "murmur-uninstall.log"
    ctx = make_ctx(uninstall_log=log_file)
    c, spy, _ = controller(ctx=ctx, answer=Choices(delete_data=True, delete_models=False))
    deleted = []
    c.ops_factory = lambda cx: type(
        "O",
        (FakeOps,),
        {"delete_path": lambda self, p: deleted.append(p) or "ok"},
    )(spy)
    c.start()
    assert deleted[-1] == log_file  # after the timings were written, and the last delete
    assert "all steps finished" in log_file.read_text()


def test_a_log_that_cannot_be_written_does_not_stop_the_uninstall(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    c, spy, _ = controller(ctx=make_ctx(uninstall_log=blocked / "murmur-uninstall.log"))
    c.start()
    assert spy.events[-1] == "quit" and all(r.ok for r in c.results)


def test_stopping_the_listener_failing_does_not_stop_the_uninstall(tmp_path):
    c, spy, _ = controller(ctx=make_ctx(uninstall_log=tmp_path / "u.log"))

    def boom():
        raise RuntimeError("pynput stuck")

    c.stop_everything = boom
    c.start()
    assert spy.events[-1] == "quit" and "FAILED (pynput stuck)" in (tmp_path / "u.log").read_text()


def test_the_alert_says_where_timings_go_and_when_that_file_is_deleted():
    ctx = make_ctx(uninstall_log=Path("/Users/x/Library/Logs/murmur-uninstall.log"))
    assert "murmur-uninstall.log" in uninstall.summary_text(ctx)
    assert "kept unless you tick the first box" in uninstall.summary_text(ctx)
    assert "uninstall log" in uninstall.data_option(ctx)[1]
    assert "uninstall log" not in uninstall.data_option(make_ctx())[1]
    assert ctx.uninstall_log in uninstall.allowed_delete_paths(ctx)


def test_the_context_names_a_log_next_to_the_normal_one(monkeypatch):
    monkeypatch.setattr(uninstallui, "is_frozen", lambda: True)
    monkeypatch.setattr(uninstallui, "bundle_path", lambda: BUNDLE)
    monkeypatch.setattr(uninstallui.models, "curated_cache", list)
    ctx = uninstallui.current_context()
    assert ctx.uninstall_log.name == "murmur-uninstall.log"
    assert ctx.uninstall_log.parent == ctx.log_file.parent


# ---- steps cannot hold each other up ----------------------------------------------------------------------------------


def test_a_hung_step_times_out_and_the_next_steps_still_run():
    import threading

    gate = threading.Event()

    class Hanging(FakeOps):
        def login_off(self):
            gate.wait(5)  # a system call that never returns
            return "late"

    spy = Spy()
    ctx = make_ctx()
    plan = uninstall.build_plan(ctx, Choices(delete_data=False, delete_models=False))
    started = []
    results = uninstall.execute(
        plan, Hanging(spy), ctx, step_timeout=0.1, on_start=lambda s: started.append(s.kind)
    )
    gate.set()
    assert results[0].ok is False and "timed out" in results[0].message
    assert [r.ok for r in results[1:]] == [True, True]  # tcc and trash were not held up
    assert started == [s.kind for s in plan]


def test_each_result_carries_its_duration():
    ticks = iter([0.0, 1.5, 10.0, 10.25, 20.0, 23.0])
    ctx = make_ctx()
    plan = uninstall.build_plan(ctx, Choices(delete_data=False, delete_models=False))
    results = uninstall.execute(
        plan, FakeOps(Spy()), ctx, clock=lambda: next(ticks), step_timeout=None
    )
    assert [r.seconds for r in results] == [1.5, 0.25, 3.0]


# ---- the host closes Settings and hides the icon -----------------------------------------------------------------------


def test_begin_ui_closes_settings_hides_the_icon_and_idles_the_pill(tmp_path):
    from murmur.config import Config
    from murmur.host import make_indicator
    from murmur.settings import LiveSettings

    live = LiveSettings(tmp_path / "c.toml", Config())  # indicator "overlay": a pill exists
    sw = ModelSwitcher(live.cfg, None, None, lambda: False, lambda *a: None)
    host = make_indicator(live, switcher=sw)
    calls = []
    host.window.close_window = lambda: calls.append("close settings")
    host.parts[-1].set_enabled = lambda enabled: calls.append(f"icon enabled={enabled}")
    pill = host.parts[0]
    pill.set = lambda state: calls.append(f"pill {state}")
    host.parts[-1].set = lambda state: calls.append(f"icon state {state}")
    host.uninstall.begin_ui()
    assert calls[0] == "close settings" and "icon enabled=False" in calls and "pill idle" in calls


def test_the_launch_login_service_result_is_read_for_both_pyobjc_return_shapes():
    from murmur.launchlogin import MainAppService

    MainAppService._check(True)
    MainAppService._check((True, None))
    with pytest.raises(RuntimeError, match="refused"):
        MainAppService._check(False)
    with pytest.raises(RuntimeError, match="denied"):
        MainAppService._check((False, "denied"))
