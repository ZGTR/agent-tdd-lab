"""Scoped cooperative shutdown. Only CLI host code installs process signal handlers."""

import signal
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event
from types import FrameType


@contextmanager
def cancellation_signals(cancelled: Event) -> Iterator[None]:
    def cancel(signum: int, frame: FrameType | None) -> None:
        cancelled.set()

    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in previous:
            signal.signal(sig, cancel)
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
