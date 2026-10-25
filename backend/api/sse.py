"""SSE response construction and disconnect cleanup."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..services.event_bus import Event, EventBus


def sse_format(event: Event) -> str:
    """Serialise an event dict into an SSE frame."""
    return (
        f"event: {event['type']}\n"
        f"data: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
    )


def sse_response(task_id: str, event_bus: EventBus, request: Request) -> StreamingResponse:
    """Return a streaming SSE response for ``task_id``.

    Late subscribers first receive a replay of buffered events, then live
    events as they arrive. A heartbeat frame is emitted every 15s to keep the
    connection alive and to let proxies detect disconnects.
    """
    queue: "asyncio.Queue[Event]" = asyncio.Queue()

    async def event_generator():
        loop = asyncio.get_running_loop()

        def on_event(event: Event) -> None:
            # Publishers run on the task's worker thread and asyncio.Queue is
            # not thread-safe: hand the event back to the loop explicitly,
            # otherwise delivery waits for the loop's next wake-up (at worst
            # the 15s heartbeat timeout).
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # pragma: no cover - loop closed after disconnect
                pass

        # Subscribe before replaying: the bus registers the callback and
        # snapshots its buffer atomically, so events published while history
        # is flushed are queued live instead of being lost in between.
        history = event_bus.subscribe(task_id, on_event)
        try:
            for ev in history:
                yield sse_format(ev)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                    yield sse_format(ev)
                except asyncio.TimeoutError:
                    yield sse_format({"type": "heartbeat", "data": {}, "ts": time.time()})
        finally:
            event_bus.unsubscribe(task_id, on_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
