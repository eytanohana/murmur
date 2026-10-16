from dataclasses import fields

import pytest

from murmur import settings_schema as schema
from murmur.config import Config, load_config
from murmur.settings_schema import BY_KEY, GROUPS, SCHEMA, validate_setting

DEFAULTS = Config()


def test_every_config_field_is_in_the_schema_and_nothing_else():
    assert [s.key for s in SCHEMA if s.key not in {f.name for f in fields(Config)}] == []
    assert {f.name for f in fields(Config)} == set(BY_KEY)
    assert len(BY_KEY) == len(SCHEMA)  # no key described twice


def test_every_setting_has_text_a_known_group_and_a_known_kind():
    for s in SCHEMA:
        assert s.label and s.help and s.group in GROUPS, s.key
        assert s.kind in {"toggle", "choice", "number", "text", "sound", "model"}, s.key
        assert chr(0x2014) not in s.label + s.help, s.key  # no em dashes in UI text
    assert {s.group for s in SCHEMA} == set(GROUPS)


@pytest.mark.parametrize("setting", SCHEMA, ids=lambda s: s.key)
def test_every_default_is_valid(setting):
    validate_setting(setting.key, getattr(DEFAULTS, setting.key))


@pytest.mark.parametrize("setting", [s for s in SCHEMA if s.kind == "number"], ids=lambda s: s.key)
def test_number_settings_have_a_sane_range_and_step(setting):
    assert setting.minimum < setting.maximum and setting.step > 0
    assert setting.minimum <= getattr(DEFAULTS, setting.key) <= setting.maximum
    assert setting.unit or setting.key == "silence_rms"


@pytest.mark.parametrize(
    ("key", "low", "high"),
    [
        ("min_hold_ms", 0, 3000),
        ("stream_interval", 0.2, 3.0),
        ("silence_rms", 0.001, 0.05),
        ("stream_holdback_words", 0, 5),
        ("stream_trim_s", 10, 28),
        ("paste_restore_delay_ms", 0, 1000),
    ],
)
def test_the_documented_ranges(key, low, high):
    setting = BY_KEY[key]
    assert (setting.minimum, setting.maximum) == (low, high)
    validate_setting(key, low)
    validate_setting(key, high)
    for bad in (low - 0.5 if low else -1, high + 1):
        with pytest.raises(ValueError, match=f"{key} must be between"):
            validate_setting(key, bad)


def test_range_errors_name_the_unit():
    with pytest.raises(ValueError, match=r"min_hold_ms must be between 0 and 3000 ms"):
        validate_setting("min_hold_ms", 5000)


def test_int_settings_reject_floats_and_float_settings_take_ints():
    with pytest.raises(ValueError, match="min_hold_ms must be int, got float"):
        validate_setting("min_hold_ms", 500.5)
    validate_setting("stream_interval", 1)  # an int is fine for a float setting


def test_choice_settings_accept_only_their_values():
    for s in (BY_KEY["option_keys"], BY_KEY["insert_mode"], BY_KEY["indicator_position"]):
        for v in s.values:
            validate_setting(s.key, v)
        with pytest.raises(ValueError, match=f"{s.key} must be"):
            validate_setting(s.key, "nonsense")
    with pytest.raises(ValueError, match="sample_rate must be 16000"):
        validate_setting("sample_rate", 8000)


def test_text_settings_must_not_be_empty():
    for key in ("model_repo", "language", "start_sound"):
        with pytest.raises(ValueError, match=f"{key} must not be empty"):
            validate_setting(key, "  ")


def test_live_and_restart_split():
    assert schema.RESTART_KEYS == {"sample_rate", "indicator"}
    assert schema.LIVE_KEYS | schema.RESTART_KEYS == set(BY_KEY)
    assert not schema.LIVE_KEYS & schema.RESTART_KEYS
    assert {"model_repo", "language"} <= schema.LIVE_KEYS


def test_conditions_point_at_real_keys():
    for s in SCHEMA:
        if s.when:
            assert s.when[0] in BY_KEY and s.when_note, s.key
    assert BY_KEY["insert_mode"].when == ("stream", False)


def test_the_config_file_uses_the_same_rules(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("stream_trim_s = 40\n")
    with pytest.raises(ValueError, match="stream_trim_s must be between 10 and 28 s"):
        load_config(p)
