"""The model switch state machine, driven with a fake loader (no download, no model)."""

import threading

import pytest

from murmur import modelswitch
from murmur.config import Config
from murmur.modelswitch import FAILED, IDLE, LOADING, WAITING, ModelSwitcher

OLD = "mlx-community/whisper-large-v3-turbo"
EN = "mlx-community/whisper-tiny.en-mlx"  # 74 MB, English only
MULTI = "mlx-community/whisper-large-v3-mlx"


class FakeLoader:
    def __init__(self):
        self.calls = []
        self.fail_download = None
        self.fail_load = None
        self.gate = threading.Event()  # load waits for this when cleared
        self.gate.set()
        self.installed = "model:" + OLD
        self.warm_up_fails = False

    def download(self, repo, on_bytes):
        self.calls.append(("download", repo))
        if self.fail_download:
            raise self.fail_download
        for _ in range(4):
            on_bytes(int(74e6 / 4) if repo == EN else 1)

    def load(self, repo):
        self.calls.append(("load", repo))
        self.gate.wait(5)
        if self.fail_load:
            raise self.fail_load
        return "model:" + repo

    def install(self, model, repo):
        self.calls.append(("install", repo))
        previous, self.installed = self.installed, model
        return previous

    def warm_up(self):
        self.calls.append(("warm_up",))
        if self.warm_up_fails:
            raise RuntimeError("warm-up failed")

    def release(self):
        self.calls.append(("release",))


class Rig:
    def __init__(self, language="en", busy=False):
        self.cfg = Config(model_repo=OLD, language=language)
        self.loader = FakeLoader()
        self.busy = busy
        self.committed, self.failed, self.states = [], [], []
        self.lock = threading.RLock()

        def commit(repo, language):
            self.committed.append((repo, language))
            self.cfg.model_repo = repo
            if language:
                self.cfg.language = language

        self.switcher = ModelSwitcher(
            self.cfg,
            self.loader,
            self.lock,
            is_busy=lambda: self.busy,
            on_commit=commit,
            on_failed=lambda *a: self.failed.append(a),
            sleep=lambda s: threading.Event().wait(0.01),
        )
        self.switcher.listeners.append(self.states.append)

    def run(self, repo, from_file=False):
        self.switcher.request(repo, from_file)
        self.switcher.join(5)

    @property
    def statuses(self):
        out = []
        for s in self.states:
            if not out or out[-1] != s.status:
                out.append(s.status)
        return out


def test_success_installs_commits_warms_up_and_frees_the_old_model():
    rig = Rig()
    rig.run(EN)
    assert [c[0] for c in rig.loader.calls] == [
        "download",
        "load",
        "install",
        "warm_up",
        "release",
    ]
    assert rig.committed == [(EN, None)]
    assert rig.loader.installed == "model:" + EN
    assert rig.statuses == [LOADING, IDLE]
    assert rig.switcher.state.status == IDLE and rig.switcher.state.error is None


def test_download_progress_is_reported_as_a_fraction_of_the_catalog_size():
    rig = Rig()
    rig.run(EN)
    fractions = [s.progress for s in rig.states if s.progress]
    assert fractions == sorted(fractions) and 0.2 < fractions[0] < 0.3 and max(fractions) <= 0.99
    assert rig.states[-1].progress is None  # finished


def test_a_failed_download_keeps_the_old_model_and_reports_the_error():
    rig = Rig()
    rig.loader.fail_download = OSError("network down")
    rig.run(EN, from_file=True)
    assert rig.switcher.state.status == FAILED and "network down" in rig.switcher.state.error
    assert ("install", EN) not in rig.loader.calls and rig.committed == []
    assert rig.cfg.model_repo == OLD and rig.loader.installed == "model:" + OLD
    assert rig.failed == [(EN, OLD, True)]  # the caller restores a hand-edited file


def test_a_failed_load_keeps_the_old_model():
    rig = Rig()
    rig.loader.fail_load = ValueError("bad weights")
    rig.run(MULTI)
    assert rig.switcher.state.status == FAILED
    assert rig.loader.installed == "model:" + OLD and rig.committed == []
    assert rig.failed == [(MULTI, OLD, False)]


def test_a_new_request_after_a_failure_clears_the_error():
    rig = Rig()
    rig.loader.fail_load = ValueError("bad weights")
    rig.run(MULTI)
    rig.loader.fail_load = None
    rig.run(EN)
    assert rig.switcher.state.status == IDLE and rig.switcher.state.error is None


def test_the_swap_waits_for_a_running_dictation():
    rig = Rig(busy=True)
    rig.switcher.request(EN)
    for _ in range(300):
        if rig.switcher.state.status == WAITING:
            break
        threading.Event().wait(0.01)
    assert rig.switcher.state.status == WAITING
    assert ("load", EN) in rig.loader.calls  # loading happened while the old model served
    assert ("install", EN) not in rig.loader.calls and rig.committed == []
    rig.busy = False  # the dictation ended
    rig.switcher.join(5)
    assert rig.committed == [(EN, None)] and rig.switcher.state.status == IDLE


def test_the_swap_does_not_install_while_a_decode_holds_the_lock():
    rig = Rig()
    rig.lock.acquire()  # a pass is decoding with the old model
    try:
        rig.switcher.request(EN)
        for _ in range(100):
            if ("load", EN) in rig.loader.calls:
                break
            threading.Event().wait(0.01)
        threading.Event().wait(0.1)
        assert ("install", EN) not in rig.loader.calls
    finally:
        rig.lock.release()
    rig.switcher.join(5)
    assert ("install", EN) in rig.loader.calls


@pytest.mark.parametrize(
    ("repo", "language", "expected"),
    [
        (EN, "fr", "en"),  # an English-only model forces English
        (EN, "en", None),  # already English: nothing to change
        (MULTI, "fr", None),  # multilingual keeps the language
    ],
)
def test_language_follows_english_only_models(repo, language, expected):
    rig = Rig(language=language)
    rig.run(repo)
    assert rig.committed == [(repo, expected)]
    assert rig.cfg.language == (expected or language)


def test_asking_for_the_current_model_does_nothing():
    rig = Rig()
    rig.switcher.request(OLD)
    assert rig.loader.calls == [] and rig.switcher.state.status == IDLE


def test_a_newer_request_supersedes_a_loading_one():
    rig = Rig()
    rig.loader.gate.clear()  # the first load hangs
    rig.switcher.request(MULTI)
    for _ in range(300):
        if ("load", MULTI) in rig.loader.calls:
            break
        threading.Event().wait(0.01)
    first = rig.switcher._thread
    rig.loader.gate.set()
    rig.switcher.request(EN)
    first.join(5)
    rig.switcher.join(5)
    assert rig.committed == [(EN, None)]
    assert ("install", MULTI) not in rig.loader.calls


def test_going_back_to_the_current_model_cancels_a_pending_switch():
    rig = Rig()
    rig.loader.gate.clear()
    rig.switcher.request(MULTI)
    for _ in range(300):
        if ("load", MULTI) in rig.loader.calls:
            break
        threading.Event().wait(0.01)
    first = rig.switcher._thread
    rig.switcher.request(OLD)  # the user changed their mind
    rig.loader.gate.set()
    first.join(5)
    assert rig.committed == [] and rig.switcher.state.status == IDLE


def test_a_failing_warm_up_does_not_undo_the_swap():
    rig = Rig()
    rig.loader.warm_up_fails = True
    rig.run(EN)
    assert rig.committed == [(EN, None)] and rig.switcher.state.status == IDLE


def test_progress_class_counts_only_byte_bars():
    seen = []
    cls = modelswitch._progress_class(seen.append)
    with cls(total=100, unit="B") as bar:
        bar.update(40)
    with cls(total=3, unit="it") as bar:
        bar.update(1)
    assert seen == [40]


# ---- the first load of a session ------------------------------------------------------


def test_the_first_load_runs_in_the_background_and_does_not_rewrite_the_config():
    rig = Rig()
    rig.loader.gate.clear()
    rig.switcher.start_initial(OLD)
    for _ in range(300):
        if ("load", OLD) in rig.loader.calls:
            break
        threading.Event().wait(0.01)
    assert rig.switcher.ready is False  # still loading: dictation must wait
    assert rig.switcher.state.status == LOADING and rig.switcher.state.target == OLD
    rig.loader.gate.set()
    rig.switcher.join(5)
    assert rig.switcher.ready is True and rig.switcher.state.status == IDLE
    assert ("install", OLD) in rig.loader.calls and ("warm_up",) in rig.loader.calls
    assert rig.committed == []  # the configured model was already the live value


def test_a_failed_first_download_stays_not_ready_with_the_error_and_can_be_retried():
    rig = Rig()
    rig.loader.fail_download = OSError("offline")
    rig.switcher.start_initial(OLD)
    rig.switcher.join(5)
    assert rig.switcher.ready is False
    assert rig.switcher.state.status == FAILED and "offline" in rig.switcher.state.error
    assert rig.failed == [(OLD, OLD, False)]
    rig.loader.fail_download = None
    rig.switcher.request(OLD)  # choosing the configured model again retries
    rig.switcher.join(5)
    assert rig.switcher.ready is True and rig.switcher.state.status == IDLE


def test_choosing_another_model_after_a_failed_first_load_makes_the_app_ready():
    rig = Rig()
    rig.loader.fail_download = OSError("offline")
    rig.switcher.start_initial(OLD)
    rig.switcher.join(5)
    rig.loader.fail_download = None
    rig.run(EN)
    assert rig.switcher.ready is True and rig.committed == [(EN, None)]


def test_a_failed_switch_later_does_not_make_a_running_app_not_ready():
    rig = Rig()
    rig.loader.fail_load = ValueError("bad weights")
    rig.run(MULTI)
    assert rig.switcher.ready is True  # the old model keeps serving


def test_a_switcher_built_for_a_background_load_starts_not_ready():
    rig = Rig()
    pending = ModelSwitcher(
        rig.cfg, rig.loader, rig.lock, lambda: False, lambda *a: None, ready=False
    )
    assert pending.ready is False and rig.switcher.ready is True


def test_the_log_for_a_failed_first_load_says_how_to_retry(caplog):
    rig = Rig()
    rig.loader.fail_download = OSError("offline")
    with caplog.at_level("ERROR", logger="murmur"):
        rig.switcher.start_initial(OLD)
        rig.switcher.join(5)
    assert "Could not load model" in caplog.text and "choose the model again" in caplog.text
    assert "keeping" not in caplog.text  # there is no old model to keep
