from pathlib import Path

import pytest

from murmur import models


def test_catalog_has_unique_repos_sizes_and_the_default():
    repos = [m.repo for m in models.CATALOG]
    assert len(repos) == len(set(repos))
    assert all(m.size_mb > 0 for m in models.CATALOG)
    from murmur.config import Config

    assert Config().model_repo in models.BY_REPO


def test_only_repos_that_load_with_mlx_whisper_are_listed():
    # model.safetensors conversions (the 4 and 8 bit turbo repos) cannot be loaded
    assert not any(m.repo.endswith(("-4bit", "-8bit")) for m in models.CATALOG)


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ("mlx-community/whisper-small.en-mlx", True),
        ("mlx-community/whisper-large-v3-turbo", False),
        ("mlx-community/distil-whisper-large-v3", True),
        ("someone/custom.en", True),  # not in the catalog: judged by the name
        ("someone/custom-multilingual", False),
    ],
)
def test_english_only_detection(repo, expected):
    assert models.is_english_only(repo) is expected


def test_size_and_label_formatting():
    assert models.format_size(74) == "74 MB"
    assert models.format_size(1614) == "1.6 GB"
    info = models.BY_REPO["mlx-community/whisper-small.en-mlx"]
    assert models.model_label(info, cached=True) == "small.en (481 MB, English, cached)"
    turbo = models.BY_REPO["mlx-community/whisper-large-v3-turbo"]
    assert (
        models.model_label(turbo, cached=False) == "large-v3 turbo (1.6 GB, multilingual, download)"
    )
    distil = models.BY_REPO["mlx-community/distil-whisper-large-v3"]
    assert "no punctuation or capitals" in models.model_label(distil, True)


def test_short_name_falls_back_to_the_repo_tail():
    assert models.short_name("mlx-community/whisper-base.en-mlx") == "base.en"
    assert models.short_name("me/my-model") == "my-model"


def make_snapshot(cache: Path, repo: str, files: tuple[str, ...]) -> None:
    folder = cache / ("models--" + repo.replace("/", "--"))
    snap = folder / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text("abc")
    for f in files:
        (snap / f).write_text("x")


def test_is_cached_needs_config_and_weights(tmp_path):
    repo = "mlx-community/whisper-tiny.en-mlx"
    assert models.is_cached(repo, cache_dir=tmp_path) is False
    make_snapshot(tmp_path, repo, ("config.json",))
    assert models.is_cached(repo, cache_dir=tmp_path) is False  # no weights yet
    repo2 = "mlx-community/whisper-base.en-mlx"
    make_snapshot(tmp_path, repo2, ("config.json", "weights.npz"))
    assert models.is_cached(repo2, cache_dir=tmp_path) is True
    repo3 = "mlx-community/whisper-small.en-mlx"
    make_snapshot(tmp_path, repo3, ("config.json", "weights.safetensors"))
    assert models.is_cached(repo3, cache_dir=tmp_path) is True


@pytest.fixture
def restore_offline():
    before = models.get_offline()
    yield
    models.set_offline(before)


def test_prepare_offline_goes_offline_only_for_a_cached_model(restore_offline):
    assert models.prepare_offline("a", cached=lambda r: True) is True
    assert models.get_offline() is True
    # an uncached model must be able to download even if the environment said offline
    models.set_offline(True)
    assert models.prepare_offline("b", cached=lambda r: False) is False
    assert models.get_offline() is False


def test_network_allowed_restores_the_previous_setting(restore_offline):
    models.set_offline(True)
    with models.network_allowed():
        assert models.get_offline() is False
    assert models.get_offline() is True
    with pytest.raises(RuntimeError), models.network_allowed():
        raise RuntimeError("download failed")
    assert models.get_offline() is True
