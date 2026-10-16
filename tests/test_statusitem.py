"""StatusItem click handling that needs no display (nothing is built)."""

import pytest

from murmur import statusitem
from murmur.config import Config, load_config
from murmur.settings import LiveSettings, MenuItem


@pytest.fixture
def live(tmp_path):
    return LiveSettings(tmp_path / "config.toml", Config())


@pytest.fixture
def item(live):
    it = statusitem.StatusItem(live, on_restart=lambda: None)
    it.menu = type("M", (), {"removeAllItems": lambda self: None})()
    it._fill = lambda menu, items: setattr(it, "filled", items)  # no AppKit menu here
    return it


def setting(key, value):
    return MenuItem("setting", key=key, value=value)


def test_a_setting_click_writes_the_file_and_applies_it_to_the_live_config(item, live):
    item._by_tag[1] = setting("sounds", True)
    item._on_click(1)
    assert load_config(live.path).sounds is True
    assert live.cfg.sounds is True


def test_a_failed_write_changes_nothing(item, live, caplog, monkeypatch):
    def fail(*a):
        raise OSError("disk full")

    monkeypatch.setattr("murmur.settings.write_setting", fail)
    item._by_tag[1] = setting("sounds", True)
    with caplog.at_level("ERROR", logger="murmur"):
        item._on_click(1)
    assert live.cfg.sounds is False
    assert "Could not save sounds" in caplog.text


def test_an_invalid_value_is_rejected_before_anything_is_written(item, live):
    item._by_tag[1] = setting("indicator_position", "middle")
    item._on_click(1)
    assert not live.path.exists() and live.cfg.indicator_position == "top-center"


def test_restart_click_calls_the_restart_callback(live):
    called = []
    it = statusitem.StatusItem(live, lambda: called.append(1))
    it._by_tag[1] = MenuItem("action", action="restart")
    it._on_click(1)
    assert called == [1]


def test_open_config_creates_the_file_and_opens_it(item, live, monkeypatch):
    opened = []
    monkeypatch.setattr(statusitem.subprocess, "Popen", lambda cmd: opened.append(cmd))
    item._by_tag[1] = MenuItem("action", action="open_config")
    item._on_click(1)
    assert live.path.read_text().startswith("# murmur config.")
    assert opened == [["open", "-t", str(live.path)]]


def test_opening_the_menu_rereads_a_changed_file_and_redraws(item, live):
    live.path.write_text('sounds = true\nindicator = "none"\n')
    item._on_open()
    assert live.cfg.sounds is True and live.pending == {"indicator": "none"}
    titles = [i.title for i in item.filled]
    assert "Restart to apply changes (indicator)" in titles


def test_opening_the_menu_with_a_broken_file_shows_the_error_header(item, live):
    live.path.write_text("sounds = [\n")
    item._on_open()
    assert item.filled[0].title == "Config error: see log"


def test_unknown_tag_is_ignored(item, live):
    item._on_click(99)
    assert not live.path.exists()


def test_the_settings_item_opens_the_window(live):
    opened = []
    window = type("W", (), {"show": lambda self: opened.append(1)})()
    it = statusitem.StatusItem(live, lambda: None, window=window)
    it._by_tag[1] = MenuItem("action", action="settings")
    it._on_click(1)
    assert opened == [1]


def test_the_settings_item_without_a_window_does_nothing(live):
    it = statusitem.StatusItem(live, lambda: None)
    it._by_tag[1] = MenuItem("action", action="settings")
    it._on_click(1)  # must not raise


def test_a_model_click_goes_to_the_switcher_not_the_file(live):
    calls = []
    live.model_hook = lambda repo, from_file: calls.append((repo, from_file))
    it = statusitem.StatusItem(live, lambda: None)
    it.menu = type("M", (), {"removeAllItems": lambda self: None})()
    it._fill = lambda menu, items: None
    it._by_tag[1] = setting("model_repo", "mlx-community/whisper-base.en-mlx")
    it._on_click(1)
    assert calls == [("mlx-community/whisper-base.en-mlx", False)]
    assert not live.path.exists()
