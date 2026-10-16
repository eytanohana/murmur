import pytest

from murmur import settings
from murmur.agent import LABEL
from murmur.config import POSITIONS, Config, load_config

INSERT_LABEL = "Insert mode (used when streaming is off)"


def flatten(items):
    for item in items:
        yield item
        yield from flatten(item.children)


def find(items, title):
    return next(i for i in flatten(items) if i.title == title)


# ---- icons -------------------------------------------------------------------


def test_icon_per_state():
    assert settings.icon_for("recording") == "mic.fill"
    assert settings.icon_for("idle") == "mic"
    assert settings.icon_for("transcribing") == "mic"  # the pill shows "..." for this state


# ---- menu model ----------------------------------------------------------------


def test_menu_headers_show_status_and_model_and_are_disabled():
    menu = settings.build_menu(Config(model_repo="org/model"))
    assert [(i.title, i.enabled) for i in menu[:2]] == [
        ("murmur is running", False),
        ("org/model", False),
    ]


def test_toggles_show_the_current_value_and_write_the_opposite():
    menu = settings.build_menu(Config(stream=True, sounds=False))
    streaming, sounds = find(menu, "Streaming"), find(menu, "Sounds")
    assert (streaming.checked, streaming.key, streaming.value) == (True, "stream", False)
    assert (sounds.checked, sounds.key, sounds.value) == (False, "sounds", True)


def test_position_submenu_has_one_radio_checkmark_per_current_value():
    menu = settings.build_menu(Config(indicator_position="bottom-left"), pending=[])
    submenu = find(menu, "Overlay position")
    assert [c.title for c in submenu.children] == list(POSITIONS)
    assert [c.title for c in submenu.children if c.checked] == ["bottom-left"]
    assert all(c.key == "indicator_position" and c.value == c.title for c in submenu.children)


def test_insert_mode_submenu_is_only_enabled_when_streaming_is_off():
    on = find(settings.build_menu(Config(insert_mode="type", stream=True)), INSERT_LABEL)
    off = find(settings.build_menu(Config(insert_mode="type", stream=False)), INSERT_LABEL)
    assert on.enabled is False and off.enabled is True
    assert [(c.title, c.checked) for c in off.children] == [("paste", False), ("type", True)]


def test_restart_item_names_the_pending_restart_keys():
    plain = find(settings.build_menu(Config()), "Restart murmur")
    assert (plain.enabled, plain.checked) == (True, False)
    pending = settings.build_menu(Config(), pending=["language", "indicator"])
    item = next(i for i in pending if i.action == "restart")
    assert item.title == "Restart to apply changes (indicator, language)"
    assert (item.enabled, item.checked) == (True, True)


def test_config_error_replaces_the_running_header():
    menu = settings.build_menu(Config(), error="bad value")
    assert (menu[0].title, menu[0].enabled) == ("Config error: see log", False)
    assert settings.build_menu(Config())[0].title == "murmur is running"


def test_actions_and_order():
    menu = settings.build_menu(Config(), pending=[])
    actions = [i.action for i in menu if i.kind == "action"]
    assert actions == ["open_config", "restart", "quit"]
    assert menu[-1].title == "Quit murmur"


# ---- writing the config ----------------------------------------------------------


def test_write_setting_keeps_comments_order_and_other_keys(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("# my notes\nmin_hold_ms = 900  # slow typist\n\n# streaming\nstream = true\n")
    settings.write_setting(p, "stream", False)
    settings.write_setting(p, "sounds", True)
    text = p.read_text()
    assert "# my notes" in text and "# slow typist" in text and "# streaming" in text
    assert text.index("min_hold_ms") < text.index("stream") < text.index("sounds")
    cfg = load_config(p)
    assert (cfg.min_hold_ms, cfg.stream, cfg.sounds) == (900, False, True)


def test_write_setting_creates_the_file_with_a_header(tmp_path):
    p = tmp_path / "new" / "dir" / "config.toml"
    settings.write_setting(p, "indicator_position", "top-right")
    assert p.read_text().startswith("# murmur config.")
    assert load_config(p).indicator_position == "top-right"


def test_write_setting_validates_before_touching_the_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("stream = true\n")
    for key, value in [("stream", "off"), ("indicator_position", "middle"), ("nope", 1)]:
        with pytest.raises(ValueError):
            settings.write_setting(p, key, value)
    assert p.read_text() == "stream = true\n"
    assert not (tmp_path / "config.toml.tmp").exists()


def test_write_setting_leaves_no_temp_file(tmp_path):
    p = tmp_path / "config.toml"
    settings.write_setting(p, "stream", False)
    assert [f.name for f in tmp_path.iterdir()] == ["config.toml"]


def test_ensure_config_file_is_idempotent(tmp_path):
    p = tmp_path / "c" / "config.toml"
    settings.ensure_config_file(p)
    p.write_text("stream = false\n")
    settings.ensure_config_file(p)  # must not overwrite an existing file
    assert p.read_text() == "stream = false\n"


# ---- restart ----------------------------------------------------------------------


def test_restart_under_launchd_uses_kickstart():
    how, cmd = settings.restart_plan({"XPC_SERVICE_NAME": LABEL}, "/py", ["python", "-m", "murmur"])
    assert how == "launchctl"
    assert cmd[:3] == ["launchctl", "kickstart", "-k"] and cmd[3].endswith(f"/{LABEL}")


@pytest.mark.parametrize("env", [{}, {"XPC_SERVICE_NAME": "0"}, {"XPC_SERVICE_NAME": "other"}])
def test_restart_outside_launchd_replays_the_original_command(env):
    how, cmd = settings.restart_plan(env, "/venv/bin/python", ["python", "-m", "murmur", "-v"])
    assert how == "exec"
    assert cmd == ["/venv/bin/python", "-m", "murmur", "-v"]


def test_restart_replays_the_console_script_form():
    _, cmd = settings.restart_plan(
        {}, "/venv/bin/python", ["python", "/venv/bin/murmur", "--config", "c"]
    )
    assert cmd == ["/venv/bin/python", "/venv/bin/murmur", "--config", "c"]
