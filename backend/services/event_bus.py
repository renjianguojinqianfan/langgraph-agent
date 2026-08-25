"""Process-internal publish/subscribe event bus.

Used to fan out step/task events from the orchestration kernel to any number of
SSE subscribers. A bounded per-task buffer lets late SSE subscribers replay
recent events so they do not miss anything published before they connected.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List

Event = Dict[str, Any]


class EventBus:
    """Tiny thread-safe pub/sub bus scoped per task id."""

    def __init__(self, buffer_size: int = 1000) -> None:
        self._subs: Dict[str, set] = defaultdict(set)
        self._buffers: Dict[str, "deque[Event]"] = defaultdict(lambda: deque(maxlen=buffer_size))
        self._lock = threading.Lock()

    def subscribe(self, task_id: str, callback: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs[task_id].add(callback)

    def unsubscribe(self, task_id: str, callback: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs[task_id].discard(callback)

    def publish(self, task_id: str, event_type: str, data: Dict[str, Any]) -> Event:
        event: Event = {"type": event_type, "data": data, "ts": time.time()}
        with self._lock:
            self._buffers[task_id].append(event)
            callbacks = list(self._subs[task_id])
        for cb in callbacks:
            try:
                cb(event)
            except Exception:  # a bad subscriber must not break the publisher
                pass
        return event

    def replay(self, task_id: str) -> List[Event]:
        with self._lock:
            return list(self._buffers[task_id])
