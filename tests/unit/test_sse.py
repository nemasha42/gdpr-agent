"""Tests for SSE helper."""


def test_format_sse_data_only():
    from dashboard.sse import format_sse
    result = format_sse("hello")
    assert result == "data: hello\n\n"


def test_format_sse_with_event():
    from dashboard.sse import format_sse
    result = format_sse("42%", event="progress")
    assert result == "event: progress\ndata: 42%\n\n"


def test_announcer_delivers_to_listener():
    from dashboard.sse import MessageAnnouncer, format_sse
    ann = MessageAnnouncer()
    q = ann.listen()
    ann.announce(format_sse("test"))
    msg = q.get_nowait()
    assert "data: test" in msg


def test_announcer_drops_full_queue():
    from dashboard.sse import MessageAnnouncer, format_sse
    ann = MessageAnnouncer()
    q = ann.listen()
    for i in range(20):
        ann.announce(format_sse(str(i)))
    assert len(ann.listeners) == 0
