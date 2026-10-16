import pytest

from murmur import settings_view as view
from murmur.config import Config
from murmur.modelswitch import SwitchState
from murmur.settings_schema import BY_KEY, GROUPS, SCHEMA

OLD = "mlx-community/whisper-large-v3-turbo"
EN = "mlx-community/whisper-tiny.en-mlx"


def test_every_setting_is_in_exactly_one_tab():
    seen = [s.key for g in GROUPS for s in view.settings_in(g)]
    assert sorted(seen) == sorted(s.key for s in SCHEMA)
    assert [g for g in GROUPS if not view.settings_in(g)] == []


@pytest.mark.parametrize(
    ("step", "expected"), [(0.001, 3), (0.1, 1), (0.05, 2), (1, 0), (10, 0), (50, 0)]
)
def test_decimals(step, expected):
    assert view.decimals(step) == expected


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("min_hold_ms", 1013, 1000),
        ("min_hold_ms", 1030, 1050),
        ("min_hold_ms", -200, 0),
        ("min_hold_ms", 99999, 3000),
        ("stream_interval", 0.54, 0.5),
        ("stream_interval", 0.56, 0.6),
        ("silence_rms", 0.0123, 0.012),
        ("stream_holdback_words", 2.4, 2),
        ("max_record_s", 123, 120),
    ],
)
def test_snap_rounds_to_the_step_and_clamps(key, raw, expected):
    value = view.snap(BY_KEY[key], raw)
    assert value == expected and isinstance(value, int) == BY_KEY[key].integer


def test_snapped_values_always_validate():
    from murmur.settings_schema import validate_setting

    for s in SCHEMA:
        if s.kind == "number":
            for raw in (s.minimum - 5, s.minimum, (s.minimum + s.maximum) / 2.7, s.maximum + 5):
                validate_setting(s.key, view.snap(s, raw))


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("min_hold_ms", 1000, "1000 ms"),
        ("stream_interval", 0.5, "0.5 s"),
        ("silence_rms", 0.005, "0.005"),
        ("stream_holdback_words", 1, "1 words"),
        ("max_record_s", 120.0, "120 s"),
    ],
)
def test_format_value(key, value, expected):
    assert view.format_value(BY_KEY[key], value) == expected


def test_insert_mode_is_disabled_with_a_note_while_streaming():
    on = view.row_state(BY_KEY["insert_mode"], Config(stream=True), [])
    off = view.row_state(BY_KEY["insert_mode"], Config(stream=False), [])
    assert on.enabled is False and "streaming is off" in on.note
    assert off.enabled is True and off.note == ""


def test_sound_pickers_need_sounds_on():
    assert view.row_state(BY_KEY["start_sound"], Config(sounds=False), []).enabled is False
    assert view.row_state(BY_KEY["start_sound"], Config(sounds=True), []).enabled is True


def test_language_is_disabled_for_english_only_models():
    en = view.row_state(BY_KEY["language"], Config(model_repo=EN), [])
    multi = view.row_state(BY_KEY["language"], Config(model_repo=OLD), [])
    assert en.enabled is False and "English only" in en.note
    assert multi.enabled is True


def test_row_state_flags_restart_and_defaults():
    s = view.row_state(BY_KEY["indicator"], Config(indicator="none"), ["indicator"])
    assert (s.restart_needed, s.is_default) == (True, False)
    s = view.row_state(BY_KEY["indicator"], Config(), [])
    assert (s.restart_needed, s.is_default) == (False, True)


def test_sound_options_lists_aiff_files_sorted(tmp_path):
    for name in ("Pop", "Basso", "Tink"):
        (tmp_path / f"{name}.aiff").write_text("")
    (tmp_path / "notes.txt").write_text("")
    assert [n for _, n in view.sound_options(tmp_path)] == ["Basso", "Pop", "Tink"]


def test_sound_choices_keep_a_path_from_the_config(tmp_path):
    (tmp_path / "Pop.aiff").write_text("")
    options = view.sound_choices("/elsewhere/Chime.aiff", tmp_path)
    assert options[-1] == ("/elsewhere/Chime.aiff", "Chime") and len(options) == 2


def test_model_options_show_size_and_cache_state_and_keep_custom_repos():
    cached = {OLD}
    options = view.model_options(OLD, lambda r: r in cached)
    labels = dict(options)
    assert labels[OLD].endswith("cached)") and "1.6 GB" in labels[OLD]
    assert labels[EN].endswith("download)") and "74 MB" in labels[EN]
    custom = view.model_options("me/mine", lambda r: False)
    assert custom[0] == ("me/mine", "mine (custom)")


def test_model_status_text_for_each_state():
    cfg = Config(model_repo=OLD)
    assert view.model_status_text(cfg, SwitchState()) == "Using large-v3 turbo."
    loading = SwitchState("loading", EN, progress=0.456)
    assert view.model_status_text(cfg, loading) == "Downloading tiny.en: 46%"
    assert "Loading tiny.en" in view.model_status_text(cfg, SwitchState("loading", EN, None))
    assert "after the current dictation" in view.model_status_text(cfg, SwitchState("waiting", EN))
    failed = view.model_status_text(cfg, SwitchState("failed", EN, error="no network"))
    assert "Could not load tiny.en: no network" in failed and "Still using large-v3 turbo" in failed


def test_header_text_priority():
    assert view.header_text(SwitchState(), None) == "murmur is running"
    assert view.header_text(SwitchState("loading", EN, 0.5), None) == "Loading tiny.en 50%..."
    assert view.header_text(SwitchState("loading", EN, None), None) == "Loading tiny.en..."
    assert "Switching to tiny.en" in view.header_text(SwitchState("waiting", EN), None)
    assert view.header_text(SwitchState("failed", EN), None) == "Model load failed: see log"
    assert view.header_text(SwitchState("loading", EN), "bad") == "Config error: see log"


def test_changed_from_defaults_lists_only_changes_with_the_model_last():
    cfg = Config(model_repo=EN, sounds=True, language="fr", min_hold_ms=500)
    keys = [k for k, _ in view.changed_from_defaults(cfg)]
    assert set(keys) == {"model_repo", "sounds", "language", "min_hold_ms"}
    assert keys[-1] == "model_repo"
    assert dict(view.changed_from_defaults(cfg))["language"] == "en"
    assert view.changed_from_defaults(Config()) == []
