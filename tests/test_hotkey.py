import threading

from pynput.keyboard import Key, KeyCode

from murmur.config import OPTION_KEYS
from murmur.hotkey import OPTION_KEY_SETS, HotkeyListener

A = KeyCode.from_char("a")


def make(option_keys="both"):
    events = []
    h = HotkeyListener(
        lambda: events.append("press"),
        lambda: events.append("release"),
        lambda: events.append("other"),
        option_keys,
    )
    return h, events


def test_press_release_cycle():
    h, ev = make()
    h.handle_press(Key.alt_l)
    h.handle_release(Key.alt_l)
    assert ev == ["press", "release"]


def test_other_key_only_counts_while_option_is_held():
    h, ev = make()
    h.handle_press(A)
    h.handle_press(Key.alt_r)
    h.handle_press(A)
    assert ev == ["press", "other"]


def test_injected_keys_do_not_cancel():
    h, ev = make()
    h.handle_press(Key.alt_l)
    h.handle_press(A, injected=True)
    assert ev == ["press"]
    h.handle_press(A, injected=False)
    assert ev == ["press", "other"]


def test_both_options_count_as_one_press():
    h, ev = make()
    h.handle_press(Key.alt_l)
    h.handle_press(Key.alt_r)
    h.handle_release(Key.alt_l)
    assert ev == ["press"]  # still held through the right key
    h.handle_release(Key.alt_r)
    assert ev == ["press", "release"]


def test_left_only_accepts_left_and_generic_option():
    h, ev = make("left")
    h.handle_press(Key.alt_r)
    h.handle_release(Key.alt_r)
    assert ev == []
    h.handle_press(Key.alt)
    h.handle_release(Key.alt)
    assert ev == ["press", "release"]


def test_right_only_ignores_left():
    h, ev = make("right")
    h.handle_press(Key.alt_l)
    h.handle_release(Key.alt_l)
    assert ev == []
    h.handle_press(Key.alt_r)
    assert ev == ["press"]


def test_release_of_a_key_that_was_not_pressed_is_ignored():
    h, ev = make()
    h.handle_release(Key.alt_l)
    assert ev == []


def start_worker(h):
    """Same wiring as start(), without a real pynput listener."""
    h._dispatch = h._queue.put
    t = threading.Thread(target=h._worker, daemon=True)
    t.start()
    return t


def test_callbacks_run_off_the_listener_thread_without_blocking_it():
    gate, ran_on, started = threading.Event(), [], threading.Event()

    def slow_press():
        ran_on.append(threading.current_thread())
        started.set()
        gate.wait(5)

    h = HotkeyListener(slow_press, lambda: None, lambda: None)
    worker = start_worker(h)
    h.handle_press(Key.alt_l)  # returns at once although the callback blocks
    assert started.wait(2)
    assert ran_on[0] is worker
    gate.set()
    h._queue.put(None)
    worker.join(2)


def test_a_failing_callback_does_not_kill_the_worker(caplog):
    seen = threading.Event()

    def bad_press():
        raise RuntimeError("callback failed")

    h = HotkeyListener(bad_press, seen.set, lambda: None)
    worker = start_worker(h)
    with caplog.at_level("ERROR", logger="murmur"):
        h.handle_press(Key.alt_l)
        h.handle_release(Key.alt_l)
        assert seen.wait(2)  # the release callback still ran
    assert "Hotkey callback failed" in caplog.text
    h._queue.put(None)
    worker.join(2)


def test_every_valid_option_keys_setting_has_a_key_set():
    assert set(OPTION_KEY_SETS) == set(OPTION_KEYS)
