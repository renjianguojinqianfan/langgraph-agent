"""Bypass run-pipeline recorder (issue #58).

One self-describing JSONL file per task: the **header** is a capability
manifest («件清单», one line per capability — deterministic, no volatile
fields, so two runs differ in exactly the lines whose capabilities differ),
the **body** records every LLM round (full request / response / usage /
elapsed) plus the tool events that make tool calls traceable end-to-end
(``tool_call`` / ``tool_result`` / ``tool_circuit_open``), each tagged with
its owner (``parent`` or ``subtask:<id>``).

Relationship to :class:`~backend.services.trace.TraceRecorder`: strictly
bypass. The trace mirrors the SSE stream and keeps its exact semantics; this
module is a *separate* subscriber writing a *separate* file under
``data/runs``. Nothing replaces the trace, no event semantics change.

Safety boundaries (#58 AC): request headers and any secret never reach the
disk — the LLM payload itself never carries auth headers, and every recorded
string passes :func:`redact_secrets` (configured key literals + common key
shapes). A per-task byte cap stops the file with one ``manifest_truncated``
marker line instead of growing unbounded.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import IO, Any, Callable, Dict, Iterator, List, Optional

from ..config import Settings
from ..core.agent.subagent import DEFAULT_SPLIT_SCENARIOS, subtask_owner
from ..core.llm.client import LLMClient, LLMResponse
from ..utils.logging import get_logger
from .event_bus import Event, EventBus

logger = get_logger("run_manifest")

# Event types mirrored into the body (owner-tagged). Everything else stays
# trace-only: these three are what "every tool call, with inputs and outputs"
# requires, plus the subtask circuit visibility ADR-0003 settled on this file.
MIRRORED_EVENT_TYPES = frozenset({"tool_call", "tool_result", "tool_circuit_open"})

# Common key shapes that must never hit the disk even inside message content.
_KEY_SHAPE_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")


def redact_secrets(value: Any, secrets: List[str]) -> Any:
    """Recursively mask secret-looking strings in ``value`` (returns a copy).

    Two layers: literal occurrences of the *configured* key values (api keys
    etc. — exact match, replaced whole), then generic key shapes inside longer
    text. Non-str values recurse into dicts / lists untouched.
    """
    if isinstance(value, str):
        for s in secrets:
            if s and s in value:
                value = value.replace(s, "***")
        return _KEY_SHAPE_RE.sub(lambda m: m.group(1)[:6] + "***", value)
    if isinstance(value, dict):
        return {k: redact_secrets(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v, secrets) for v in value]
    return value


def _secret_values(settings: Settings) -> List[str]:
    """The configured secret literals to mask anywhere in recorded content."""
    return [
        settings.llm_api_key,
        settings.aux_llm_api_key,
        settings.openapi_api_key,
        settings.serpapi_key,
        settings.auth_token,
    ]


def _capability_lines(
    settings: Settings,
    tool_names: List[str],
    confirm_enabled: bool,
    subagent_tool_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """The «件清单» — one deterministic line per capability.

    Diff contract: a single capability change (switch or key param) flips
    exactly one line. Never include task ids, timestamps or absolute paths —
    two runs of the same problem must produce byte-identical headers.
    """
    face = sorted(tool_names)
    subagent_face = sorted(subagent_tool_names or [])

    def in_face(name: str) -> bool:
        return name in face

    return [
        {
            "type": "capability",
            "name": "model",
            "enabled": True,
            "params": {
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "mock": settings.use_mock_llm,
                "request_timeout_sec": settings.llm_request_timeout_sec,
            },
        },
        {
            "type": "capability",
            "name": "planning",
            "enabled": True,
            "params": {"max_steps": settings.max_steps},
        },
        {
            "type": "capability",
            "name": "risk_scan",
            "enabled": settings.risk_scan_enabled,
            "params": {
                "semantic": settings.risk_semantic_enabled,
                "policy": settings.risk_policy,
            },
        },
        {
            "type": "capability",
            "name": "confirm_gate",
            "enabled": confirm_enabled,
            "params": {},
        },
        {
            "type": "capability",
            "name": "context_compress",
            "enabled": settings.context_token_budget > 0,
            "params": {
                "token_budget": settings.context_token_budget,
                "keep_recent": settings.context_keep_recent,
                "strategy": settings.context_compress_strategy,
            },
        },
        {
            "type": "capability",
            "name": "tool_result_eviction",
            "enabled": settings.context_evict_enabled,
            "params": {
                "threshold_chars": settings.context_evict_threshold_chars,
                "protect_recent": settings.context_evict_protect_recent,
            },
        },
        {
            "type": "capability",
            "name": "completion_verification",
            "enabled": settings.verify_enabled,
            "params": {"max_retries": settings.verify_max_retries},
        },
        {
            "type": "capability",
            "name": "circuit_breaker_and_retry",
            "enabled": True,
            "params": {
                "failure_threshold": settings.tool_failure_threshold,
                "cooldown_sec": settings.tool_cooldown_sec,
                "backoff_base": settings.tool_backoff_base,
                "backoff_factor": settings.tool_backoff_factor,
                "max_retries": settings.tool_max_retries,
            },
        },
        {
            "type": "capability",
            # Entry A = model-driven spawn_subagent tool; entry B = the
            # keyword auto-split graph node. ``face`` lists the tools a
            # subtask can actually reach (the sub-agent "档位" — today the
            # confirm-stripped face; #56's tiers will replace this list).
            # Entry B's truth is the scenario table itself, NOT
            # ``subagent_enabled``: when #56 deletes the table this line
            # flips to False — the disappearance must stay diffable.
            "name": "subagent",
            "enabled": settings.subagent_enabled,
            "params": {
                "entry_a_spawn": settings.subagent_enabled and in_face("spawn_subagent"),
                "entry_b_split": settings.subagent_enabled and bool(DEFAULT_SPLIT_SCENARIOS),
                "face": subagent_face,
                "max_concurrency": settings.subagent_max_concurrency,
                "timeout_sec": settings.subagent_timeout_sec,
            },
        },
        {
            "type": "capability",
            "name": "skills_runtime",
            "enabled": in_face("load_skill"),
            "params": {},
        },
        {
            "type": "capability",
            "name": "context_injection",
            "enabled": settings.context_inject_enabled,
            "params": {"skills_budget": settings.context_inject_skills_budget},
        },
        {
            "type": "capability",
            "name": "rollback_ledger",
            "enabled": settings.snapshot_enabled,
            "params": {"retention_days": settings.snapshot_retention_days},
        },
        {
            "type": "capability",
            "name": "knowledge_base",
            "enabled": settings.kb_enabled,
            "params": {
                "embedding": settings.kb_embedding_enabled,
                "top_k": settings.kb_top_k,
            },
        },
        {
            "type": "capability",
            # The actually-mounted tool face (MCP / Git / OpenAPI included by
            # name) — gate intersections are already reflected in the list.
            "name": "tool_face",
            "enabled": True,
            "params": {"tools": face},
        },
        {
            "type": "capability",
            "name": "aux_llm",
            "enabled": settings.aux_llm_enabled,
            "params": {"model": settings.aux_llm_model},
        },
        {
            "type": "capability",
            "name": "checkpoint_resume",
            "enabled": settings.checkpoint_enabled,
            "params": {},
        },
    ]


class ManifestLLMProxy(LLMClient):
    """Transparent :class:`LLMClient` wrapper that records every round.

    Carries both the **file** (``task_id`` — the parent task whose manifest
    file the round lands in) and the **owner label** (``parent`` or
    ``subtask:<id>`` — attribution shown in the line). The agent runtime
    calls ``complete()`` for every planner / executor / reflect round, so
    this is the single choke point where the full request (messages incl.
    injected blocks, tool schemas) and the response meet the file.
    ``stream()`` delegates unrecorded — the runtime never streams.
    """

    def __init__(
        self,
        inner: LLMClient,
        manifest: "RunManifest",
        task_id: str,
        owner: str,
        client_tag: str = "main",
    ) -> None:
        self._inner = inner
        self._manifest = manifest
        self._task_id = task_id
        self._owner = owner
        self._client_tag = client_tag

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        t0 = time.monotonic()
        resp = self._inner.complete(messages, tools, **kwargs)
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 1)
        self._manifest.record_llm(
            task_id=self._task_id,
            owner=self._owner,
            client_tag=self._client_tag,
            messages=messages,
            tools=tools,
            params=kwargs,
            response=resp,
            elapsed_ms=elapsed_ms,
        )
        return resp

    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        # Runtime never streams; delegate as-is (no recording, no alteration).
        return self._inner.stream(messages, tools, **kwargs)


class RunManifest:
    """Append-only JSONL run-pipeline recorder bound to an :class:`EventBus`.

    Lifecycle mirrors :class:`TraceRecorder` (attach on create/resume, close
    in a ``finally``), but writes to ``<run_manifest_path>/<task_id>.jsonl``
    and additionally mirrors subtask channels into the *parent's* file with
    an owner tag. All writes (bus callbacks, LLM proxies, subtask pool
    threads) funnel through one lock; the byte cap turns the file read-only
    after one ``manifest_truncated`` marker.
    """

    def __init__(
        self,
        settings: Settings,
        tool_names: List[str],
        confirm_enabled: bool = True,
        subagent_tool_names: Optional[List[str]] = None,
        max_bytes: Optional[int] = None,
    ) -> None:
        self._dir: Path = settings.run_manifest_path
        self._dir.mkdir(parents=True, exist_ok=True)
        self._secrets = [s for s in _secret_values(settings) if s]
        self._cap_lines = _capability_lines(
            settings, tool_names, confirm_enabled, subagent_tool_names
        )
        self._max_bytes = (
            max_bytes if max_bytes is not None else settings.run_manifest_max_mb * 1024 * 1024
        )
        self._files: Dict[str, IO[str]] = {}  # parent task_id -> handle
        self._bytes: Dict[str, int] = {}
        self._truncated: set[str] = set()
        # channel_id -> (parent_task_id, owner_label, callback, bus)
        self._channels: Dict[str, tuple[str, str, Callable[[Event], None], EventBus]] = {}
        self._lock = threading.Lock()

    # ── lifecycle ──
    def attach(self, event_bus: EventBus, task_id: str) -> None:
        """Open the file for ``task_id``, write the header, subscribe its channel."""
        with self._lock:
            if task_id in self._files:
                return  # already attached (idempotent)
            path = self._dir / f"{task_id}.jsonl"
            # Resume re-attaches in append mode: the byte cap keeps counting
            # what earlier eras already wrote, not zero (per-task, not per-era).
            self._bytes[task_id] = path.stat().st_size if path.exists() else 0
            fh = path.open("a", encoding="utf-8")
            self._files[task_id] = fh
            self._write(task_id, {"type": "manifest_begin", "task_id": task_id, "ts": time.time()})
            for line in self._cap_lines:
                self._write(task_id, line)
        # File is open, so the channel subscription below cannot be rejected.
        self._subscribe(event_bus, task_id, task_id, "parent")

    def attach_subtask(
        self, event_bus: EventBus, parent_task_id: str, subtask_id: str
    ) -> None:
        """Mirror ``subtask_id``'s bus channel into the parent's file (owner tag).

        No-op when the parent has no open file (its run started with the
        manifest off) — subtask channels then stay trace-only, no empty shells.
        """
        self._subscribe(event_bus, subtask_id, parent_task_id, subtask_owner(subtask_id))

    def detach_subtask(self, subtask_id: str) -> None:
        """Unsubscribe one subtask channel (its rounds already landed in the file)."""
        with self._lock:
            entry = self._channels.pop(subtask_id, None)
        if entry is not None:
            _parent, _owner, cb, bus = entry
            bus.unsubscribe(subtask_id, cb)

    def close(self, task_id: str) -> None:
        """Write ``manifest_end``, unsubscribe every channel, close the handle."""
        with self._lock:
            subs = [
                (cid, entry) for cid, entry in self._channels.items() if entry[0] == task_id
            ]
            for cid, _ in subs:
                self._channels.pop(cid, None)
            fh = self._files.get(task_id)
            # Drop the truncation flag first so the closing line is always
            # written — a capped file still ends with manifest_end.
            self._truncated.discard(task_id)
            if fh is not None:
                self._write(task_id, {"type": "manifest_end", "ts": time.time()})
                fh.close()
            self._files.pop(task_id, None)
            self._bytes.pop(task_id, None)
        # Unsubscribe outside the lock (TraceRecorder pattern, no deadlock risk).
        for _cid, (_parent, _owner, cb, bus) in subs:
            bus.unsubscribe(_cid, cb)

    # ── recording ──
    def wrap_llm(
        self,
        task_id: str,
        owner: str,
        client: LLMClient,
        client_tag: str = "main",
    ) -> LLMClient:
        """Wrap ``client`` so every round lands in ``task_id``'s manifest.

        Returns the client unchanged when the task has no open file (manifest
        off) or the client is already a proxy — callers need no ``if`` around
        the wrapping.
        """
        with self._lock:
            attached = task_id in self._files
        if not attached or isinstance(client, ManifestLLMProxy):
            return client
        return ManifestLLMProxy(client, self, task_id, owner, client_tag)

    def record_llm(
        self,
        task_id: str,
        owner: str,
        client_tag: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None,
        response: LLMResponse,
        elapsed_ms: float,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist one LLM round (request body + response + usage + elapsed)."""
        usage = (response.raw or {}).get("usage")
        line = {
            "type": "llm_call",
            "owner": owner,
            "client": client_tag,
            "request": {
                "messages": messages,
                "tools": tools,
                "n_tools": len(tools) if tools else 0,
                "params": params or {},
            },
            "response": {
                "content": response.content,
                "tool_calls": response.tool_calls,
                "raw": response.raw,
            },
            "usage": usage,
            "elapsed_ms": elapsed_ms,
        }
        with self._lock:
            self._write(task_id, redact_secrets(line, self._secrets))

    # ── internals ──
    def _subscribe(
        self, event_bus: EventBus, channel_id: str, parent_task_id: str, owner: str
    ) -> Optional[Callable[[Event], None]]:
        """Register one bus channel into the parent's file; ``None`` if dup/unknown."""
        with self._lock:
            if channel_id in self._channels or parent_task_id not in self._files:
                return None

            def _cb(event: Event, _tid: str = parent_task_id, _owner: str = owner) -> None:
                self._on_event(_tid, _owner, event)

            self._channels[channel_id] = (parent_task_id, owner, _cb, event_bus)
        event_bus.subscribe(channel_id, _cb)
        return _cb

    def _on_event(self, task_id: str, owner: str, event: Event) -> None:
        if event.get("type") not in MIRRORED_EVENT_TYPES:
            return
        with self._lock:
            self._write(
                task_id,
                redact_secrets(
                    {
                        "type": "event",
                        "event": event.get("type"),
                        "owner": owner,
                        "data": event.get("data"),
                        "ts": event.get("ts"),
                    },
                    self._secrets,
                ),
            )

    def _write(self, task_id: str, line: Dict[str, Any]) -> None:
        """Append one JSON line (caller holds the lock); enforce the byte cap."""
        fh = self._files.get(task_id)
        if fh is None or task_id in self._truncated:
            return
        text = json.dumps(line, ensure_ascii=False, default=str) + "\n"
        size = len(text.encode("utf-8"))
        if self._bytes[task_id] + size > self._max_bytes:
            marker = (
                json.dumps(
                    {
                        "type": "manifest_truncated",
                        "task_id": task_id,
                        "cap_bytes": self._max_bytes,
                        "ts": time.time(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            fh.write(marker)
            fh.flush()
            self._truncated.add(task_id)
            logger.warning(
                "run manifest for %s hit the %d-byte cap; further writes dropped",
                task_id,
                self._max_bytes,
            )
            return
        fh.write(text)
        fh.flush()
        self._bytes[task_id] += size

    def file_path(self, task_id: str) -> Path:
        """Absolute path of ``task_id``'s manifest file (may not exist)."""
        return self._dir / f"{task_id}.jsonl"
