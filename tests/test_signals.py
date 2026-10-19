import signal
from threading import Event

import pytest

from agent_tdd.signals import cancellation_signals


def test_signal_scope_cancels_and_restores_handlers():
    cancelled = Event()
    original = signal.getsignal(signal.SIGTERM)
    with cancellation_signals(cancelled):
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert cancelled.is_set()
    assert signal.getsignal(signal.SIGTERM) == original


def test_handlers_are_restored_after_exception():
    original = signal.getsignal(signal.SIGINT)
    with pytest.raises(RuntimeError), cancellation_signals(Event()):
        raise RuntimeError("run failed")
    assert signal.getsignal(signal.SIGINT) == original
