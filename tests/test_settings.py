import pytest

from murmur import models, settings
from murmur.agent import LABEL
from murmur.config import Config, load_config
from murmur.modelswitch import SwitchState


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


def test_menu_headers_show_status_and_the_current_model_and_are_disabled():
    menu = settings.build_menu(Config(model_repo="mlx-community/whisper-small.en-mlx"))
    assert [(i.title, i.enabled) for i in menu[:2]] == [
        ("murmur is running", False),
        ("Model: small.en (481 MB)", False),
    ]


def test_header_shows_a_model_load_in_progress_and_a_config_error():
    loading = SwitchState("loading", "mlx-community/whisper-base.en-mlx", progress=0.5)
    assert settings.build_menu(Config(), switch=loading)[0].title == "Loading base.en 50%..."
    assert settings.build_menu(Config(), error="bad")[0].title == "Config error: see log"


def test_toggles_show_the_current_value_and_write_the_opposite():
    menu = settings.build_menu(Config(stream=True, sounds=False))
    streaming, sounds = find(menu, "Streaming"), find(menu, "Sounds")
    assert (streaming.checked, streaming.key, streaming.value) == (True, "stream", False)
    assert (sounds.checked, sounds.key, sounds.value) == (False, "sounds", True)


def test_the_menu_is_compact():
    titles = [i.title for i in settings.build_menu(Config()) if i.kind != "separator"]
    assert titles[2:] == [
        "Streaming",
        "Sounds",
        "Model",
        "Settings...",
        "Open config file",
        "Restart murmur",
        "Quit murmur",
    ]


def test_model_submenu_has_one_radio_checkmark_and_shows_size_and_cache_state():
    current = "mlx-community/whisper-small.en-mlx"
    menu = settings.build_menu(Config(model_repo=current), cached={current})
    submenu = find(menu, "Model")
    assert len(submenu.children) == len(models.CATALOG)
    assert [c.value for c in submenu.children if c.checked] == [current]
    assert all(c.key == "model_repo" for c in submenu.children)
    labels = {c.value: c.title for c in submenu.children}
    assert labels[current] == "small.en (481 MB, English, cached)"
    assert labels["mlx-community/whisper-tiny.en-mlx"].endswith("download)")


def test_a_model_outside_the_catalog_is_listed_and_checked():
    menu = settings.build_menu(Config(model_repo="me/custom"))
    children = find(menu, "Model").children
    assert children[0].title == "custom (custom)" and children[0].checked


def test_restart_item_names_the_pending_restart_keys():
    plain = find(settings.build_menu(Config()), "Restart murmur")
    assert (plain.enabled, plain.checked) == (True, False)
    pending = settings.build_menu(Config(), pending=["menubar_icon", "indicator"])
    item = next(i for i in pending if i.action == "restart")
    assert item.title == "Restart to apply changes (indicator, menubar_icon)"
    assert (item.enabled, item.checked) == (True, True)


def test_config_error_replaces_the_running_header():
    menu = settings.build_menu(Config(), error="bad value")
    assert (menu[0].title, menu[0].enabled) == ("Config error: see log", False)
    assert settings.build_menu(Config())[0].title == "murmur is running"


def test_actions_and_order():
    menu = settings.build_menu(Config())
    assert [i.action for i in menu if i.kind == "action"] == [
        "settings",
        "open_config",
        "restart",
        "quit",
    ]
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
