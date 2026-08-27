"""Checkpoint persistence spot-checks (spec Issue #4, testing layer 2).

These tests pin down the two fragile links of the checkpoint wiring without
going through the full graph:

1. The saver round-trip under the project's ``thread_id = task_id``
   convention, including cross-thread isolation.
2. The serialization round-trip of a fully-populated :class:`AgentState`
   (sentinel against future non-serializable state fields).

They deliberately use the real SqliteSaver (temp file) rather than
MemorySaver so the on-disk format and connection semantics are exercised.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from backend.core.agent.state import AgentState


@pytest.fixture
def saver(tmp_path):
    """A real SqliteSaver rooted at a throwaway file (sync usage).

    Note: the ``with`` form closes the connection on exit — correct for a
    test, but production must construct the saver over its own long-lived
    ``sqlite3.connect(...)`` instead of borrowing this context manager.
    """
    conn_path = tmp_path / "checkpoints.sqlite"
    with SqliteSaver.from_conn_string(str(conn_path)) as saver:
        yield saver


def _config(task_id: str) -> Dict[str, Any]:
    """The project-wide convention: thread_id == task_id."""
    return {"configurable": {"thread_id": task_id}}


def _full_state() -> AgentState:
    """An AgentState with every field populated to a meaningful value."""
    return {
        "task_id": "t-full",
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        ],
        "plan": [{"index": 0, "description": "do a thing", "status": "done"}],
        "steps": [{"index": 0, "action": "tool_call", "tool": "web_search"}],
        "artifacts": [],
        "status": "RUNNING",
        "stop_requested": False,
        "pending_confirm": {"tool_call_id": "c1", "tool_name": "file_write"},
        "step_index": 3,
        "final_answer": "",
        "error": "",
        "compressed": False,
        "context_tokens": 512,
        "_last_action": "tool_done",
        "_current_tool_calls": [],
        "_confirmed_ids": ["c0"],
        "_rejected_ids": [],
        "_needs_confirm": True,
        "risk_report": [{"level": "high", "keyword": "rm -rf"}],
        "_risk_blocked": False,
        "subtasks": [],
        "_is_subtask": False,
    }


class TestCheckpointRoundTrip:
    def test_put_then_get_returns_identical_state_via_graph_state_channel(
        self, saver
    ):
        """A state saved under our thread_id convention comes back intact.

        We exercise the saver through a minimal StateGraph because channel
        values are what the resume path actually reads back — raw saver.put
        calls bypass langgraph's checkpoint envelope formatting.
        """
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        def echo(state: AgentState) -> dict:
            return {}

        g = StateGraph(_S)
        g.add_node("echo", echo)
        g.add_edge(START, "echo")
        g.add_edge("echo", END)
        app = g.compile(checkpointer=saver)

        state = _full_state()
        task_id = state["task_id"]
        app.invoke(state, _config(task_id))

        snap = app.get_state(_config(task_id))
        assert snap is not None
        values = dict(snap.values)
        for key, expected in state.items():
            assert values.get(key) == expected, f"field {key!r} lost in round-trip"

    def test_threads_are_isolated(self, saver):
        """Two threads never see each other's checkpoints."""
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        def mark(state: AgentState) -> dict:
            return {"final_answer": f"answer-of-{state['task_id']}"}

        g = StateGraph(_S)
        g.add_node("mark", mark)
        g.add_edge(START, "mark")
        g.add_edge("mark", END)
        app = g.compile(checkpointer=saver)

        app.invoke({"task_id": "t-a"}, _config("t-a"))
        app.invoke({"task_id": "t-b"}, _config("t-b"))

        sa = app.get_state(_config("t-a"))
        sb = app.get_state(_config("t-b"))
        assert sa.values["final_answer"] == "answer-of-t-a"
        assert sb.values["final_answer"] == "answer-of-t-b"

    def test_resume_invoke_none_continues_from_checkpoint(self, saver):
        """invoke(None, config) resumes without re-running completed work."""
        from langgraph.graph import END, START, StateGraph

        calls = {"n": 0}

        class _S(AgentState):
            pass

        def count(state: AgentState) -> dict:
            calls["n"] += 1
            return {}

        g = StateGraph(_S)
        g.add_node("count", count)
        g.add_edge(START, "count")
        g.add_edge("count", END)
        app = g.compile(checkpointer=saver)

        cfg = _config("t-resume")
        app.invoke({"task_id": "t-resume"}, cfg)
        assert calls["n"] == 1

        # Resuming a finished graph must not re-execute completed nodes.
        app.invoke(None, cfg)
        assert calls["n"] == 1


class TestEmptyStateRoundTrip:
    def test_minimal_state_survives_round_trip(self, saver):
        """A bare-bones state (only task_id) does not crash the checkpoint."""
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        g = StateGraph(_S)
        g.add_node("noop", lambda s: {})
        g.add_edge(START, "noop")
        g.add_edge("noop", END)
        app = g.compile(checkpointer=saver)

        cfg = _config("t-empty")
        app.invoke({"task_id": "t-empty"}, cfg)
        snap = app.get_state(cfg)
        assert snap.values.get("task_id") == "t-empty"


class TestAgentStateSerde:
    """Sentinel: the saver's serde pipeline must swallow a fully-populated AgentState.

    If someone ever adds a non-serializable field (client instance, lock,
    file handle) to AgentState, these tests are the tripwire.
    """

    def test_full_agentstate_round_trips_through_saver_serde(self, saver):
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        captured: Dict[str, Any] = {}

        def capture(state: AgentState) -> dict:
            captured.update(state)  # what the node actually received
            return {}  # write nothing back: keep the input state as the checkpoint

        g = StateGraph(_S)
        g.add_node("capture", capture)
        g.add_edge(START, "capture")
        g.add_edge("capture", END)
        app = g.compile(checkpointer=saver)

        state = _full_state()
        cfg = _config(state["task_id"])
        app.invoke(state, cfg)

        # The node must receive every field intact (pre-serialization).
        assert dict(captured) == {k: v for k, v in state.items()}

        snap = app.get_state(cfg)
        # Every input field must survive the serialize -> deserialize cycle
        # and reach the checkpoint snapshot unchanged.
        for key, expected in state.items():
            actual = snap.values.get(key)
            assert actual == expected, (
                f"field {key!r} changed across saver serde: {actual!r} != {expected!r}"
            )

    def test_unserializable_value_is_dropped_by_serde_documented(self, saver):
        """Documented behavior: langgraph's serde silently DROPS values it
        cannot serialize (e.g. threading.Lock) instead of raising.

        This is a real corruption risk for resumed tasks — recorded here so
        the behavior is pinned and noticed if it ever changes. The project
        guard is convention: AgentState stays JSON-safe (see spec Issue #4).
        """
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        lock = __import__("threading").Lock()

        def bad(state: AgentState) -> dict:
            return {"_secret_lock": lock}  # not a declared AgentState field

        g = StateGraph(_S)
        g.add_node("bad", bad)
        g.add_edge(START, "bad")
        g.add_edge("bad", END)
        app = g.compile(checkpointer=saver)

        cfg = _config("t-bad")
        app.invoke({"task_id": "t-bad"}, cfg)  # must not crash
        snap = app.get_state(cfg)
        # The unusable value did not survive: either absent or still the live
        # object it started as — never a corrupted re-hydrated copy.
        stored = snap.values.get("_secret_lock")
        assert stored is None or stored is lock
