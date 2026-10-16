"""The uninstall plan, its safety checks and the operations, with fakes and temp directories."""

import itertools
import os
import plistlib
import subprocess
from pathlib import Path

import pytest

from murmur import launchlogin, models, uninstall
from murmur.models import CachedModel
from murmur.uninstall import (
    DELETE,
    DEV_AGENT,
    LOGIN_OFF,
    TCC_RESET,
    TRASH,
    Choices,
    Context,
    SystemOperations,
)

HOME = Path("/Users/someone")
BUNDLE = Path("/Applications/murmur.app")


def make_ctx(**kw):
    defaults = {
        "frozen": True,
        "bundle": BUNDLE,
        "config_dir": HOME / ".config" / "murmur",
        "log_file": HOME / "Library" / "Logs" / "murmur.log",
        "dev_agent_installed": False,
        "models": (
            CachedModel("a/small", "small.en", HOME / "hub" / "models--a--small", 481_000_000),
            CachedModel("a/tiny", "tiny.en", HOME / "hub" / "models--a--tiny", 74_000_000),
        ),
    }
    defaults.update(kw)
    return Context(**defaults)


def kinds(plan):
    return [s.kind for s in plan]


# ---- availability and the plan -----------------------------------------------------------------------


def test_the_item_is_hidden_outside_the_app():
    assert uninstall.available(make_ctx()) is True
    assert uninstall.available(make_ctx(frozen=False)) is False
    assert uninstall.available(make_ctx(bundle=None)) is False
    assert uninstall.build_plan(make_ctx(frozen=False), Choices()) == []  # nothing to trash
    assert uninstall.build_plan(make_ctx(bundle=None), Choices()) == []


@pytest.mark.parametrize(
    ("data", "models_", "agent"), list(itertools.product([False, True], repeat=3))
)
def test_every_checkbox_combination_gives_an_ordered_plan(data, models_, agent):
    ctx = make_ctx(dev_agent_installed=agent)
    plan = uninstall.build_plan(ctx, Choices(delete_data=data, delete_models=models_))
    k = kinds(plan)
    assert k[0] == LOGIN_OFF and k[-1] == TRASH  # the Trash step is always last
    assert k.count(TCC_RESET) == 1 and k.index(TCC_RESET) == len(k) - 2
    assert (DEV_AGENT in k) is agent
    if agent:
        assert k[1] == DEV_AGENT  # booted out first: its KeepAlive would bring the app back
    deleted = {s.path for s in plan if s.kind == DELETE}
    assert (ctx.config_dir in deleted and ctx.log_file in deleted) is data
    assert (deleted & {m.path for m in ctx.models} != set()) is models_
    assert plan[-1].path == BUNDLE


def test_models_are_off_by_default_and_data_is_on():
    c = Choices()
    assert (c.delete_data, c.delete_models) == (True, False)
    deleted = {s.path for s in uninstall.build_plan(make_ctx(), c) if s.kind == DELETE}
    assert make_ctx().config_dir in deleted
    assert not deleted & {m.path for m in make_ctx().models}


# ---- path safety --------------------------------------------------------------------------------------------


def test_only_the_known_paths_may_be_deleted():
    ctx = make_ctx()
    allowed = uninstall.allowed_delete_paths(ctx)
    assert uninstall.safe_delete_target(ctx.config_dir, allowed) is None
    assert uninstall.safe_delete_target(ctx.log_file, allowed) is None
    assert uninstall.safe_delete_target(ctx.models[0].path, allowed) is None


@pytest.mark.parametrize(
    "path",
    [
        Path("/"),
        HOME,
        HOME / ".config",  # the parent of the config dir
        HOME / "hub",  # the whole cache is never allowed
        HOME / ".config" / "murmur" / "config.toml",  # a child is not listed either
        HOME / ".config" / "murmur" / ".." / "other",
        Path("relative/murmur"),
        HOME / "hub" / "models--a--*",  # no globbing
        Path("/Applications/murmur.app"),
    ],
)
def test_anything_else_is_refused(path):
    assert (
        uninstall.safe_delete_target(path, uninstall.allowed_delete_paths(make_ctx())) is not None
    )


def test_execute_refuses_a_delete_step_that_was_not_planned_from_known_paths():
    ctx = make_ctx()
    ops = FakeOps()
    bad = [uninstall.Step(DELETE, "bad", HOME / "Documents")]
    results = uninstall.execute(bad, ops, ctx)
    assert results[0].ok is False and "not one of murmur's own paths" in results[0].message
    assert ops.calls == []


def test_the_trash_target_must_be_the_running_murmur_app():
    ok = uninstall.check_trash_target(BUNDLE, BUNDLE, lambda p: uninstall.BUNDLE_ID)
    assert ok is None
    other = Path("/Applications/Safari.app")
    assert "not the running app" in uninstall.check_trash_target(other, BUNDLE, lambda p: "x")
    assert "not the running app" in uninstall.check_trash_target(BUNDLE, None, lambda p: "x")
    notapp = Path("/Applications/murmur")
    assert "not an .app" in uninstall.check_trash_target(
        notapp, notapp, lambda p: uninstall.BUNDLE_ID
    )
    assert "bundle id" in uninstall.check_trash_target(BUNDLE, BUNDLE, lambda p: "com.other.app")


def test_bundle_id_is_read_from_info_plist(tmp_path):
    app = tmp_path / "murmur.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "x.y"}))
    assert uninstall.bundle_id_of(app) == "x.y"
    assert uninstall.bundle_id_of(tmp_path / "missing.app") is None


# ---- failure isolation --------------------------------------------------------------------------------------


class FakeOps:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def _do(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise OSError(f"{name} failed")
        return f"{name} done"

    def login_off(self):
        return self._do("login_off")

    def remove_dev_agent(self):
        return self._do("remove_dev_agent")

    def delete_path(self, path):
        return self._do("delete_path", path)

    def reset_permissions(self):
        return self._do("reset_permissions")

    def trash(self, path):
        return self._do("trash", path)


def test_a_failed_step_never_stops_the_rest():
    ctx = make_ctx(dev_agent_installed=True)
    plan = uninstall.build_plan(ctx, Choices(delete_data=True, delete_models=True))
    ops = FakeOps(fail={"login_off", "delete_path", "reset_permissions"})
    seen = []
    results = uninstall.execute(plan, ops, ctx, on_result=seen.append)
    assert len(results) == len(plan) == len(seen)  # every step was attempted
    assert [name for name, *_ in ops.calls][-1] == "trash"
    assert all(not r.ok for r in results if r.step.kind in (LOGIN_OFF, DELETE, TCC_RESET))
    assert all(r.ok for r in results if r.step.kind in (DEV_AGENT, TRASH))
    assert uninstall.trash_failed(results) is None


def test_a_failed_trash_is_reported_so_the_user_can_drag_the_app_manually():
    ctx = make_ctx()
    results = uninstall.execute(uninstall.build_plan(ctx, Choices()), FakeOps(fail={"trash"}), ctx)
    failed = uninstall.trash_failed(results)
    assert failed is not None and failed.step.path == BUNDLE


def test_execute_with_an_empty_plan_does_nothing():
    ops = FakeOps()
    assert uninstall.execute([], ops, make_ctx()) == [] and ops.calls == []


# ---- the dialog text ----------------------------------------------------------------------------------------------


def test_the_dialog_lists_what_always_happens_and_what_is_optional():
    ctx = make_ctx(dev_agent_installed=True)
    text = uninstall.summary_text(ctx)
    assert "launch at login" in text and "com.user.murmur" in text
    assert "tccutil reset All com.eytanohana.murmur" in text and str(BUNDLE) in text
    assert "com.user.murmur" not in uninstall.summary_text(make_ctx())
    assert chr(0x2014) not in text


def test_the_models_option_has_sizes_a_total_and_the_shared_cache_warning():
    title, detail, enabled = uninstall.models_option(make_ctx())
    assert title == "Also delete downloaded models (555 MB)" and enabled is True
    assert "small.en 481 MB" in detail and "tiny.en 74 MB" in detail
    assert "shared with other tools" in detail and "never the whole cache" in detail
    _, none_detail, none_enabled = uninstall.models_option(make_ctx(models=()))
    assert "None of murmur's models" in none_detail and none_enabled is False


def test_the_data_option_names_the_paths_and_the_size(tmp_path):
    cfg, log_file = tmp_path / "murmur", tmp_path / "murmur.log"
    cfg.mkdir()
    (cfg / "config.toml").write_text("x" * 2000)
    log_file.write_text("y" * 3000)
    title, detail = uninstall.data_option(make_ctx(config_dir=cfg, log_file=log_file))
    assert title == "Also delete my settings and log"
    assert str(cfg) in detail and str(log_file) in detail and "5 KB" in detail


# ---- the model cache scope --------------------------------------------------------------------------------------------


def test_only_the_curated_models_are_listed_with_sizes_and_symlinks_are_not_followed(tmp_path):
    def folder(name, size):
        root = tmp_path / name
        (root / "blobs").mkdir(parents=True)
        (root / "blobs" / "b").write_bytes(b"x" * size)
        (root / "snapshots" / "abc").mkdir(parents=True)
        os.symlink(root / "blobs" / "b", root / "snapshots" / "abc" / "weights.npz")
        return root

    folder("models--mlx-community--whisper-tiny.en-mlx", 100)
    folder("models--someone--private-model", 5000)  # not murmur's: must never be listed
    (tmp_path / "datasets--x--y").mkdir()
    found = models.curated_cache(tmp_path)
    assert [m.repo for m in found] == ["mlx-community/whisper-tiny.en-mlx"]
    assert found[0].size_bytes == 100  # the blob once, not the symlink to it


def test_a_symlinked_cache_folder_is_not_listed(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, tmp_path / "models--mlx-community--whisper-base.en-mlx")
    assert models.curated_cache(tmp_path) == []


# ---- the real operations in a temp HOME ---------------------------------------------------------------------------


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".config" / "murmur"
    config.mkdir(parents=True)
    (config / "config.toml").write_text("x = 1")
    (home / ".config" / "other-tool").mkdir()
    (home / ".config" / "other-tool" / "keep.txt").write_text("keep")
    logs = home / "Library" / "Logs"
    logs.mkdir(parents=True)
    (logs / "murmur.log").write_text("log")
    (logs / "other.log").write_text("keep")
    hub = home / ".cache" / "huggingface" / "hub"
    for name in (
        "models--mlx-community--whisper-tiny.en-mlx",
        "models--mlx-community--whisper-base.en-mlx",
        "models--someone--other",
    ):
        (hub / name / "blobs").mkdir(parents=True)
        (hub / name / "blobs" / "b").write_bytes(b"x" * 10)
    app = tmp_path / "Applications" / "murmur.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": uninstall.BUNDLE_ID})
    )
    monkeypatch.setattr(
        launchlogin, "MainAppService", lambda: (_ for _ in ()).throw(ImportError("fake"))
    )
    return home, hub, app


def real_ops(home, hub, app, trashed, commands, dev_agent=False):
    ctx = Context(
        frozen=True,
        bundle=app,
        config_dir=home / ".config" / "murmur",
        log_file=home / "Library" / "Logs" / "murmur.log",
        dev_agent_installed=dev_agent,
        models=tuple(models.curated_cache(hub)),
    )

    def fake_run(cmd, **kw):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ops = SystemOperations(
        ctx, home=home, run=fake_run, trash_fn=trashed.append, agent_runner=lambda c: fake_run(c)
    )
    return ctx, ops


def test_a_temp_home_run_removes_exactly_the_chosen_things(temp_home):
    home, hub, app = temp_home
    trashed, commands = [], []
    ctx, ops = real_ops(home, hub, app, trashed, commands)
    results = uninstall.execute(uninstall.build_plan(ctx, Choices(True, False)), ops, ctx)
    assert all(r.ok for r in results), [r.message for r in results if not r.ok]
    assert (
        not (home / ".config" / "murmur").exists()
        and not (home / "Library/Logs/murmur.log").exists()
    )
    assert (home / ".config" / "other-tool" / "keep.txt").read_text() == "keep"
    assert (home / "Library/Logs/other.log").exists()
    assert len(list(hub.iterdir())) == 3  # models stay unless chosen
    assert commands == [["tccutil", "reset", "All", "com.eytanohana.murmur"]]
    assert trashed == [app]


def test_choosing_models_removes_only_murmurs_cache_folders(temp_home):
    home, hub, app = temp_home
    ctx, ops = real_ops(home, hub, app, [], [])
    uninstall.execute(uninstall.build_plan(ctx, Choices(False, True)), ops, ctx)
    assert [p.name for p in hub.iterdir()] == ["models--someone--other"]
    assert (home / ".config" / "murmur" / "config.toml").exists()  # data kept: box was off


def test_the_login_item_and_its_fallback_plist_are_removed_and_the_dev_agent_is_booted_out(
    temp_home,
):
    home, hub, app = temp_home
    fallback = launchlogin.fallback_plist_path(home)
    fallback.parent.mkdir(parents=True)
    fallback.write_text("<plist/>")
    dev = home / "Library" / "LaunchAgents" / "com.user.murmur.plist"
    dev.write_text("<plist/>")
    trashed, commands = [], []
    ctx, ops = real_ops(home, hub, app, trashed, commands, dev_agent=True)
    uninstall.execute(uninstall.build_plan(ctx, Choices(False, False)), ops, ctx)
    assert not fallback.exists() and not dev.exists()
    assert any(c[:2] == ["launchctl", "bootout"] for c in commands)  # through the fake runner


def test_delete_path_never_follows_a_symlink_out_of_the_known_path(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    cfg = tmp_path / "home" / ".config" / "murmur"
    cfg.parent.mkdir(parents=True)
    os.symlink(outside, cfg)  # the known path is a link to somewhere else
    ctx = make_ctx(config_dir=cfg, log_file=tmp_path / "home" / "murmur.log", models=())
    SystemOperations(ctx, home=tmp_path / "home").delete_path(cfg)
    assert not cfg.exists() and (outside / "precious").read_text() == "keep"


def test_a_link_inside_the_config_folder_is_not_followed_either(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    cfg = tmp_path / "home" / ".config" / "murmur"
    cfg.mkdir(parents=True)
    os.symlink(outside, cfg / "link")
    ctx = make_ctx(config_dir=cfg, log_file=tmp_path / "home" / "murmur.log", models=())
    SystemOperations(ctx, home=tmp_path / "home").delete_path(cfg)
    assert not cfg.exists() and (outside / "precious").read_text() == "keep"


def test_trash_is_refused_for_a_non_writable_location_and_for_other_apps(tmp_path):
    app = tmp_path / "ro" / "murmur.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": uninstall.BUNDLE_ID})
    )
    ctx = make_ctx(bundle=app)
    trashed = []
    ops = SystemOperations(ctx, trash_fn=trashed.append)
    os.chmod(app.parent, 0o500)
    try:
        if os.access(app.parent, os.W_OK):  # running as root: the check cannot be shown
            pytest.skip("directory stays writable for this user")
        with pytest.raises(PermissionError, match="not writable"):
            ops.trash(app)
    finally:
        os.chmod(app.parent, 0o700)
    with pytest.raises(PermissionError, match="not the running app"):
        ops.trash(tmp_path / "Safari.app")
    assert trashed == []


def test_a_failed_tccutil_is_a_failed_step_not_a_crash(temp_home):
    home, _hub, app = temp_home
    ctx = make_ctx(
        bundle=app,
        config_dir=home / ".config" / "murmur",
        log_file=home / "Library/Logs/murmur.log",
        models=(),
    )
    ops = SystemOperations(
        ctx,
        home=home,
        run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "no such service"),
    )
    with pytest.raises(RuntimeError, match="tccutil exited 1"):
        ops.reset_permissions()
