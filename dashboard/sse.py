"""Server-Sent Events helper for real-time streaming to HTMX frontend."""

from __future__ import annotations

import queue


class MessageAnnouncer:
    """Fan-out message queue for SSE listeners."""

    def __init__(self):
        self.listeners: list[queue.Queue] = []

    def listen(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=10)
        self.listeners.append(q)
        return q

    def announce(self, msg: str) -> None:
        for i in reversed(range(len(self.listeners))):
            try:
                self.listeners[i].put_nowait(msg)
            except queue.Full:
                del self.listeners[i]


def format_sse(data: str, event: str | None = None) -> str:
    """Format a message as an SSE frame."""
    msg = f"data: {data}\n\n"
    if event:
        msg = f"event: {event}\n{msg}"
    return msg
