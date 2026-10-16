"""Command line wiring: flags, subcommands and which indicator is used."""

import pytest

from murmur import __main__ as cli


@pytest.fixture
def calls(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli, "run_dictation", lambda cfg, path, first_launch=False: seen.update(run=cfg, path=path)
    )
    monkeypatch.setattr(cli, "main_agent", lambda command, args: seen.update(agent=(command, args)))
    return seen


def config_file(tmp_path, text=""):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return str(p)


def test_default_run_uses_the_configured_indicator(calls, tmp_path):
    cli.main(["--config", config_file(tmp_path, 'indicator = "none"\n')])
    assert calls["run"].indicator == "none"
    assert calls["path"] == tmp_path / "c.toml"


def test_agent_subcommands_exit_with_their_status(calls, monkeypatch):
    monkeypatch.setattr(cli, "main_agent", lambda command, args: 7)
    with pytest.raises(SystemExit) as exc:
        cli.main(["agent-status"])
    assert exc.value.code == 7


def test_install_agent_flags_reach_the_agent_module(calls):
    with pytest.raises(SystemExit):
        cli.main(["install-agent", "--dry-run", "--no-start"])
    command, args = calls["agent"]
    assert command == "install-agent" and args.dry_run and args.no_start


def test_uninstall_agent_accepts_dry_run(calls):
    with pytest.raises(SystemExit):
        cli.main(["uninstall-agent", "--dry-run"])
    assert calls["agent"][1].dry_run


def test_invalid_config_is_reported_as_an_error(calls, tmp_path):
    with pytest.raises(ValueError, match="insert_mode"):
        cli.main(["--config", config_file(tmp_path, 'insert_mode = "x"\n')])


def test_without_a_config_flag_the_default_config_path_is_used(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "default.toml")
    cli.main([])
    assert calls["path"] == tmp_path / "default.toml"
