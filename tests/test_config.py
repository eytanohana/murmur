import pytest

from whisper_dictation.config import load_config


def test_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "none.toml")
    assert cfg.model_repo == "mlx-community/whisper-large-v3-turbo"
    assert cfg.min_hold_ms == 1000
    assert cfg.sounds is False


def test_overrides(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('min_hold_ms = 500\ninsert_mode = "type"\n')
    cfg = load_config(p)
    assert cfg.min_hold_ms == 500
    assert cfg.insert_mode == "type"


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("bogus = 1\n")
    with pytest.raises(ValueError):
        load_config(p)


def test_streaming_defaults(tmp_path):
    cfg = load_config(tmp_path / "none.toml")
    assert cfg.stream is True
    assert (cfg.stream_interval, cfg.stream_holdback_words, cfg.stream_min_speech_s) == (0.5, 1, 0.3)
