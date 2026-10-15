from unittest import mock

import numpy as np

from whisper_dictation import __main__ as cli


class FakeStream:
    def __init__(self, data, **kwargs):
        self.data = data
        self.callback = kwargs["callback"]

    def __enter__(self):
        self.callback(self.data, len(self.data), None, None)
        return self

    def __exit__(self, *exc):
        return False


def run_check(stream_factory, caplog):
    with (
        mock.patch("sounddevice.InputStream", stream_factory),
        mock.patch.object(cli.time, "sleep"),
        caplog.at_level("WARNING", logger="whisper_dictation"),
    ):
        cli.check_microphone()


def test_silent_microphone_warns_with_binary_path(caplog):
    run_check(lambda **kw: FakeStream(np.zeros((160, 1), dtype=np.float32), **kw), caplog)
    assert "only silence" in caplog.text
    assert str(cli.real_python(cli.Path(cli.sys.executable))) in caplog.text


def test_live_microphone_is_quiet(caplog):
    run_check(lambda **kw: FakeStream(np.full((160, 1), 0.01, dtype=np.float32), **kw), caplog)
    assert caplog.text == ""


def test_microphone_open_failure_warns(caplog):
    def boom(**kw):
        raise OSError("no device")

    run_check(boom, caplog)
    assert "Could not open the microphone" in caplog.text
