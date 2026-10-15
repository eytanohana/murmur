import plistlib
import subprocess
from pathlib import Path

from murmur import agent


class FakeRunner:
    def __init__(self, returncode=0, stdout=""):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, cmd):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "")


def test_plist_contents(tmp_path):
    python = Path("/proj/.venv/bin/python")
    data = agent.build_plist(python, Path("/proj"), tmp_path)
    assert data["Label"] == "com.user.murmur"
    assert data["ProgramArguments"] == ["/proj/.venv/bin/python", "-m", "murmur"]
    assert "uv" not in data["ProgramArguments"]
    assert data["WorkingDirectory"] == "/proj"
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}  # restart on crash only
    assert data["ProcessType"] == "Interactive"
    log = str(tmp_path / "Library" / "Logs" / "murmur.log")
    assert data["StandardOutPath"] == data["StandardErrorPath"] == log
    assert data["EnvironmentVariables"]["PATH"].startswith("/proj/.venv/bin:")
    plistlib.dumps(data)  # serializes


def test_install_writes_valid_plist_and_bootstraps(tmp_path):
    runner, lines = FakeRunner(), []
    assert agent.install(tmp_path, runner, out=lines.append) == 0
    path = agent.plist_path(tmp_path)
    loaded = plistlib.loads(path.read_bytes())
    assert loaded["Label"] == agent.LABEL
    cmds = [c[:2] for c in runner.calls]
    assert ["plutil", "-lint"] in cmds
    assert ["launchctl", "bootout"] in cmds
    boot = next(c for c in runner.calls if c[:2] == ["launchctl", "bootstrap"])
    assert boot[2].startswith("gui/") and boot[3] == str(path)
    assert not any("load" == c[1] for c in runner.calls)  # deprecated verbs unused
    assert any("Accessibility" in line and "Input Monitoring" in line for line in lines)


def test_install_dry_run_writes_nothing_and_runs_nothing(tmp_path):
    runner, lines = FakeRunner(), []
    assert agent.install(tmp_path, runner, dry_run=True, out=lines.append) == 0
    assert runner.calls == []
    assert not agent.plist_path(tmp_path).exists()
    assert any("com.user.murmur" in line for line in lines)


def test_install_no_start_skips_launchctl(tmp_path):
    runner = FakeRunner()
    agent.install(tmp_path, runner, no_start=True, out=lambda s: None)
    assert [c[0] for c in runner.calls] == ["plutil"]
    assert agent.plist_path(tmp_path).exists()


def test_uninstall_boots_out_and_removes_plist(tmp_path):
    agent.install(tmp_path, FakeRunner(), no_start=True, out=lambda s: None)
    runner = FakeRunner()
    agent.uninstall(tmp_path, runner, out=lambda s: None)
    assert runner.calls[0][:2] == ["launchctl", "bootout"]
    assert not agent.plist_path(tmp_path).exists()


def test_status_reports_state_and_not_loaded(tmp_path):
    out = []
    runner = FakeRunner(stdout="foo\n\tstate = running\n\tpid = 123\n\tlast exit code = 0\n")
    assert agent.status(tmp_path, runner, out=out.append) == 0
    assert any("state = running" in line for line in out)
    out.clear()
    assert agent.status(tmp_path, FakeRunner(returncode=113), out=out.append) == 1
    assert any("not loaded" in line for line in out)


def test_permission_text_names_resolved_python(tmp_path):
    link = tmp_path / "python"
    target = tmp_path / "real-python"
    target.write_text("")
    link.symlink_to(target)
    text = agent.permission_instructions(link)
    assert str(target.resolve()) in text
    assert "Accessibility" in text and "Microphone" in text


def test_install_fails_when_plutil_rejects_the_plist(tmp_path):
    runner, lines = FakeRunner(returncode=1, stdout="bad plist"), []
    assert agent.install(tmp_path, runner, out=lines.append) == 1
    assert [c[0] for c in runner.calls] == ["plutil"]  # launchctl is never reached
    assert any("plutil -lint failed" in line for line in lines)


def test_install_reports_a_failed_bootstrap(tmp_path):
    class BootstrapFails(FakeRunner):
        def __call__(self, cmd):
            result = super().__call__(cmd)
            if cmd[:2] == ["launchctl", "bootstrap"]:
                result.returncode = 5
            return result

    lines = []
    assert agent.install(tmp_path, BootstrapFails(), out=lines.append) == 1
    assert any("launchctl bootstrap failed" in line for line in lines)


def test_install_needs_the_project_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "project_dir", lambda: tmp_path)
    lines = []
    assert agent.install(tmp_path / "home", FakeRunner(), out=lines.append) == 1
    assert any("uv sync" in line for line in lines)


def test_uninstall_when_not_loaded_still_removes_the_plist(tmp_path):
    agent.install(tmp_path, FakeRunner(), no_start=True, out=lambda s: None)
    lines = []
    agent.uninstall(tmp_path, FakeRunner(returncode=113), out=lines.append)
    assert any("was not loaded" in line for line in lines)
    assert not agent.plist_path(tmp_path).exists()


def test_uninstall_dry_run_touches_nothing(tmp_path):
    agent.install(tmp_path, FakeRunner(), no_start=True, out=lambda s: None)
    runner = FakeRunner()
    agent.uninstall(tmp_path, runner, dry_run=True, out=lambda s: None)
    assert runner.calls == [] and agent.plist_path(tmp_path).exists()


def test_main_agent_routes_commands(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(agent, "install", lambda home, **kw: seen.append(("install", kw)) or 0)
    monkeypatch.setattr(agent, "uninstall", lambda home, **kw: seen.append(("uninstall", kw)) or 0)
    monkeypatch.setattr(agent, "status", lambda home: seen.append(("status", {})) or 0)

    class Args:
        dry_run, no_start = True, False

    agent.main_agent("install-agent", Args, tmp_path)
    agent.main_agent("uninstall-agent", Args, tmp_path)
    agent.main_agent("agent-status", Args, tmp_path)
    assert seen == [
        ("install", {"dry_run": True, "no_start": False}),
        ("uninstall", {"dry_run": True}),
        ("status", {}),
    ]
