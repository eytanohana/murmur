import time

from murmur.watchdog import watchdog


def test_logs_thread_stacks_when_a_step_is_slow(caplog):
    with caplog.at_level("ERROR", logger="murmur"), watchdog("slow step", 0.05):
        time.sleep(0.2)
    assert "slow step has taken over" in caplog.text
    assert "thread MainThread" in caplog.text


def test_quiet_when_the_step_is_fast(caplog):
    with caplog.at_level("ERROR", logger="murmur"), watchdog("fast", 0.5):
        pass
    time.sleep(0.6)  # the timer was cancelled, so nothing may fire later
    assert caplog.text == ""
