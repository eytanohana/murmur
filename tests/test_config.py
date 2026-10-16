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


def test_the_old_menubar_key_is_now_an_unknown_key(tmp_path):
    with pytest.raises(ValueError, match="menubar"):
        load_config(write(tmp_path, "menubar = true\n"))


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


@pytest.mark.parametrize(
    "line",
    [
        "stream = true",
        "min_hold_ms = 1500",
        "max_record_s = 90.5",
        "max_record_s = 90",  # an int is fine where a float is expected
        "silence_rms = 0.01",
        'model_repo = "mlx-community/whisper-medium.en-mlx"',
        "stream_holdback_words = 2",
    ],
)
def test_values_of_the_right_type_load(tmp_path, line):
    cfg = load_config(write(tmp_path, line + "\n"))
    key, _, raw = line.partition(" = ")
    assert str(getattr(cfg, key)).lower() == raw.strip('"').lower()


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('min_hold_ms = "1000"', "min_hold_ms must be int, got str"),
        ("min_hold_ms = 1.5", "min_hold_ms must be int, got float"),
        ("min_hold_ms = true", "min_hold_ms must be int, got bool"),
        ('max_record_s = "x"', "max_record_s must be float, got str"),
        ("max_record_s = true", "max_record_s must be float, got bool"),
        ("stream = 1", "stream must be bool, got int"),
        ('stream = "yes"', "stream must be bool, got str"),
        ("model_repo = 5", "model_repo must be str, got int"),
        ("sounds = 0.5", "sounds must be bool, got float"),
        ("insert_mode = 1", "insert_mode must be str, got int"),
    ],
)
def test_values_of_the_wrong_type_name_the_key_and_the_expected_type(tmp_path, line, message):
    with pytest.raises(ValueError, match=message):
        load_config(write(tmp_path, line + "\n"))


def test_a_complete_valid_file_still_loads(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            'model_repo = "m"\nlanguage = "en"\nsample_rate = 16000\nmin_hold_ms = 800\n'
            'max_record_s = 60\nsilence_rms = 0.004\noption_keys = "left"\n'
            'insert_mode = "type"\nsounds = true\nindicator = "none"\nstream = false\n',
        )
    )
    assert (cfg.min_hold_ms, cfg.option_keys, cfg.sounds, cfg.stream) == (800, "left", True, False)
