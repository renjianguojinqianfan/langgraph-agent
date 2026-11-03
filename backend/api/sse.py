"""SSE response construction and disconnect cleanup."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..services.event_bus import DEFAULT_BUFFER_SIZE, Event, EventBus
from ..utils.logging import get_logger

logger = get_logger("api.sse")

#: Bound on the per-connection outbox (#260). Publishers hand events to the
#: loop thread from the task's worker thread, and that hand-off used to queue
#: without limit: a slow consumer (backgrounded tab, stalled proxy) let the
#: heap grow for as long as the task ran. On overflow the OLDEST queued event
#: is dropped — the stream stays near-live. Derived from the bus's replay
#: buffer so the two caps cannot drift.
#:
#: Recovery boundary, stated honestly: a reconnect replays the bus buffer's
#: most recent events, so an event dropped from the outbox is recoverable only
#: while it is still inside that window. Once it has also been evicted from
#: the buffer it is gone by design — the alternative (unbounded growth) is
#: exactly what this bound removes. Drops are logged, never silent: an
#: observability stream losing data must be visible.
SSE_QUEUE_MAX = DEFAULT_BUFFER_SIZE


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

    The outbox between the bus and this stream is bounded at
    :data:`SSE_QUEUE_MAX`; overflow drops the oldest queued event (policy and
    rationale on the constant).
    """
    queue: "asyncio.Queue[Event]" = asyncio.Queue(maxsize=SSE_QUEUE_MAX)
    dropped = 0

    async def event_generator():
        loop = asyncio.get_running_loop()

        def enqueue(event: Event) -> None:
            # Runs on the loop thread (call_soon_threadsafe), so puts and the
            # generator's get are serialised and cannot interleave.
            nonlocal dropped
            try:
                queue.put_nowait(event)
                return
            except asyncio.QueueFull:
                pass
            dropped += 1
            if dropped == 1:
                logger.warning(
                    "sse outbox for task %s overflowed (cap %d); dropping oldest "
                    "queued events — a reconnect replays the bus buffer's window",
                    task_id,
                    SSE_QUEUE_MAX,
                )
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - consumer drained it first
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - the loop serialises puts
                pass

        def on_event(event: Event) -> None:
            # Publishers run on the task's worker thread and asyncio.Queue is
            # not thread-safe: hand the event back to the loop explicitly,
            # otherwise delivery waits for the loop's next wake-up (at worst
            # the 15s heartbeat timeout).
            try:
                loop.call_soon_threadsafe(enqueue, event)
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
            if dropped:
                # End-of-stream summary: the per-drop warning fires once, so a
                # long backlog would otherwise leave no trace of the loss.
                logger.warning(
                    "sse stream for task %s closed after dropping %d event(s)",
                    task_id,
                    dropped,
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
