"""Which parts make_indicator builds, and how Host fans state out."""

from pathlib import Path

from murmur.config import Config
from murmur.host import Host, make_indicator
from murmur.indicator import Overlay
from murmur.settings import LiveSettings
from murmur.statusitem import StatusItem

PATH = Path("/tmp/does-not-matter.toml")


def live(cfg):
    return LiveSettings(PATH, cfg, stat=lambda p: None)


def switcher():
    from murmur.modelswitch import ModelSwitcher

    return ModelSwitcher(Config(), None, None, lambda: False, lambda *a: None)


def kinds(indicator):
    return [type(p) for p in indicator.parts]


def test_overlay_only_is_the_default():
    ind = make_indicator(live(Config()))
    assert isinstance(ind, Host) and kinds(ind) == [Overlay]
    assert ind.parts[0].cfg.indicator_position == "top-center"


def test_overlay_position_comes_from_the_config():
    assert make_indicator(live(Config(indicator_position="bottom-left"))).parts[
        0
    ].cfg.indicator_position == ("bottom-left")


def test_icon_next_to_the_pill_when_the_pill_is_on():
    ind = make_indicator(live(Config(menubar_icon=True)), switcher=switcher())
    assert kinds(ind) == [Overlay, StatusItem]


def test_icon_only_with_indicator_none():
    ind = make_indicator(live(Config(indicator="none", menubar_icon=True)), switcher=switcher())
    assert kinds(ind) == [StatusItem]


def test_the_status_item_is_always_built_so_the_icon_can_be_switched_on_later():
    ind = make_indicator(live(Config(indicator="none")), switcher=switcher())
    assert kinds(ind) == [StatusItem]
    assert isinstance(ind, Host) and ind.window is not None


def test_without_a_switcher_there_is_no_status_item_or_window():
    ind = make_indicator(live(Config(indicator="none")))
    assert isinstance(ind, Host) and ind.parts == [] and ind.window is None


def test_menubar_icon_is_wired_to_the_status_item_as_a_live_hook():
    lv = live(Config(indicator="none"))
    ind = make_indicator(lv, switcher=switcher())
    assert lv.key_hooks["menubar_icon"] == ind.parts[0].enable


def test_the_restart_callback_reaches_the_status_item():
    def restart():
        pass

    item = make_indicator(
        live(Config(indicator="none", menubar_icon=True)), restart, switcher()
    ).parts[0]
    assert item.on_restart is restart and item.live.path == PATH


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
