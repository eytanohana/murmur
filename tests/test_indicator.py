from murmur.indicator import NullIndicator, Overlay, make_indicator, overlay_origin

FRAME = (0.0, 0.0, 1800.0, 1169.0)
VISIBLE = (0.0, 76.0, 1800.0, 1056.0)  # Dock at the bottom, menu bar and notch at the top
SIZE = (78.0, 26.0)


def test_top_center_sits_below_the_menu_bar():
    x, y = overlay_origin(FRAME, VISIBLE, SIZE, "top-center", margin=6)
    assert x == (1800 - 78) / 2
    assert y == 76 + 1056 - 26 - 6


def test_corner_positions():
    assert overlay_origin(FRAME, VISIBLE, SIZE, "top-left", 6)[0] == 6
    assert overlay_origin(FRAME, VISIBLE, SIZE, "top-right", 6)[0] == 1800 - 78 - 6
    assert overlay_origin(FRAME, VISIBLE, SIZE, "bottom-left", 6) == (6, 76 + 6)
    assert overlay_origin(FRAME, VISIBLE, SIZE, "bottom-center", 6)[1] == 82


def test_make_indicator_kinds():
    overlay = make_indicator("overlay", "top-left")
    assert isinstance(overlay, Overlay) and overlay.position == "top-left"
    assert isinstance(make_indicator("none"), NullIndicator)


def test_overlay_set_before_run_is_ignored():
    Overlay().set("recording")  # no panel yet; must not raise or touch AppKit
