"""The curated model list, cache detection and the offline switch for Hugging Face downloads."""

from __future__ import annotations

import contextlib
import logging
import os
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("murmur")

WEIGHT_FILES = ("weights.safetensors", "weights.npz")  # what mlx_whisper.load_model looks for


@dataclass(frozen=True)
class ModelInfo:
    repo: str
    name: str
    size_mb: int  # weights size reported by the Hugging Face API
    english_only: bool
    note: str = ""


# Every entry exists on Hugging Face and loads with mlx_whisper 0.4.x, which only reads
# weights.safetensors or weights.npz. The mlx-community 4 and 8 bit turbo conversions ship
# model.safetensors and fail to load, so they are not listed.
CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo("mlx-community/whisper-tiny.en-mlx", "tiny.en", 74, True),
    ModelInfo("mlx-community/whisper-base.en-mlx", "base.en", 144, True),
    ModelInfo("mlx-community/whisper-small.en-mlx", "small.en", 481, True),
    ModelInfo("mlx-community/whisper-medium.en-mlx", "medium.en", 1525, True),
    ModelInfo("mlx-community/whisper-large-v3-turbo", "large-v3 turbo", 1614, False, "default"),
    ModelInfo("mlx-community/whisper-large-v3-mlx", "large-v3", 3084, False),
    ModelInfo(
        "mlx-community/distil-whisper-large-v3",
        "distil large-v3",
        1509,
        True,
        "no punctuation or capitals",
    ),
)
BY_REPO = {m.repo: m for m in CATALOG}


def is_english_only(repo: str) -> bool:
    """True for English-only models, including repos that are not in the catalog."""
    info = BY_REPO.get(repo)
    if info is not None:
        return info.english_only
    return repo.endswith((".en", ".en-mlx"))


def short_name(repo: str) -> str:
    info = BY_REPO.get(repo)
    return info.name if info else repo.rsplit("/", 1)[-1]


def format_size(size_mb: int) -> str:
    return f"{size_mb} MB" if size_mb < 1000 else f"{size_mb / 1000:.1f} GB"


def model_label(info: ModelInfo, cached: bool) -> str:
    """Dropdown text: name, size, language scope and whether it is already downloaded."""
    scope = "English" if info.english_only else "multilingual"
    state = "cached" if cached else "download"
    extra = f", {info.note}" if info.note and info.note != "default" else ""
    return f"{info.name} ({format_size(info.size_mb)}, {scope}, {state}{extra})"


def is_cached(repo: str, cache_dir: str | Path | None = None) -> bool:
    """True when a complete snapshot (config and weights) is in the local cache."""
    from huggingface_hub import snapshot_download

    try:
        path = Path(snapshot_download(repo, local_files_only=True, cache_dir=cache_dir))
    except Exception:  # noqa: BLE001  not cached, partial, or the cache dir is unreadable
        return False
    return (path / "config.json").exists() and any((path / f).exists() for f in WEIGHT_FILES)


@dataclass(frozen=True)
class CachedModel:
    repo: str
    name: str
    path: Path  # the repo's folder in the Hugging Face cache
    size_bytes: int


def cache_folder(repo: str, cache_dir: str | Path | None = None) -> Path:
    """The Hugging Face cache folder of one repo (`models--org--name`)."""
    if cache_dir is None:
        from huggingface_hub import constants

        cache_dir = constants.HF_HUB_CACHE
    return Path(cache_dir) / ("models--" + repo.replace("/", "--"))


def folder_size(path: Path) -> int:
    """Bytes of the regular files under `path`. Symlinks (snapshots point at blobs) are
    not followed, so nothing is counted twice or from outside the folder."""
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


def curated_cache(cache_dir: str | Path | None = None) -> list[CachedModel]:
    """The catalog's models that have a folder in the cache. Other repos are never listed."""
    found = []
    for info in CATALOG:
        folder = cache_folder(info.repo, cache_dir)
        if folder.is_dir() and not folder.is_symlink():
            found.append(CachedModel(info.repo, info.name, folder, folder_size(folder)))
    return found


def format_bytes(n: int) -> str:
    if n < 1000:
        return f"{n} B"
    if n < 1_000_000:
        return f"{n / 1000:.0f} KB"
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.0f} MB"
    return f"{n / 1_000_000_000:.1f} GB"


def set_offline(flag: bool) -> None:
    """Turn Hugging Face's offline mode on or off for this process, whatever the env said."""
    from huggingface_hub import constants

    constants.HF_HUB_OFFLINE = flag


def get_offline() -> bool:
    from huggingface_hub import constants

    return bool(constants.HF_HUB_OFFLINE)


def prepare_offline(repo: str, cached: Callable[[str], bool] = is_cached) -> bool:
    """At startup go offline only when the configured model is cached.

    Login without a network then still starts, and an uncached model can download even
    when an older LaunchAgent plist exported HF_HUB_OFFLINE=1. Returns the new setting.
    """
    offline = cached(repo)
    set_offline(offline)
    if not offline:
        log.info("Model %s is not cached; it will be downloaded", repo)
    return offline


@contextlib.contextmanager
def network_allowed() -> Iterator[None]:
    """Allow downloads for an explicit model switch, then restore the previous setting."""
    previous = get_offline()
    set_offline(False)
    try:
        yield
    finally:
        set_offline(previous)
