"""Switch the Whisper model while murmur runs.

The new model is downloaded and loaded on a background thread while the old one keeps
serving. The swap itself happens under the transcriber lock and only while no
dictation is running; a switch requested mid-dictation waits for it to end. The config
value changes (and is written to the file) only after the new model loaded.
"""

from __future__ import annotations

import gc
import io
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from . import models

log = logging.getLogger("murmur")

IDLE, LOADING, WAITING, FAILED = "idle", "loading", "waiting", "failed"


@dataclass(frozen=True)
class SwitchState:
    status: str = IDLE
    target: str | None = None
    progress: float | None = None  # 0..1 while downloading; None when not measurable
    error: str | None = None

    @property
    def busy(self) -> bool:
        return self.status in (LOADING, WAITING)


class Loader(Protocol):
    def download(self, repo: str, on_bytes: Callable[[int], None]) -> None: ...

    def load(self, repo: str) -> Any: ...

    def install(self, model: Any, repo: str) -> Any:
        """Make `model` the one used for decoding. Returns the previous model."""

    def warm_up(self) -> None: ...

    def release(self) -> None: ...


def _progress_class(on_bytes: Callable[[int], None]):
    from tqdm.auto import tqdm

    class ByteProgress(tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["file"] = io.StringIO()  # keep the terminal clean
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            if self.unit == "B":
                on_bytes(int(n))
            return super().update(n)

    return ByteProgress


class MlxLoader:
    """The real loader: mlx_whisper's loader and its process-wide model slot."""

    def __init__(self, warm_up: Callable[[], None]):
        self._warm_up = warm_up

    def download(self, repo: str, on_bytes: Callable[[int], None]) -> None:
        if models.is_cached(repo):
            return
        from huggingface_hub import snapshot_download

        with models.network_allowed():  # an explicit switch may download
            snapshot_download(repo, tqdm_class=_progress_class(on_bytes))

    def load(self, repo: str) -> Any:
        import mlx.core as mx
        from mlx_whisper.load_models import load_model

        return load_model(repo, dtype=mx.float16)  # the dtype transcribe(fp16=True) expects

    def install(self, model: Any, repo: str) -> Any:
        from mlx_whisper.transcribe import ModelHolder

        previous = ModelHolder.model
        ModelHolder.model, ModelHolder.model_path = model, repo
        return previous

    def warm_up(self) -> None:
        self._warm_up()

    def release(self) -> None:
        import mlx.core as mx

        gc.collect()
        mx.clear_cache()


class ModelSwitcher:
    def __init__(
        self,
        cfg,
        loader: Loader,
        lock,
        is_busy: Callable[[], bool],
        on_commit: Callable[[str, str | None], None],
        on_failed: Callable[[str, str, bool], None] = lambda repo, old, from_file: None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.loader = loader
        self.lock = lock  # the transcriber's decode lock
        self.is_busy = is_busy  # True while a dictation is running
        self.on_commit = on_commit  # (repo, language or None): update config and file
        self.on_failed = on_failed  # (repo, old_repo, from_file): restore a hand-edited file
        self._sleep = sleep
        self.ready = True  # False until the first model of a session finished loading
        self.state = SwitchState()
        self.listeners: list[Callable[[SwitchState], None]] = []
        self._generation = 0
        self._downloaded = 0
        self._thread: threading.Thread | None = None

    # state

    def _set(self, **changes) -> None:
        self.state = replace(self.state, **changes)
        for listener in list(self.listeners):
            listener(self.state)

    def _on_bytes(self, n: int) -> None:
        self._downloaded += n
        info = models.BY_REPO.get(self.state.target or "")
        if info:
            self._set(progress=min(self._downloaded / (info.size_mb * 1e6), 0.99))

    # control

    def request(self, repo: str, from_file: bool = False) -> None:
        """Start switching to `repo`. Asking for the running model cancels a pending switch.

        Asking for the configured model while it never finished loading (a failed first
        download) retries that load.
        """
        self._generation += 1
        gen = self._generation
        if repo == self.cfg.model_repo and self.ready:
            self.state = SwitchState()  # back to the current model: nothing to load
            self._set()
            return
        initial = repo == self.cfg.model_repo
        self.ready = self.ready and not initial
        self._thread = threading.Thread(
            target=self._run, args=(repo, gen, from_file, initial), daemon=True
        )
        self._thread.start()

    def start_initial(self, repo: str) -> None:
        """Load the first model in the background, so the app is up while it downloads."""
        self.ready = False
        self.request(repo)

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _stale(self, gen: int) -> bool:
        return gen != self._generation

    def _run(self, repo: str, gen: int, from_file: bool, initial: bool = False) -> None:
        old_repo = self.cfg.model_repo
        self._downloaded = 0
        self._set(status=LOADING, target=repo, progress=0.0, error=None)
        if initial:
            log.info("Loading model %s (first load of this session)", repo)
        else:
            log.info("Switching model: %s -> %s", old_repo, repo)
        t0 = time.monotonic()
        try:
            self.loader.download(repo, self._on_bytes)
            if self._stale(gen):
                return
            self._set(progress=None)  # reading the weights into memory
            model = self.loader.load(repo)
            if self._stale(gen):
                self.loader.release()
                return
            swapped, previous = self._swap_when_idle(model, repo, gen, initial)
        except Exception as exc:  # noqa: BLE001  any failure keeps the old model serving
            log.error("Could not switch to %s, keeping %s: %s", repo, old_repo, exc)
            if not self._stale(gen):
                self._set(status=FAILED, error=str(exc), progress=None)
                self.on_failed(repo, old_repo, from_file)
            return
        if not swapped:
            return
        del previous
        self.loader.release()  # free the old weights and the MLX cache
        self._set(status=IDLE, progress=None)
        log.info("Model %s ready in %.1fs", repo, time.monotonic() - t0)

    def _swap_when_idle(
        self, model: Any, repo: str, gen: int, initial: bool = False
    ) -> tuple[bool, Any]:
        """Install between dictations. Returns (swapped, previous model)."""
        waiting_logged = False
        while True:
            if self._stale(gen):
                return False, None
            if self.is_busy():
                if not waiting_logged:
                    log.info("Model %s is ready; swapping when the dictation ends", repo)
                    waiting_logged = True
                    self._set(status=WAITING)
                self._sleep(0.1)
                continue
            with self.lock:
                if self.is_busy():  # a press slipped in before we took the lock
                    continue
                previous = self.loader.install(model, repo)
                language = (
                    "en" if models.is_english_only(repo) and self.cfg.language != "en" else None
                )
                if not initial:  # the configured model is already the live value
                    self.on_commit(repo, language)
                try:
                    self.loader.warm_up()
                except Exception:
                    log.exception("Warm-up after the model switch failed")
            self.ready = True
            return True, previous
