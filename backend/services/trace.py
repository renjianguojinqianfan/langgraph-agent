"""Persistent observable trace recording (P0 item 4).

:class:`TraceRecorder` is a **resident EventBus subscriber**: every event
published for a task is appended as a JSON line
``{"type": ..., "data": ..., "ts": ...}`` to
``<trace_dir>/<task_id>.jsonl``, mirroring the SSE stream for each run era
(same order, same payloads) so a run can be replayed later for audit /
debugging. Resume/continue reattach *before* ``task_resumed`` is published,
so that boundary event is captured; the replay snapshot returned by
``subscribe`` is dropped so the previous era is not written twice.

The recorder never modifies :class:`~backend.services.event_bus.EventBus` —
it is simply another fan-out subscriber, so the existing SSE push logic stays
untouched. Lifecycle:

* :meth:`attach` — called from ``TaskManager.create_task`` *before* any event
  is published (so ``task_created`` is captured); resume/continue reattach
  before ``task_resumed`` is published (so the boundary event is captured);
* :meth:`close` — called from ``TaskManager.run`` in a ``finally`` block; it
  appends a final ``trace_end`` line, unsubscribes, and closes the file handle
  (covers success / failure / interrupt alike).

Concurrency: all file access is guarded by a lock; writes use ``append +
flush`` so the on-disk order matches the publish order. ``attach`` registers
its callback outside the lock — the bus is never called while holding it —
and removes it again if a concurrent :meth:`close` won the race.

Write failures are never silent (#260): :class:`EventBus` swallows subscriber
exceptions by contract ("a bad subscriber must not break the publisher"), so a
disk-full or I/O-error trace would otherwise lose every event with no signal.
The recorder counts the loss per task (:attr:`TraceRecorder.write_failures`),
logs the first failure with its traceback, and logs one end-of-era summary in
:meth:`close` — a disk full for the whole run costs exactly two log lines.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import IO, Callable, Dict

from ..config import Settings
from ..utils.logging import get_logger
from .event_bus import Event, EventBus

logger = get_logger("trace")


class TraceRecorder:
    """Append-only JSONL recorder bound to an :class:`EventBus`."""

    def __init__(self, settings: Settings) -> None:
        self._dir: Path = settings.trace_path
        self._dir.mkdir(parents=True, exist_ok=True)
        self._files: Dict[str, IO[str]] = {}  # task_id -> open file handle
        self._cbs: Dict[str, Callable[[Event], None]] = {}
        self._buses: Dict[str, EventBus] = {}
        self._lock = threading.Lock()
        #: Per-task count of events lost to write failures (#260) — the
        #: in-process observable signal that a trace is incomplete. Mutated
        #: under ``self._lock``; popped by :meth:`close` when the era ends.
        self.write_failures: Dict[str, int] = {}

    def attach(self, event_bus: EventBus, task_id: str) -> None:
        """Subscribe to ``event_bus`` for ``task_id`` and open its JSONL file.

        A concurrent :meth:`close` may win the race; the stale subscription is
        then removed before returning, so no callback outlives the closed era.
        """
        with self._lock:
            if task_id in self._files:
                return  # already attached (idempotent)
            fh = (self._dir / f"{task_id}.jsonl").open("a", encoding="utf-8")
            self._files[task_id] = fh
            self._buses[task_id] = event_bus

            def _cb(event: Event, _tid: str = task_id) -> None:
                self._on_event(_tid, event)

            self._cbs[task_id] = _cb
        # Subscribe outside the lock: close() deliberately never calls the bus
        # while holding it, and keeping that rule avoids a new trace -> bus
        # nesting. Snapshot dropped: reattaching before task_resumed keeps the
        # boundary event live, while replaying the snapshot would duplicate
        # prior eras.
        event_bus.subscribe(task_id, _cb)
        # close() or a newer era's attach may have won while we subscribed; a
        # callback registered after that point is stale and must not stay on
        # the bus (it would double-write into the next era's trace).
        with self._lock:
            stale = self._cbs.get(task_id) is not _cb
        if stale:
            event_bus.unsubscribe(task_id, _cb)

    def _on_event(self, task_id: str, event: Event) -> None:
        with self._lock:
            fh = self._files.get(task_id)
            if fh is None:
                return
            line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
            try:
                fh.write(line)
                fh.flush()
            except OSError:
                # A failed write must be visible (#260): the bus swallows
                # subscriber exceptions by contract, so without this the
                # recorder would lose events in total silence. Count the loss
                # and log the FIRST failure per task with the traceback; later
                # ones only bump the counter, so a disk-full run cannot flood
                # the log with one line per event.
                first = self.write_failures.get(task_id, 0) == 0
                self.write_failures[task_id] = self.write_failures.get(task_id, 0) + 1
                if first:
                    logger.exception(
                        "trace write failed for %s; events are being lost from "
                        "this task's trace — close() will log the total",
                        task_id,
                    )

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
                except OSError:
                    logger.exception(
                        "trace_end write failed for %s; the JSONL has no end marker",
                        task_id,
                    )
                finally:
                    fh.close()
            # End-of-era summary (#260): the per-event warning fires once, so a
            # run that failed from the first write would otherwise leave no
            # end-of-run trace of the loss.
            lost = self.write_failures.pop(task_id, 0)
            if lost:
                logger.error(
                    "trace for %s lost %d event(s) to write failures; the JSONL "
                    "is incomplete",
                    task_id,
                    lost,
                )
        # Unsubscribe outside the lock to avoid any deadlock risk.
        if bus is not None and cb is not None:
            bus.unsubscribe(task_id, cb)

    def file_path(self, task_id: str) -> Path:
        """Absolute path of ``task_id``'s trace file (may not exist)."""
        return self._dir / f"{task_id}.jsonl"
