"""SSE response construction and disconnect cleanup."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..services.event_bus import EventBus, Event


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

    def _on_event(event: Event) -> None:
        try:
            queue.put_nowait(event)
        except Exception:  # pragma: no cover - queue full / closed
            pass

    async def event_generator():
        # Replay buffered history so a reconnecting client is consistent.
        for ev in event_bus.replay(task_id):
            yield sse_format(ev)
        event_bus.subscribe(task_id, _on_event)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                    yield sse_format(ev)
                except asyncio.TimeoutError:
                    yield sse_format({"type": "heartbeat", "data": {}, "ts": time.time()})
        finally:
            event_bus.unsubscribe(task_id, _on_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
