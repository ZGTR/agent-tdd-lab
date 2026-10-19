import pytest

from agent_tdd.events import Event, EventBus, OutboxDispatcher


class MemoryOutbox:
    def __init__(self):
        self.items = [Event("1", "topic", {"x": 1})]

    def pending(self, limit):
        return self.items[:limit]

    def acknowledge(self, event_id):
        self.items = [event for event in self.items if event.id != event_id]


def test_subscriber_failure_keeps_event_for_replay():
    outbox = MemoryOutbox()
    bus = EventBus()
    seen = []

    def broken(event):
        seen.append(event.id)
        raise RuntimeError("subscriber unavailable")

    bus.subscribe("topic", broken)
    bus.subscribe("topic", broken)  # registration is idempotent
    with pytest.raises(RuntimeError):
        OutboxDispatcher(outbox, bus).dispatch()
    assert outbox.items
    assert seen == ["1"]
    recovered = EventBus()
    recovered.subscribe("topic", lambda event: seen.append(event.id))
    assert OutboxDispatcher(outbox, recovered).dispatch() == 1
    assert seen == ["1", "1"]  # at least once, not exactly once
    assert outbox.items == []


def test_no_subscribers_is_not_silent_loss():
    with pytest.raises(RuntimeError, match="no subscriber"):
        OutboxDispatcher(MemoryOutbox(), EventBus()).dispatch()
    with pytest.raises(ValueError):
        OutboxDispatcher(MemoryOutbox(), EventBus()).dispatch(limit=0)


def test_subscribers_receive_independent_payloads():
    bus = EventBus()

    def mutate(event):
        event.payload["x"] = 999

    seen = []
    bus.subscribe("topic", mutate)
    bus.subscribe("topic", lambda event: seen.append(event.payload["x"]))
    event = Event("1", "topic", {"x": 1})
    bus.publish(event)
    assert seen == [1]
    assert event.payload == {"x": 1}
