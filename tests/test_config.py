import pytest

from murmur.config import INDICATORS, INSERT_MODES, OPTION_KEYS, POSITIONS, load_config


def write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_defaults_when_the_file_is_missing(tmp_path):
    cfg = load_config(tmp_path / "none.toml")
    assert cfg.model_repo == "mlx-community/whisper-large-v3-turbo"
    assert (cfg.min_hold_ms, cfg.sounds, cfg.stream, cfg.insert_mode) == (
        1000,
        False,
        True,
        "paste",
    )
    assert (cfg.stream_interval, cfg.stream_holdback_words) == (0.5, 1)
    assert (cfg.stream_min_speech_s, cfg.stream_flush_silence_s) == (0.3, 0.7)
    assert (cfg.indicator, cfg.indicator_position) == ("overlay", "top-center")


def test_file_values_override_defaults(tmp_path):
    cfg = load_config(write(tmp_path, 'min_hold_ms = 500\ninsert_mode = "type"\n'))
    assert (cfg.min_hold_ms, cfg.insert_mode) == (500, "type")
    assert cfg.stream_interval == 0.5  # untouched keys keep their defaults


def test_menubar_true_is_an_alias_for_the_menubar_indicator(tmp_path):
    assert load_config(write(tmp_path, "menubar = true\n")).indicator == "menubar"


def test_indicator_and_position_can_be_set(tmp_path):
    cfg = load_config(write(tmp_path, 'indicator = "none"\nindicator_position = "bottom-right"\n'))
    assert (cfg.indicator, cfg.indicator_position) == ("none", "bottom-right")


@pytest.mark.parametrize("position", POSITIONS)
def test_every_documented_position_is_accepted(tmp_path, position):
    assert load_config(write(tmp_path, f'indicator_position = "{position}"\n'))


@pytest.mark.parametrize(
    "line",
    [
        "bogus = 1",
        'option_keys = "middle"',
        'insert_mode = "clipboard"',
        'indicator = "dock"',
        'indicator_position = "middle"',
    ],
)
def test_invalid_settings_are_rejected(tmp_path, line):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, line + "\n"))


def test_unknown_key_error_names_the_key(tmp_path):
    with pytest.raises(ValueError, match="bogus"):
        load_config(write(tmp_path, "bogus = 1\n"))


@pytest.mark.parametrize(
    ("key", "values"),
    [
        ("option_keys", OPTION_KEYS),
        ("indicator", INDICATORS),
        ("insert_mode", INSERT_MODES),
    ],
)
def test_every_documented_choice_is_accepted(tmp_path, key, values):
    for value in values:
        assert getattr(load_config(write(tmp_path, f'{key} = "{value}"\n')), key) == value


def test_choice_error_lists_the_valid_values(tmp_path):
    with pytest.raises(ValueError, match="option_keys must be 'left', 'right' or 'both'"):
        load_config(write(tmp_path, 'option_keys = "middle"\n'))
