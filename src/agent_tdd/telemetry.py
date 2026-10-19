"""Metadata-only execution events with exception-isolated, synchronous observers."""

import json
import logging
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Protocol


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    application: str
    phase: str
    elapsed_seconds: float
    model_calls: int = 0
    status: str | None = None


class Observer(Protocol):
    def observe(self, event: RunEvent) -> None: ...


class NullObserver:
    def observe(self, event: RunEvent) -> None:
        pass


class LoggingObserver:
    """Inject an application-owned logger; no global logging configuration or raw payloads."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def observe(self, event: RunEvent) -> None:
        self._logger.info(json.dumps(asdict(event), sort_keys=True, allow_nan=False))


class ResilientObserver:
    """Observer failures are counted, never used as permission checks or run outcomes."""

    def __init__(self, observer: Observer) -> None:
        self._observer = observer
        self._failures = 0
        self._lock = Lock()

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def observe(self, event: RunEvent) -> None:
        try:
            self._observer.observe(event)
        except Exception:
            with self._lock:
                self._failures += 1
