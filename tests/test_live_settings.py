"""LiveSettings: one stat per check, reload only on a new mtime, own writes not reloaded."""

import logging

import pytest

from murmur.config import Config
from murmur.settings import LiveSettings


class FakeStat:
    """An injectable stat: tests set the mtime by hand and count the calls."""

    def __init__(self, mtime=1):
        self.mtime = mtime
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        return self.mtime


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "config.toml"
    stat = FakeStat(1)
    live = LiveSettings(path, Config(), stat=stat)
    return live, stat, path


def edit(path, stat, text):
    path.write_text(text)
    stat.mtime += 1


def test_unchanged_mtime_costs_one_stat_and_no_parse(setup, monkeypatch):
    live, stat, _ = setup
    monkeypatch.setattr("murmur.settings.load_config", lambda p: pytest.fail("parsed"))
    before = stat.calls
    assert live.refresh() is False
    assert stat.calls == before + 1


def test_a_changed_file_applies_live_keys_and_flags_restart_keys(setup):
    live, stat, path = setup
    edit(
        path,
        stat,
        'sounds = true\nstream_interval = 0.8\nsample_rate = 16000\nindicator = "none"\nmenubar_icon = true\n',
    )
    assert live.refresh() is True
    assert (live.cfg.sounds, live.cfg.stream_interval) == (True, 0.8)
    assert live.cfg.indicator == "overlay" and live.cfg.menubar_icon is False  # not applied live
    assert live.pending == {"indicator": "none", "menubar_icon": True}
    assert live.error is None


def test_changing_a_restart_key_back_clears_the_pending_flag(setup):
    live, stat, path = setup
    edit(path, stat, 'indicator = "none"\n')
    live.refresh()
    edit(path, stat, 'indicator = "overlay"\n')
    live.refresh()
    assert live.pending == {}


def test_the_same_mtime_is_not_reloaded_twice(setup):
    live, stat, path = setup
    edit(path, stat, "sounds = true\n")
    assert live.refresh() is True
    path.write_text("sounds = false\n")  # content changed but the mtime did not
    assert live.refresh() is False and live.cfg.sounds is True


def test_invalid_file_keeps_the_old_config_and_warns_once_per_change(setup, caplog):
    live, stat, path = setup
    edit(path, stat, "sounds = true\n")
    live.refresh()
    with caplog.at_level(logging.WARNING, logger="murmur"):
        edit(path, stat, 'min_hold_ms = "slow"\n')
        live.refresh()
        live.refresh()
        live.refresh()
    assert live.cfg.sounds is True and live.cfg.min_hold_ms == 1000  # old config kept
    assert live.error and "min_hold_ms must be int" in live.error
    assert caplog.text.count("keeping the previous settings") == 1
    with caplog.at_level(logging.WARNING, logger="murmur"):
        edit(path, stat, "min_hold_ms = [\n")  # a different bad edit warns again
        live.refresh()
    assert caplog.text.count("keeping the previous settings") == 2


def test_fixing_the_file_clears_the_error_and_applies_it(setup):
    live, stat, path = setup
    edit(path, stat, "sounds = [\n")
    live.refresh()
    assert live.error
    edit(path, stat, "sounds = true\n")
    live.refresh()
    assert live.error is None and live.cfg.sounds is True


def test_own_write_updates_the_remembered_mtime_and_is_not_reloaded(setup, monkeypatch):
    live, stat, _ = setup
    stat.mtime = 5  # what the file system reports after our write
    live.set_value("sounds", True)
    assert live.cfg.sounds is True
    monkeypatch.setattr("murmur.settings.load_config", lambda p: pytest.fail("reloaded own write"))
    assert live.refresh() is False


def test_set_value_for_a_restart_key_is_pending_not_live(setup):
    live, _, _ = setup
    live.set_value("indicator", "none")
    assert live.cfg.indicator == "overlay" and live.pending == {"indicator": "none"}


def test_set_value_validates(setup):
    live, _, path = setup
    with pytest.raises(ValueError):
        live.set_value("sounds", "yes")
    assert not path.exists()


def test_a_deleted_file_falls_back_to_defaults(setup):
    live, stat, path = setup
    edit(path, stat, "sounds = true\n")
    live.refresh()
    path.unlink()
    stat.mtime = None
    live.refresh()
    assert live.cfg.sounds is False


# ---- model_repo goes through the switcher, not straight into the config ----------


def test_a_model_change_in_the_file_calls_the_hook_and_leaves_the_config_alone(setup):
    live, stat, path = setup
    calls = []
    live.model_hook = lambda repo, from_file: calls.append((repo, from_file))
    edit(path, stat, 'model_repo = "mlx-community/whisper-base.en-mlx"\n')
    live.refresh()
    assert calls == [("mlx-community/whisper-base.en-mlx", True)]
    assert live.cfg.model_repo == Config().model_repo  # unchanged until commit_model


def test_set_value_for_the_model_does_not_write_the_file(setup):
    live, _, path = setup
    calls = []
    live.model_hook = lambda repo, from_file: calls.append((repo, from_file))
    live.set_value("model_repo", "mlx-community/whisper-base.en-mlx")
    assert calls == [("mlx-community/whisper-base.en-mlx", False)]
    assert not path.exists()
    with pytest.raises(ValueError, match="must not be empty"):
        live.set_value("model_repo", " ")


def test_commit_model_updates_config_and_file_and_is_not_reloaded(setup, monkeypatch):
    live, stat, path = setup
    stat.mtime = 9
    live.commit_model("mlx-community/whisper-base.en-mlx", "en")
    assert (live.cfg.model_repo, live.cfg.language) == ("mlx-community/whisper-base.en-mlx", "en")
    text = path.read_text()
    assert "whisper-base.en-mlx" in text and 'language = "en"' in text
    monkeypatch.setattr("murmur.settings.load_config", lambda p: pytest.fail("reloaded own write"))
    assert live.refresh() is False


def test_revert_model_restores_only_a_hand_edited_file(setup):
    live, stat, path = setup
    edit(path, stat, 'model_repo = "bad/repo"\n')
    live.revert_model("bad/repo", "old/repo", from_file=False)
    assert "bad/repo" in path.read_text()  # a menu switch never wrote the file
    live.revert_model("bad/repo", "old/repo", from_file=True)
    assert 'model_repo = "old/repo"' in path.read_text()


def test_choosing_the_configured_model_again_still_reaches_the_switcher(setup):
    live, _, path = setup
    calls = []
    live.model_hook = lambda repo, from_file: calls.append((repo, from_file))
    live.set_value("model_repo", live.cfg.model_repo)  # a retry after a failed first load
    assert calls == [(live.cfg.model_repo, False)] and not path.exists()
