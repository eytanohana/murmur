"""Pure logic for running as murmur.app: paths, messages, lock, first run, relaunch."""

import logging
import shlex
import sys
import wave

import numpy as np
import pytest

from murmur import __main__ as cli
from murmur import frozen, permissions, selftest, settings
from murmur.agent import LABEL
from murmur.config import Config, load_config
from murmur.instance import InstanceLock

# ---- frozen detection and the bundle path ----------------------------------------------


def test_bundle_path_from_the_executable_inside_an_app(tmp_path):
    exe = tmp_path / "murmur.app" / "Contents" / "MacOS" / "murmur"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert frozen.bundle_path(str(exe)) == (tmp_path / "murmur.app").resolve()


@pytest.mark.parametrize("path", ["/usr/bin/python3", "/x/MacOS/murmur", "/x/foo/Contents/MacOS/m"])
def test_no_bundle_outside_an_app(path):
    assert frozen.bundle_path(path) is None


def test_is_frozen_follows_the_pyinstaller_flag(monkeypatch):
    assert frozen.is_frozen() is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert frozen.is_frozen() is True


# ---- permission messages name the app, not a python binary ------------------------------------


def test_permission_target_names_the_app_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(permissions, "is_frozen", lambda: True)
    monkeypatch.setattr(permissions, "bundle_path", lambda: tmp_path / "murmur.app")
    assert permissions.permission_target() == f"murmur.app ({tmp_path / 'murmur.app'})"


def test_permission_target_names_the_python_when_not_frozen():
    target = permissions.permission_target()
    assert "murmur.app" not in target and "python" in target.lower()


def test_grant_hint_for_the_app_and_for_python():
    app = permissions.grant_hint("Accessibility", "murmur.app (/Applications/murmur.app)")
    assert app.startswith("Turn on murmur.app (/Applications/murmur.app) under System Settings")
    assert "Accessibility" in app and "terminal" not in app
    dev = permissions.grant_hint("Microphone", "/opt/py/bin/python3.12")
    assert "Add /opt/py/bin/python3.12 (or the terminal app" in dev


def test_startup_warnings_name_the_app_when_frozen(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(permissions, "is_frozen", lambda: True)
    monkeypatch.setattr(permissions, "bundle_path", lambda: tmp_path / "murmur.app")
    monkeypatch.setattr(permissions, "accessibility_trusted", lambda prompt=False: False)
    monkeypatch.setattr(permissions, "check_microphone", lambda: None)
    import Quartz

    monkeypatch.setattr(Quartz, "CGPreflightListenEventAccess", lambda: False)
    monkeypatch.setattr(Quartz, "CGRequestListenEventAccess", lambda: None)
    with caplog.at_level(logging.WARNING, logger="murmur"):
        permissions.check_permissions()
    assert caplog.text.count("murmur.app (") == 2  # Accessibility and Input Monitoring
    assert "python" not in caplog.text.lower()


# ---- single instance ----------------------------------------------------------------------


def test_second_lock_on_the_same_file_is_refused_until_the_first_releases(tmp_path):
    path = tmp_path / "sub" / "murmur.lock"
    first, second = InstanceLock(path), InstanceLock(path)
    assert first.acquire() is True
    assert second.acquire() is False
    assert second.holder_pid() == first.holder_pid() and first.holder_pid() > 0
    first.release()
    assert second.acquire() is True
    second.release()


def test_a_second_copy_exits_without_loading_anything(tmp_path, monkeypatch, caplog):
    held = InstanceLock(tmp_path / "murmur.lock")
    assert held.acquire()
    monkeypatch.setattr(cli, "Transcriber", lambda cfg: pytest.fail("a second copy must not start"))
    with caplog.at_level(logging.WARNING, logger="murmur"):
        cli.run_dictation(Config(), tmp_path / "c.toml", lock_path=tmp_path / "murmur.lock")
    assert "already running" in caplog.text
    held.release()


# ---- first run ---------------------------------------------------------------------------------


def test_first_run_config_turns_the_icon_on_and_loads(tmp_path):
    path = tmp_path / "murmur" / "config.toml"
    assert settings.write_first_run_config(path) is True
    text = path.read_text()
    assert text.startswith("# murmur config") and "menubar_icon = true" in text
    assert chr(0x2014) not in text
    cfg = load_config(path)
    assert cfg.menubar_icon is True and cfg.stream is True  # only the icon differs


def test_first_run_config_never_overwrites(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("menubar_icon = false\n")
    assert settings.write_first_run_config(path) is False
    assert path.read_text() == "menubar_icon = false\n"


def test_main_writes_the_first_run_config_only_for_the_frozen_app(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "run_dictation", lambda cfg, path: seen.append(cfg))
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    cli.main([])  # a checkout run: no file is created
    assert not (tmp_path / "config.toml").exists()
    monkeypatch.setattr(cli, "is_frozen", lambda: True)
    cli.main([])
    assert seen[-1].menubar_icon is True and (tmp_path / "config.toml").exists()
    assert (tmp_path / "Library" / "Logs").is_dir()  # the app logs to ~/Library/Logs


def test_an_explicit_config_flag_is_never_replaced_by_the_first_run_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_dictation", lambda cfg, path: None)
    monkeypatch.setattr(cli, "is_frozen", lambda: True)
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    mine = tmp_path / "mine.toml"
    cli.main(["--config", str(mine)])
    assert not mine.exists()


def test_setup_logging_adds_a_file_handler_for_the_app(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    root.handlers.clear()
    try:
        cli.setup_logging(False, tmp_path / "Library" / "Logs" / "murmur.log")
        logging.getLogger("murmur").warning("hello log file")
        for h in root.handlers:
            h.flush()
        assert "hello log file" in (tmp_path / "Library" / "Logs" / "murmur.log").read_text()
    finally:
        root.handlers[:] = before


# ---- restart inside the bundle ----------------------------------------------------------------


def test_the_bundle_relaunches_after_this_process_has_exited(tmp_path):
    bundle = tmp_path / "my apps" / "murmur.app"
    how, cmd = settings.restart_plan({}, "/py", ["python"], bundle=bundle, pid=4242)
    assert how == "relaunch" and cmd[:2] == ["/bin/sh", "-c"]
    script = cmd[2]
    assert "kill -0 4242" in script  # waits for the old copy
    assert script.index("kill -0 4242") < script.index("/usr/bin/open -n")  # then opens
    assert shlex.quote(str(bundle)) in script  # spaces survive


def test_the_bundle_wins_over_launchd_and_exec():
    how, _ = settings.restart_plan({"XPC_SERVICE_NAME": LABEL}, "/py", ["python"], bundle=sys)
    assert how == "relaunch"


# ---- the self-check ----------------------------------------------------------------------------


def test_selftest_is_requested_only_with_the_variable():
    assert selftest.requested({}) is None
    assert selftest.requested({selftest.ENV_WAV: ""}) is None
    assert selftest.requested({selftest.ENV_WAV: "/x.wav"}) == "/x.wav"


def test_read_wav_gives_float_samples(tmp_path):
    path = tmp_path / "t.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.array([0, 16384, -16384], dtype=np.int16).tobytes())
    assert list(selftest.read_wav(str(path))) == [0.0, 0.5, -0.5]
