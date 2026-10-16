"""The DMG layout settings and the app Info.plist values, checked without building."""

import re
import runpy
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def dmg_settings():
    scope = {
        "defines": {
            "app": "dist/murmur.app",
            "background": "build/dmg-background.png",
            "icon": "build/murmur.icns",
        }
    }
    return runpy.run_path(str(ROOT / "packaging" / "dmg_settings.py"), init_globals=scope)


def test_the_dmg_has_the_app_on_the_left_and_an_applications_alias_on_the_right():
    s = dmg_settings()
    assert s["files"] == ["dist/murmur.app"] and s["symlinks"] == {"Applications": "/Applications"}
    app_x, app_y = s["icon_locations"]["murmur.app"]
    apps_x, apps_y = s["icon_locations"]["Applications"]
    assert app_x < apps_x and app_y == apps_y
    width, height = s["window_rect"][1]
    assert 0 < app_x < apps_x < width and 0 < app_y < height


def test_the_dmg_window_matches_the_background_image_size():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_assets", ROOT / "scripts" / "make_assets.py"
    )
    text = (ROOT / "scripts" / "make_assets.py").read_text()
    match = re.search(r"DMG_SIZE = \((\d+), (\d+)\)", text)
    assert spec is not None and match
    assert tuple(map(int, match.groups())) == dmg_settings()["window_rect"][1]


def test_the_bundle_declares_the_values_the_app_depends_on():
    spec = (ROOT / "packaging" / "murmur.spec").read_text()
    assert 'bundle_identifier="com.eytanohana.murmur"' in spec
    assert '"LSUIElement": True' in spec  # no Dock icon
    assert "NSMicrophoneUsageDescription" in spec
    assert chr(0x2014) not in spec


def test_the_minimum_macos_comes_from_the_installed_mlx_wheel():
    import sys

    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        from build_info import mlx_min_macos
    finally:
        sys.path.pop(0)
    from importlib.metadata import distribution

    tag = re.search(r"macosx_(\d+)_", distribution("mlx").read_text("WHEEL"))
    assert mlx_min_macos() == f"{tag.group(1)}.0"
    assert "mlx_min_macos()" in (ROOT / "packaging" / "murmur.spec").read_text()


def test_the_minimum_macos_falls_back_when_the_wheel_has_no_tag(monkeypatch):
    import sys

    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        import build_info
    finally:
        sys.path.pop(0)

    class Dist:
        def read_text(self, name):
            return "Wheel-Version: 1.0\n"

    monkeypatch.setattr(build_info, "distribution", lambda name: Dist())
    assert build_info.mlx_min_macos() == "15.0"
    assert build_info.mlx_min_macos("14.0") == "14.0"


def test_the_runtime_dependencies_stay_light():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    runtime = " ".join(data["project"]["dependencies"]).lower()
    assert "pyinstaller" not in runtime and "dmgbuild" not in runtime
    build = " ".join(data["dependency-groups"]["build"]).lower()
    assert "pyinstaller" in build and "dmgbuild" in build
