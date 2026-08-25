"""Tests for the in-process pub/sub :class:`EventBus`."""

from __future__ import annotations

from backend.services.event_bus import EventBus


def test_subscriber_receives_published_events():
    bus = EventBus()
    received = []
    bus.subscribe("t1", received.append)
    bus.publish("t1", "step_start", {"index": 1})
    bus.publish("t1", "tool_call", {"name": "file_io"})
    assert len(received) == 2
    assert received[0]["type"] == "step_start"
    assert received[1]["data"]["name"] == "file_io"


def test_subscribers_are_scoped_per_task():
    bus = EventBus()
    a, b = [], []
    bus.subscribe("t1", a.append)
    bus.subscribe("t2", b.append)
    bus.publish("t1", "x", {})
    assert len(a) == 1 and len(b) == 0


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received = []
    bus.subscribe("t1", received.append)
    bus.publish("t1", "x", {})
    bus.unsubscribe("t1", received.append)
    bus.publish("t1", "y", {})
    assert len(received) == 1


def test_replay_returns_buffered_events_for_late_subscriber():
    """A new subscriber can replay events published before it connected."""
    bus = EventBus()
    bus.publish("t1", "a", {"n": 1})
    bus.publish("t1", "b", {"n": 2})
    history = bus.replay("t1")
    assert [e["type"] for e in history] == ["a", "b"]
    assert history[0]["data"]["n"] == 1


def test_replay_is_empty_for_unknown_task():
    bus = EventBus()
    assert bus.replay("never") == []


def test_event_has_type_data_and_timestamp():
    bus = EventBus()
    ev = bus.publish("t1", "heartbeat", {})
    assert ev["type"] == "heartbeat"
    assert ev["data"] == {}
    assert "ts" in ev


def test_bad_subscriber_does_not_break_publisher():
    bus = EventBus()

    def broken(event):
        raise RuntimeError("subscriber boom")

    received = []
    bus.subscribe("t1", broken)
    bus.subscribe("t1", received.append)
    bus.publish("t1", "ok", {})  # should not raise despite broken subscriber
    assert len(received) == 1
