from pynput.keyboard import Key, KeyCode

from whisper_dictation.hotkey import HotkeyListener


def make(option_keys="both"):
    events = []
    h = HotkeyListener(lambda: events.append("press"), lambda: events.append("release"),
                       lambda: events.append("other"), option_keys)
    return h, events


def test_press_release_cycle():
    h, ev = make()
    h.handle_press(Key.alt_l)
    h.handle_release(Key.alt_l)
    assert ev == ["press", "release"]


def test_other_key_only_while_held():
    h, ev = make()
    h.handle_press(KeyCode.from_char("a"))
    h.handle_press(Key.alt_r)
    h.handle_press(KeyCode.from_char("a"))
    assert ev == ["press", "other"]


def test_both_options_count_as_one_press():
    h, ev = make()
    h.handle_press(Key.alt_l)
    h.handle_press(Key.alt_r)
    h.handle_release(Key.alt_l)
    h.handle_release(Key.alt_r)
    assert ev == ["press", "release"]


def test_right_only_ignores_left():
    h, ev = make("right")
    h.handle_press(Key.alt_l)
    h.handle_release(Key.alt_l)
    assert ev == []
