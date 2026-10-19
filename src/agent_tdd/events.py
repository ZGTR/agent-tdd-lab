"""Generic pub/sub and an at-least-once outbox delivery contract."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from agent_tdd.types import JsonObject


@dataclass(frozen=True)
class Event:
    id: str
    topic: str
    payload: JsonObject


class Outbox(Protocol):
    def pending(self, limit: int) -> list[Event]: ...
    def acknowledge(self, event_id: str) -> None: ...


class Publisher(Protocol):
    def publish(self, event: Event) -> None: ...


Subscriber = Callable[[Event], None]


class EventBus:
    """Configure at startup. Each subscriber must deduplicate event IDs."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = {}

    def subscribe(self, topic: str, subscriber: Subscriber) -> None:
        subscribers = self._subscribers.setdefault(topic, [])
        if subscriber not in subscribers:
            subscribers.append(subscriber)

    def publish(self, event: Event) -> None:
        subscribers = tuple(self._subscribers.get(event.topic, ()))
        if not subscribers:
            raise RuntimeError(f"no subscriber for {event.topic}")
        for subscriber in subscribers:
            subscriber(deepcopy(event))


class OutboxDispatcher:
    def __init__(self, outbox: Outbox, publisher: Publisher) -> None:
        self._outbox = outbox
        self._publisher = publisher

    def dispatch(self, *, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("dispatch limit must be an integer in [1, 1000]")
        count = 0
        for event in self._outbox.pending(limit):
            self._publisher.publish(event)
            self._outbox.acknowledge(event.id)
            count += 1
        return count
