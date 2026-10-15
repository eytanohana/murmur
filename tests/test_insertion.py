from unittest import mock

from murmur import inserter


def test_utf16_chunks_respect_the_limit_and_surrogate_pairs():
    assert [len(c) for c in inserter.utf16_chunks("a" * 45)] == [20, 20, 5]
    emoji = inserter.utf16_chunks("\U0001f600" * 11)
    assert all(len(c.encode("utf-16-le")) // 2 <= 20 for c in emoji)
    assert "".join(emoji) == "\U0001f600" * 11
    assert inserter.utf16_chunks("") == []


def capture_posts():
    calls = []
    return calls, mock.patch.object(
        inserter,
        "_post",
        side_effect=lambda src, kc, down, flags, text=None: calls.append((kc, down, flags, text)),
    )


def test_post_text_chunks_text_and_clears_modifier_flags():
    calls, post = capture_posts()
    with mock.patch.object(inserter, "_source"), post, mock.patch.object(inserter.time, "sleep"):
        posted = inserter.post_text("x" * 25)
    assert posted == 4
    assert all(flags == 0 for _, _, flags, _ in calls)
    assert [c[3] for c in calls] == ["x" * 20, "x" * 20, "x" * 5, "x" * 5]
    assert [c[1] for c in calls] == [True, False, True, False]


def test_post_text_sends_return_for_a_newline():
    calls, post = capture_posts()
    with mock.patch.object(inserter, "_source"), post, mock.patch.object(inserter.time, "sleep"):
        inserter.post_text("\n")
    assert [(c[0], c[3]) for c in calls] == [(inserter.KEYCODE_RETURN, None)] * 2


def test_paste_shortcut_carries_only_the_command_flag():
    calls, post = capture_posts()
    with mock.patch.object(inserter, "_source"), post:
        inserter.post_cmd_v()
    command = inserter.Quartz.kCGEventFlagMaskCommand
    assert [(c[0], c[1], c[2]) for c in calls] == [(9, True, command), (9, False, command)]


def test_post_applies_the_flags_to_the_event():
    quartz = inserter.Quartz
    with (
        mock.patch.object(quartz, "CGEventCreateKeyboardEvent", return_value="ev") as create,
        mock.patch.object(quartz, "CGEventKeyboardSetUnicodeString") as set_text,
        mock.patch.object(quartz, "CGEventSetFlags") as set_flags,
        mock.patch.object(quartz, "CGEventPost") as post,
    ):
        inserter._post("src", 0, True, 0, "hé")
    create.assert_called_once_with("src", 0, True)
    set_text.assert_called_once_with("ev", 2, "hé")
    set_flags.assert_called_once_with("ev", 0)
    post.assert_called_once_with(quartz.kCGHIDEventTap, "ev")


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


def test_empty_text_posts_nothing():
    with mock.patch.object(inserter, "post_text") as post:
        inserter.Inserter().type_text("")
        inserter.Inserter("type").insert("")
    post.assert_not_called()


def test_insert_dispatches_on_mode():
    with (
        mock.patch.object(inserter, "accessibility_trusted", return_value=True),
        mock.patch.object(inserter, "post_text", return_value=2) as typed,
        mock.patch.object(inserter, "post_cmd_v") as pasted,
        mock.patch.object(inserter, "get_clipboard", return_value=b"old"),
        mock.patch.object(inserter, "set_clipboard"),
        mock.patch.object(inserter.time, "sleep"),
    ):
        inserter.Inserter("type").insert("hi")
        typed.assert_called_once_with("hi")
        pasted.assert_not_called()
        inserter.Inserter("paste").insert("yo")
        pasted.assert_called_once()
        typed.assert_called_once()  # paste mode does not type


def test_paste_sets_the_transcript_then_restores_the_previous_clipboard():
    order = []
    with (
        mock.patch.object(inserter, "accessibility_trusted", return_value=True),
        mock.patch.object(inserter, "get_clipboard", return_value=b"previous"),
        mock.patch.object(
            inserter, "set_clipboard", side_effect=lambda d: order.append(("set", d))
        ),
        mock.patch.object(inserter, "post_cmd_v", side_effect=lambda: order.append(("paste",))),
        mock.patch.object(inserter.time, "sleep"),
    ):
        inserter.Inserter("paste").insert("héllo")
    assert order == [("set", "héllo".encode()), ("paste",), ("set", b"previous")]
