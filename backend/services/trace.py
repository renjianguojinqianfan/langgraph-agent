"""Persistent observable trace recording (P0 item 4).

:class:`TraceRecorder` is a **resident EventBus subscriber**: every event
published for a task is appended as a JSON line
``{"type": ..., "data": ..., "ts": ...}`` to
``<trace_dir>/<task_id>.jsonl``, mirroring the SSE stream exactly (same order,
same payloads) so a run can be replayed later for audit / debugging.

The recorder never modifies :class:`~backend.services.event_bus.EventBus` —
it is simply another fan-out subscriber, so the existing SSE push logic stays
untouched. Lifecycle:

* :meth:`attach` — called from ``TaskManager.create_task`` *before* any event
  is published (so ``task_created`` is captured);
* :meth:`close` — called from ``TaskManager.run`` in a ``finally`` block; it
  appends a final ``trace_end`` line, unsubscribes, and closes the file handle
  (covers success / failure / interrupt alike).

Concurrency: all file access is guarded by a lock; writes use ``append +
flush`` so the on-disk order matches the publish order.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from ..config import Settings
from ..utils.logging import get_logger
from .event_bus import Event, EventBus

logger = get_logger("trace")


class TraceRecorder:
    """Append-only JSONL recorder bound to an :class:`EventBus`."""

    def __init__(self, settings: Settings) -> None:
        self._dir: Path = settings.trace_path
        self._dir.mkdir(parents=True, exist_ok=True)
        self._files: Dict[str, object] = {}  # task_id -> open file handle
        self._cbs: Dict[str, Callable[[Event], None]] = {}
        self._buses: Dict[str, EventBus] = {}
        self._lock = threading.Lock()

    def attach(self, event_bus: EventBus, task_id: str) -> None:
        """Subscribe to ``event_bus`` for ``task_id`` and open its JSONL file."""
        with self._lock:
            if task_id in self._files:
                return  # already attached (idempotent)
            fh = (self._dir / f"{task_id}.jsonl").open("a", encoding="utf-8")
            self._files[task_id] = fh
            self._buses[task_id] = event_bus

            def _cb(event: Event, _tid: str = task_id) -> None:
                self._on_event(_tid, event)

            self._cbs[task_id] = _cb
        event_bus.subscribe(task_id, self._cbs[task_id])

    def _on_event(self, task_id: str, event: Event) -> None:
        with self._lock:
            fh = self._files.get(task_id)
            if fh is None:
                return
            line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
            fh.write(line)
            fh.flush()

    def close(self, task_id: str) -> None:
        """Write ``trace_end``, unsubscribe, and close the file handle."""
        with self._lock:
            fh = self._files.pop(task_id, None)
            cb = self._cbs.pop(task_id, None)
            bus = self._buses.pop(task_id, None)
            if fh is not None:
                end_event: Event = {"type": "trace_end", "data": {}, "ts": time.time()}
                try:
                    fh.write(json.dumps(end_event, ensure_ascii=False, default=str) + "\n")
                    fh.flush()
                finally:
                    fh.close()
        # Unsubscribe outside the lock to avoid any deadlock risk.
        if bus is not None and cb is not None:
            bus.unsubscribe(task_id, cb)

    def file_path(self, task_id: str) -> Path:
        """Absolute path of ``task_id``'s trace file (may not exist)."""
        return self._dir / f"{task_id}.jsonl"
