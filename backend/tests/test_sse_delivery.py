"""Regression tests for SSE event delivery ordering (#200).

The graph publishes events from a worker thread while the SSE generator lives
on the event-loop thread, so a publish must wake the stream immediately. A late
subscriber must also not lose events published while the replay buffer is
being flushed.

#260 adds the bounded outbox: a slow consumer must not grow the heap, and the
overflow (drop-oldest) must be visible in the log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import AsyncGenerator, cast

from starlette.requests import Request

from backend.api import sse
from backend.api.sse import sse_response
from backend.services.event_bus import EventBus


class _LiveRequest:
    async def is_disconnected(self) -> bool:
        return False


def _frame_type(chunk: str) -> str:
    return chunk.split("\n", 1)[0].removeprefix("event: ")


def _frame_payload(chunk: str) -> dict:
    return cast(dict, json.loads(chunk.split("data: ", 1)[1].strip()))


def _stream(bus: EventBus) -> AsyncGenerator[str, None]:
    response = sse_response("t1", bus, cast(Request, _LiveRequest()))
    return cast(AsyncGenerator[str, None], response.body_iterator)


def test_worker_thread_publish_reaches_sse_stream_promptly():
    async def scenario() -> tuple[float, str]:
        bus = EventBus()
        agen = _stream(bus)

        def publish() -> None:
            time.sleep(0.05)
            bus.publish("t1", "step_start", {"i": 1})

        threading.Thread(target=publish, daemon=True).start()
        started = time.perf_counter()
        try:
            chunk = await asyncio.wait_for(agen.__anext__(), timeout=3.0)
        finally:
            await agen.aclose()
        return time.perf_counter() - started, chunk

    latency, chunk = asyncio.run(scenario())
    assert _frame_type(chunk) == "step_start"
    assert latency < 1.5, f"worker-thread publish starved the SSE stream for {latency:.2f}s"


def test_event_published_during_replay_is_neither_lost_nor_duplicated():
    async def scenario() -> list[str]:
        bus = EventBus()
        for i in range(50):
            bus.publish("t1", "step_start", {"i": i})
        agen = _stream(bus)
        frames: list[str] = []
        try:
            frames.append(await agen.__anext__())
            bus.publish("t1", "tool_call", {"name": "write"})
            for _ in range(49):
                frames.append(await agen.__anext__())
            while True:
                try:
                    frames.append(await asyncio.wait_for(agen.__anext__(), timeout=0.3))
                except asyncio.TimeoutError:
                    break
        finally:
            await agen.aclose()
        return [_frame_type(frame) for frame in frames]

    types = asyncio.run(scenario())
    assert types[:50] == ["step_start"] * 50
    assert types[50:] == ["tool_call"]


# ── #260: bounded outbox ────────────────────────────────────────
async def _overflow_scenario() -> list[int]:
    """Publish 5 events into a 3-slot outbox before the consumer reads."""
    bus = EventBus()
    agen = _stream(bus)
    # Start the generator so the subscription is live; the first __anext__
    # then blocks on the (still empty) queue.
    pending = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.01)
    for i in range(5):
        bus.publish("t1", "step_start", {"i": i})
    await asyncio.sleep(0.05)  # let the loop run the scheduled puts
    frames = [await asyncio.wait_for(pending, timeout=1.0)]
    for _ in range(2):
        frames.append(await asyncio.wait_for(agen.__anext__(), timeout=1.0))
    await agen.aclose()
    return [_frame_payload(frame)["i"] for frame in frames]


def test_slow_consumer_overflow_drops_oldest_and_stays_live(
    caplog, monkeypatch, app_log_capture
):
    """#260 AC1: the outbox is bounded; overflow drops the OLDEST queued event.

    Policy: with a slow consumer the stream keeps the newest events (it stays
    near-live), and the loss is logged — an observability stream never drops
    silently. Recovery boundary: a reconnect replays the bus buffer's window;
    events evicted from both are gone by design (see SSE_QUEUE_MAX).
    """
    app_log_capture("api.sse")
    monkeypatch.setattr(sse, "SSE_QUEUE_MAX", 3)
    with caplog.at_level(logging.WARNING):
        delivered = asyncio.run(_overflow_scenario())

    assert delivered == [2, 3, 4]
    assert "sse outbox for task t1 overflowed" in caplog.text
    assert "dropping oldest" in caplog.text
    assert "closed after dropping 2 event(s)" in caplog.text


def test_prompt_consumer_never_drops(caplog, app_log_capture):
    """The bound only bites on a real backlog: no drop, no warning."""
    app_log_capture("api.sse")

    async def scenario() -> list[int]:
        bus = EventBus()
        agen = _stream(bus)
        delivered: list[int] = []
        try:
            for i in range(4):
                bus.publish("t1", "step_start", {"i": i})
                frame = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
                delivered.append(_frame_payload(frame)["i"])
        finally:
            await agen.aclose()
        return delivered

    with caplog.at_level(logging.WARNING):
        delivered = asyncio.run(scenario())

    assert delivered == [0, 1, 2, 3]
    assert "overflowed" not in caplog.text
    assert "dropping" not in caplog.text
