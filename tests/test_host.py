"""Which parts make_indicator builds, and how Host fans state out."""

from pathlib import Path

from murmur.config import Config
from murmur.host import Host, make_indicator
from murmur.indicator import NullIndicator, Overlay
from murmur.statusitem import StatusItem

PATH = Path("/tmp/does-not-matter.toml")


def kinds(indicator):
    return [type(p) for p in indicator.parts]


def test_overlay_only_is_the_default():
    ind = make_indicator(Config(), PATH)
    assert isinstance(ind, Host) and kinds(ind) == [Overlay]
    assert ind.parts[0].position == "top-center"


def test_overlay_position_comes_from_the_config():
    assert make_indicator(Config(indicator_position="bottom-left"), PATH).parts[0].position == (
        "bottom-left"
    )


def test_icon_next_to_the_pill():
    ind = make_indicator(Config(menubar_icon=True), PATH)
    assert kinds(ind) == [Overlay, StatusItem]


def test_icon_only_with_indicator_none():
    ind = make_indicator(Config(indicator="none", menubar_icon=True), PATH)
    assert kinds(ind) == [StatusItem]


def test_nothing_visible_gives_the_null_indicator():
    assert isinstance(make_indicator(Config(indicator="none"), PATH), NullIndicator)


def test_the_restart_callback_reaches_the_status_item():
    def restart():
        pass

    item = make_indicator(Config(indicator="none", menubar_icon=True), PATH, restart).parts[0]
    assert item.on_restart is restart and item.config_path == PATH


def test_host_fans_state_out_to_every_part():
    class Part:
        def __init__(self):
            self.states = []

        def build(self):
            pass

        def set(self, state):
            self.states.append(state)

    a, b = Part(), Part()
    host = Host([a, b])
    host.set("recording")
    host.set("idle")
    assert a.states == b.states == ["recording", "idle"]
