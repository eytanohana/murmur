"""StatusItem state handling that needs no display (nothing is built)."""

import pytest

from murmur import statusitem
from murmur.config import Config, load_config
from murmur.settings import MenuItem


@pytest.fixture
def item(tmp_path):
    it = statusitem.StatusItem(Config(), tmp_path / "config.toml", on_restart=lambda: None)
    it._rebuild_menu = lambda: None  # no AppKit menu in these tests
    return it


def setting(key, value):
    return MenuItem("setting", key=key, value=value)


def test_a_setting_click_writes_the_file_and_marks_a_pending_restart(item):
    item._by_tag[1] = setting("stream", False)
    item._on_click(1)
    assert load_config(item.config_path).stream is False
    assert item.cfg.stream is False and item.changed is True
    assert Config().stream is True  # the loaded defaults are not mutated


def test_a_failed_write_changes_nothing(item, caplog, monkeypatch):
    def fail(*a):
        raise OSError("disk full")

    monkeypatch.setattr(statusitem, "write_setting", fail)
    item._by_tag[1] = setting("stream", False)
    with caplog.at_level("ERROR", logger="murmur"):
        item._on_click(1)
    assert item.cfg.stream is True and item.changed is False
    assert "Could not save stream" in caplog.text


def test_an_invalid_value_is_rejected_before_anything_is_written(item):
    item._by_tag[1] = setting("indicator_position", "middle")
    item._on_click(1)
    assert not item.config_path.exists() and item.changed is False


def test_restart_click_calls_the_restart_callback(tmp_path):
    called = []
    it = statusitem.StatusItem(Config(), tmp_path / "c.toml", lambda: called.append(1))
    it._by_tag[1] = MenuItem("action", action="restart")
    it._on_click(1)
    assert called == [1]


def test_open_config_creates_the_file_opens_it_and_offers_a_restart(item, monkeypatch):
    opened = []
    monkeypatch.setattr(statusitem.subprocess, "Popen", lambda cmd: opened.append(cmd))
    item._by_tag[1] = MenuItem("action", action="open_config")
    item._on_click(1)
    assert item.config_path.read_text().startswith("# murmur config.")
    assert opened == [["open", "-t", str(item.config_path)]]
    assert item.changed is True


def test_unknown_tag_is_ignored(item):
    item._on_click(99)
    assert item.changed is False
