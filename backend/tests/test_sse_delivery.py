"""Regression tests for SSE event delivery ordering (#200).

The graph publishes events from a worker thread while the SSE generator lives
on the event-loop thread, so a publish must wake the stream immediately. A late
subscriber must also not lose events published while the replay buffer is
being flushed.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import AsyncGenerator, cast

from starlette.requests import Request

from backend.api.sse import sse_response
from backend.services.event_bus import EventBus


class _LiveRequest:
    async def is_disconnected(self) -> bool:
        return False


def _frame_type(chunk: str) -> str:
    return chunk.split("\n", 1)[0].removeprefix("event: ")


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
