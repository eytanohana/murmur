import threading
import time
from unittest import mock

import pytest
from pynput.keyboard import Key, KeyCode

from murmur import inserter
from murmur.hotkey import HotkeyListener
from murmur.watchdog import watchdog


def test_utf16_chunks_respect_limit_and_pairs():
    chunks = inserter.utf16_chunks("a" * 45)
    assert [len(c) for c in chunks] == [20, 20, 5]
    emoji = inserter.utf16_chunks("\U0001f600" * 11)
    assert all(len(c.encode("utf-16-le")) // 2 <= 20 for c in emoji)
    assert "".join(emoji) == "\U0001f600" * 11
    assert inserter.utf16_chunks("") == []


def test_post_text_clears_flags_and_chunks():
    calls = []
    with (
        mock.patch.object(inserter, "_source", return_value="src"),
        mock.patch.object(
            inserter,
            "_post",
            side_effect=lambda src, kc, down, flags, text=None: calls.append(
                (kc, down, flags, text)
            ),
        ),
        mock.patch.object(inserter.time, "sleep"),
    ):
        posted = inserter.post_text("x" * 25)
    assert posted == 4
    assert all(flags == 0 for _, _, flags, _ in calls)
    assert [c[3] for c in calls] == ["x" * 20, "x" * 20, "x" * 5, "x" * 5]
    assert [c[1] for c in calls] == [True, False, True, False]


def test_paste_sends_only_command_flag():
    calls = []
    with (
        mock.patch.object(inserter, "_source", return_value="src"),
        mock.patch.object(
            inserter,
            "_post",
            side_effect=lambda src, kc, down, flags, text=None: calls.append((kc, down, flags)),
        ),
    ):
        inserter.post_cmd_v()
    assert calls == [
        (9, True, inserter.Quartz.kCGEventFlagMaskCommand),
        (9, False, inserter.Quartz.kCGEventFlagMaskCommand),
    ]


def test_type_text_warns_once_without_accessibility(caplog):
    ins = inserter.Inserter()
    with (
        mock.patch.object(inserter, "accessibility_trusted", return_value=False),
        mock.patch.object(inserter, "post_text", return_value=2),
        caplog.at_level("INFO", logger="murmur"),
    ):
        ins.type_text("hi")
        ins.type_text("again")
    assert caplog.text.count("Accessibility is not granted") == 1
    assert "insert (type) 'hi'" in caplog.text


def test_injected_keys_do_not_cancel():
    events = []
    h = HotkeyListener(
        lambda: events.append("press"),
        lambda: events.append("release"),
        lambda: events.append("other"),
    )
    h.handle_press(Key.alt_l)
    h.handle_press(KeyCode.from_char("a"), injected=True)
    h.handle_press(KeyCode.from_char("a"), injected=False)
    assert events == ["press", "other"]


def test_callbacks_run_off_the_listener_thread_and_do_not_block_it():
    gate = threading.Event()
    ran_on = []

    def slow_press():
        ran_on.append(threading.current_thread())
        gate.wait(5)

    h = HotkeyListener(slow_press, lambda: None, lambda: None)
    # Same wiring as start(), without a real pynput listener.
    h._dispatch = h._queue.put
    threading.Thread(target=h._worker, daemon=True).start()
    t0 = time.monotonic()
    h.handle_press(Key.alt_l)  # must return immediately although the callback blocks
    assert time.monotonic() - t0 < 0.5
    time.sleep(0.1)
    assert ran_on and ran_on[0] is not threading.current_thread()
    gate.set()
    h._queue.put(None)


def test_watchdog_logs_when_step_is_slow(caplog):
    with caplog.at_level("ERROR", logger="murmur"), watchdog("slow step", 0.05):
        time.sleep(0.2)
    assert "slow step has taken over" in caplog.text
    assert "thread MainThread" in caplog.text


def test_watchdog_quiet_when_fast(caplog):
    with caplog.at_level("ERROR", logger="murmur"), watchdog("fast", 0.5):
        pass
    assert caplog.text == ""


@pytest.mark.parametrize("text", ["", None])
def test_empty_insert_does_nothing(text):
    with mock.patch.object(inserter, "post_text") as p:
        inserter.Inserter().type_text(text or "")
    p.assert_not_called()
