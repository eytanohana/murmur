"""Noticing a permission grant while the app runs. Fakes only: no real TCC state is read."""

import logging

import pytest

from murmur import permissions as p
from murmur.hotkey import HotkeyListener

A, I, M = p.ACCESSIBILITY, p.INPUT_MONITORING, p.MICROPHONE
ALL_OK = {A: True, I: True, M: True}


# ---- the flip logic ---------------------------------------------------------------------------------


def test_only_a_missing_to_granted_change_is_a_flip():
    before = {A: False, I: False, M: True}
    after = {A: True, I: False, M: True}
    assert p.newly_granted(before, after) == {A}
    assert p.newly_granted(after, after) == set()  # unchanged
    assert p.newly_granted({A: True}, {A: False}) == set()  # a revoke is not a grant
    assert p.newly_granted({}, {A: True}) == set()  # never seen missing: not a flip


def test_missing():
    assert p.missing({A: True, I: False, M: False}) == {I, M}
    assert p.missing(ALL_OK) == set()


@pytest.mark.parametrize(
    ("flipped", "allowed", "expected"),
    [
        ({I}, True, ["restart"]),
        ({A}, True, ["restart"]),
        ({A, I}, True, ["restart"]),
        ({I}, False, ["recreate_listener"]),  # a relaunch just happened: no loop
        ({M}, True, ["recheck_microphone"]),  # the microphone never needs a restart
        ({M}, False, ["recheck_microphone"]),
        ({I, M}, True, ["restart", "recheck_microphone"]),
        (set(), True, []),
    ],
)
def test_decide(flipped, allowed, expected):
    assert p.decide(flipped, allowed) == expected


# ---- the watcher ------------------------------------------------------------------------------------


class Reads:
    """A scripted `read`: each call returns the next state; counts the calls."""

    def __init__(self, *states):
        self.states, self.calls = list(states), 0

    def __call__(self):
        self.calls += 1
        return dict(self.states[min(self.calls - 1, len(self.states) - 1)])


def test_nothing_is_polled_when_everything_was_granted_at_startup():
    reads, granted = Reads(ALL_OK), []
    watcher = p.PermissionWatcher(reads, ALL_OK, granted.append, sleep=lambda s: None)
    assert watcher.pending is False
    watcher.start()  # no thread
    watcher.run()  # returns at once
    assert reads.calls == 0 and granted == []


def test_a_grant_is_reported_once_and_the_loop_stops_when_all_are_granted():
    initial = {A: False, I: True, M: True}
    reads = Reads(initial, initial, {A: True, I: True, M: True}, ALL_OK, ALL_OK)
    granted, slept = [], []
    watcher = p.PermissionWatcher(reads, initial, granted.append, interval=2.0, sleep=slept.append)
    watcher.run()
    assert granted == [{A}]  # exactly one report
    assert reads.calls == 3  # it stopped polling once nothing was missing
    assert slept == [2.0, 2.0, 2.0]  # a cheap poll every two seconds


def test_the_first_poll_with_nothing_changed_triggers_nothing():
    initial = {A: False, I: False}
    reads, granted = Reads(initial), []
    watcher = p.PermissionWatcher(reads, initial, granted.append, sleep=lambda s: None)
    assert watcher.poll() == set() and watcher.poll() == set()
    assert granted == []  # never a restart merely because something is still missing


def test_stop_ends_the_loop_without_polling():
    initial = {A: False}
    reads = Reads(initial)
    watcher = p.PermissionWatcher(reads, initial, lambda f: None, sleep=lambda s: None)
    watcher.stop()
    watcher.run()
    assert reads.calls == 0


def test_a_failing_probe_is_logged_and_the_loop_goes_on(caplog):
    initial = {A: False}
    calls = []

    def read():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("probe failed")
        return {A: True}

    granted = []
    watcher = p.PermissionWatcher(read, initial, granted.append, sleep=lambda s: None)
    with caplog.at_level(logging.ERROR, logger="murmur"):
        watcher.run()
    assert "Permission check failed" in caplog.text and granted == [{A}]


# ---- the actions and the no-loop guard ----------------------------------------------------------------------


class Rig:
    def __init__(self, tmp_path, now=1000.0, busy=()):
        self.events = []
        self.now = now
        self.busy = list(busy)  # successive answers of is_busy
        self.guard = p.RestartGuard(tmp_path / "permission-restart", clock=lambda: self.now)
        self.actions = p.PermissionActions(
            restart=lambda: self.events.append("restart"),
            is_busy=lambda: self.busy.pop(0) if self.busy else False,
            recreate_listener=lambda: self.events.append("recreate"),
            recheck_microphone=lambda: self.events.append("recheck") or True,
            guard=self.guard,
            sleep=lambda s: self.events.append("wait"),
        )


def test_a_grant_after_startup_relaunches_once(tmp_path):
    rig = Rig(tmp_path)
    rig.actions({I})
    assert rig.events == ["restart"]
    assert rig.guard.path.exists()  # the relaunch is remembered


def test_a_second_grant_in_the_same_process_never_relaunches_again(tmp_path):
    rig = Rig(tmp_path)
    rig.actions({I})
    rig.now += 500  # even after the guard window has passed: this process already asked once
    rig.actions({A})
    assert rig.events == ["restart", "recreate"]


def test_the_new_process_after_a_relaunch_recreates_instead_of_looping(tmp_path):
    first = Rig(tmp_path, now=1000.0)
    first.actions({I})  # the old process relaunched
    second = Rig(tmp_path, now=1010.0)  # the new process: it sees a flip 10 s later
    second.actions({A})
    assert second.events == ["recreate"]


def test_the_guard_expires_so_a_much_later_grant_can_relaunch(tmp_path):
    Rig(tmp_path, now=1000.0).actions({I})
    later = Rig(tmp_path, now=1000.0 + p.RESTART_WINDOW_S + 1)
    later.actions({I})
    assert later.events == ["restart"]


def test_the_relaunch_waits_for_a_running_dictation(tmp_path):
    rig = Rig(tmp_path, busy=[True, True, False])
    rig.actions({I})
    assert rig.events == ["wait", "wait", "restart"]


def test_a_microphone_grant_rechecks_with_a_fresh_stream_and_never_restarts(tmp_path, caplog):
    rig = Rig(tmp_path)
    with caplog.at_level(logging.INFO, logger="murmur"):
        rig.actions({M})
    assert rig.events == ["recheck"] and not rig.guard.path.exists()
    assert "no restart needed" in caplog.text


def test_a_microphone_that_is_still_silent_is_reported_honestly(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.actions.recheck_microphone = lambda: False
    with caplog.at_level(logging.INFO, logger="murmur"):
        rig.actions({M})
    assert "still returned silence" in caplog.text


def test_the_guard_ignores_a_damaged_file(tmp_path):
    path = tmp_path / "permission-restart"
    path.write_text("not a number")
    assert p.RestartGuard(path).allows() is True


# ---- end to end with fakes: grant after startup, and the no-restart cases -----------------------------------------------


def test_input_monitoring_granted_after_startup_leads_to_one_relaunch(tmp_path):
    initial = {A: True, I: False, M: True}
    reads = Reads(initial, {A: True, I: True, M: True})
    rig = Rig(tmp_path)
    watcher = p.PermissionWatcher(reads, initial, rig.actions, sleep=lambda s: None)
    watcher.run()
    assert rig.events == ["restart"]


def test_everything_granted_at_startup_never_relaunches(tmp_path):
    rig = Rig(tmp_path)
    watcher = p.PermissionWatcher(Reads(ALL_OK), ALL_OK, rig.actions, sleep=lambda s: None)
    watcher.start()
    watcher.run()
    assert rig.events == [] and not rig.guard.path.exists()


def test_only_the_microphone_missing_at_startup_ends_with_a_recheck_not_a_relaunch(tmp_path):
    initial = {A: True, I: True, M: False}
    rig = Rig(tmp_path)
    watcher = p.PermissionWatcher(
        Reads(initial, ALL_OK), initial, rig.actions, sleep=lambda s: None
    )
    watcher.run()
    assert rig.events == ["recheck"]


# ---- the pieces around it ---------------------------------------------------------------------------------------------


def test_the_startup_warnings_no_longer_tell_the_user_to_restart(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(p, "is_frozen", lambda: True)
    monkeypatch.setattr(p, "bundle_path", lambda: tmp_path / "murmur.app")
    monkeypatch.setattr(p, "accessibility_trusted", lambda prompt=False: False)
    monkeypatch.setattr(p, "check_microphone", lambda: False)
    import Quartz

    monkeypatch.setattr(Quartz, "CGPreflightListenEventAccess", lambda: False)
    monkeypatch.setattr(Quartz, "CGRequestListenEventAccess", lambda: None)
    with caplog.at_level(logging.WARNING, logger="murmur"):
        p.check_permissions()
    assert "then restart" not in caplog.text
    assert caplog.text.count("restarts by itself once you grant it") == 2


def test_the_microphone_warnings_say_it_is_rechecked_by_itself(monkeypatch, caplog):
    import numpy as np

    class Stream:
        def __init__(self, **kw):
            self.cb = kw["callback"]

        def __enter__(self):
            self.cb(np.zeros((160, 1), dtype=np.float32), 160, None, None)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(p.sd, "InputStream", Stream)
    monkeypatch.setattr(p.time, "sleep", lambda s: None)
    with caplog.at_level(logging.WARNING, logger="murmur"):
        assert p.check_microphone() is False
    assert "checks again by itself" in caplog.text and "and restart" not in caplog.text


def test_a_fresh_mic_check_resets_portaudio_first(monkeypatch):
    order = []
    monkeypatch.setattr(p.sd, "_terminate", lambda: order.append("terminate"))
    monkeypatch.setattr(p.sd, "_initialize", lambda: order.append("initialize"))
    monkeypatch.setattr(p, "check_microphone", lambda: order.append("check") or True)
    assert p.recheck_microphone() is True and order == ["terminate", "initialize", "check"]


def test_a_portaudio_reset_failure_still_checks(monkeypatch):
    monkeypatch.setattr(p.sd, "_terminate", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(p, "check_microphone", lambda: True)
    assert p.recheck_microphone() is True


def test_the_microphone_is_left_unwatched_when_its_status_cannot_be_read(monkeypatch):
    import objc

    monkeypatch.setattr(objc, "loadBundle", lambda *a, **k: (_ for _ in ()).throw(ImportError("x")))
    assert p.microphone_authorized() is None


def test_recreating_the_listener_replaces_the_pynput_listener(monkeypatch):
    from murmur import hotkey

    made = []

    class FakeListener:
        def __init__(self, **kw):
            self.started = self.stopped = False
            made.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(hotkey.keyboard, "Listener", FakeListener)
    h = HotkeyListener(lambda: None, lambda: None, lambda: None)
    h._listener = old = FakeListener()
    h._held.add("alt")
    h.recreate()
    assert old.stopped and made[-1].started and h._listener is made[-1] and h._held == set()


# ---- polling must not disturb a recording --------------------------------------------------------------------


def test_the_watcher_does_not_poll_while_a_dictation_is_running():
    initial = {A: False}
    reads = Reads({A: False}, {A: True})
    import itertools

    busy = itertools.chain([True, True], itertools.repeat(False))
    watcher = p.PermissionWatcher(
        reads, initial, lambda f: None, sleep=lambda s: None, paused=lambda: next(busy)
    )
    watcher.run()
    assert reads.calls == 2  # two skipped cycles cost no probe at all; the next two polled


def test_the_microphone_status_class_is_loaded_once(monkeypatch):
    import objc

    loads = []
    monkeypatch.setattr(p, "_capture_device", None)

    class Device:
        @staticmethod
        def authorizationStatusForMediaType_(kind):
            return 3

    monkeypatch.setattr(objc, "loadBundle", lambda *a, **k: loads.append(1))
    monkeypatch.setattr(objc, "lookUpClass", lambda name: Device)
    assert p.microphone_authorized() is True and p.microphone_authorized() is True
    assert loads == [1]  # loading the bundle on every poll cost recorded audio


def test_a_microphone_that_worked_at_startup_is_not_watched():
    state = p.startup_state(lambda: {A: True, I: False, M: False}, mic_heard=True)
    assert state == {A: True, I: False, M: True} and p.missing(state) == {I}
    assert p.startup_state(lambda: {A: True, I: True, M: False}, mic_heard=False)[M] is False
    assert p.missing(p.startup_state(lambda: ALL_OK, mic_heard=False)) == set()


def test_check_permissions_reports_whether_the_microphone_gave_audio(monkeypatch):
    import Quartz

    monkeypatch.setattr(p, "accessibility_trusted", lambda prompt=False: True)
    monkeypatch.setattr(Quartz, "CGPreflightListenEventAccess", lambda: True)
    monkeypatch.setattr(p, "check_microphone", lambda: True)
    assert p.check_permissions() is True
    monkeypatch.setattr(p, "check_microphone", lambda: False)
    assert p.check_permissions() is False
