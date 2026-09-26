"""P1 item 2 — sub-agent collaboration tests (fully offline).

Issue #56 left exactly one entry point (``spawn_subagent`` ->
``SubAgentExecutor.run_subtask``) and two built-in capability tiers. This module
covers three things:

* the surviving entry end to end: isolation (parent channel / artifacts), the
  concurrency cap, stop propagation, failure folding;
* the tier contract, judged **against the really mounted tool face** (never
  against a comment): what a subtask can get, what it can narrow to, and what
  it must never reach in any tier;
* the deleted entry B as a deletion regression: the keyword-triggered split node
  is gone from the topology and its API is gone from the module, so a
  "调研 X 并写报告" input must produce zero subtasks and zero side effects.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from backend.core.agent import subagent as subagent_mod
from backend.core.agent.graph import build_graph
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.subagent import (
    DEFAULT_TIER,
    EXECUTE_TIER,
    EXPLORE_TIER,
    TOOL_TIERS,
    SubAgentExecutor,
    SubTaskResult,
    SubTaskSpec,
    TierError,
    resolve_tool_face,
    subtask_owner,
    tier_face_names,
    tier_names,
)
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.core.tools import http_api
from backend.core.tools.mcp_tool import McpTool
from backend.services.snapshots import set_current_task_id
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import (
    _auto_confirm_in_background,
    _declared_edges,
    _run_until_done,
    _runtime,
)

#: Everything #56 puts out of a subtask's reach by construction: external
#: writes, generated / legacy tools, and the spawn tool itself (anti-recursion).
OUT_OF_TIER_NAMES = (
    "code_exec",
    "http_request",
    "file_io",
    "git_commit",
    "git_init",
    "git_checkout",
    "spawn_subagent",
    "createPet",
    "mcp__demo__fetch",
    "mcp__demo__create_issue",
)

EXPLORE_MEMBERS = [
    "read",
    "glob",
    "grep",
    "kb_query",
    "memory_search",
    "load_skill",
    "web_search",
    "git_status",
    "git_diff",
    "git_log",
    "git_branch",
]


# ── fixtures / helpers ───────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clear_task_ctx():
    """``_exec_one`` re-seats the parent task id on the calling thread and never
    restores it (harmless on the pool / worker threads it is built for, but not
    on pytest's one reused thread)."""
    set_current_task_id(None)
    yield
    set_current_task_id(None)


class _CountingMock(MockLLMClient):
    """Mock client that counts LLM rounds and records the offered tool names.

    ``turns == 0`` is how these tests say "the run never started" (a refused
    spawn must not spend a round), ``offered`` is the subtask's own view of its
    capability face — the only view the model can act on.
    """

    def __init__(self, plan=None, final_answer="done", **kwargs: Any) -> None:
        super().__init__(plan=plan or ["p"], final_answer=final_answer, **kwargs)
        self.turns = 0
        self.offered: List[str] = []

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            self.turns += 1
            return LLMResponse(content=json.dumps(self.plan))
        self.turns += 1
        self.offered = sorted(t["function"]["name"] for t in tools)
        if self._executor_turn < len(self.tool_calls):
            tc = self.tool_calls[self._executor_turn]
            self._executor_turn += 1
            return LLMResponse(content="", tool_calls=[tc])
        return LLMResponse(content=self.final_answer)


def _spec(subtask_id: str = "probe:sub:1", **overrides: Any) -> SubTaskSpec:
    base: Dict[str, Any] = dict(subtask_id=subtask_id, name="probe", instruction="do it")
    base.update(overrides)
    return SubTaskSpec(**base)


def _mounted_managers(tmp_path) -> List[Tuple[str, TaskManager, SubAgentExecutor]]:
    """One manager per face-growing configuration (plain / Git / OpenAPI).

    The tier assertions are only worth anything if they run against tools that
    actually exist, so every configuration that appends tools to the parent face
    gets its own manager here.
    """
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "pets", "version": "1"},
        "paths": {
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "responses": {"200": {"description": "ok"}},
                },
                "post": {
                    "operationId": "createPet",
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    configs: Dict[str, Dict[str, Any]] = {
        "plain": {},
        "git": {"git_enabled": True},
        "openapi": {
            "openapi_enabled": True,
            "openapi_spec_path": str(spec_path),
        },
        "mcp": {},
    }
    out: List[Tuple[str, TaskManager, SubAgentExecutor]] = []
    for label, overrides in configs.items():
        settings = make_settings(tmp_path / label, **overrides)
        tm = make_manager(settings, _CountingMock(plan=["p"], final_answer="d"))
        if label == "mcp":
            # A real server would spawn a subprocess; TaskManager appends
            # discovered MCP tools to the same list the same way, so mount two.
            for tool_name in ("fetch", "create_issue"):
                tm._tools.append(
                    McpTool(
                        server_name="demo",
                        tool_name=tool_name,
                        description="",
                        input_schema={"type": "object", "properties": {}},
                        manager=None,
                        settings=settings,
                    )
                )
        out.append((label, tm, SubAgentExecutor(tm, settings)))
    return out


class _RecordingHttpClient:
    """Stand-in for ``httpx.Client``: records use, refuses to hit the network."""

    calls: List[Any] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).calls.append(("client", kwargs))

    def __enter__(self) -> "_RecordingHttpClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def request(self, **kwargs: Any) -> Any:
        type(self).calls.append(("request", kwargs))
        raise AssertionError("a subtask must never reach the HTTP layer")


# ── entry B is dead (deletion regression, #56) ───────────────────────────────
def test_entry_b_split_api_is_gone():
    """入口 B 连同其调度 API 一起删掉：留着名字就是留着可复用的后门。"""
    for gone in ("run_plan_with_subtasks", "subtask_tool_face"):
        assert not hasattr(SubAgentExecutor, gone)
        assert not hasattr(subagent_mod, gone)
    for gone in ("split_plan_for_scenario", "DEFAULT_SPLIT_SCENARIOS", "_extract_topic"):
        assert not hasattr(subagent_mod, gone)


def test_executor_exposes_only_the_spawn_entry():
    assert callable(SubAgentExecutor.run_subtask)
    assert callable(SubAgentExecutor.check_face)
    public = {n for n in dir(SubAgentExecutor) if not n.startswith("_")}
    assert public == {"check_face", "run_subtask"}


def test_main_graph_has_no_split_node():
    """`subagent_split` must be neither a node nor an endpoint of any edge."""
    graph = build_graph(_runtime())
    edges = _declared_edges(graph)
    names = set(graph.get_graph().nodes)

    assert "subagent_split" not in names
    for src, dst in edges:
        assert "subagent_split" not in (src, dst)
    # The risk scan now hands straight to the executor.
    assert ("risk_scan", "executor") in edges


def test_research_wording_no_longer_produces_subtasks(tmp_path, event_bus):
    """票面验收：含「调研…写报告」的输入在主图跑完 = 零子任务、零 subtask_* 事件。"""
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["调研", "写作"], final_answer="父任务自己完成了")
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="t", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    assert task.subtasks == []

    events = event_bus.replay(task_id)
    assert not [e for e in events if "subtask" in e["type"]], "入口 B 事件词汇表已死"
    # 没有任何工具被调用过（拆分入口曾替父任务写文件），也就没有产物。
    assert not [e for e in events if e["type"] == "tool_call"]
    assert not [e for e in events if e["type"] == "artifact_created"]
    assert list(settings.artifacts_path.glob("**/*") if settings.artifacts_path.exists() else []) == []


# ── tier declaration and helpers ─────────────────────────────────────────────
def test_builtin_tiers_are_the_declared_name_sets():
    assert set(TOOL_TIERS) == {EXPLORE_TIER, EXECUTE_TIER}
    assert DEFAULT_TIER == EXECUTE_TIER
    assert sorted(TOOL_TIERS[EXPLORE_TIER]) == sorted(EXPLORE_MEMBERS)
    assert sorted(TOOL_TIERS[EXECUTE_TIER]) == sorted(set(EXPLORE_MEMBERS) | {"write", "edit"})
    # explore 档里没有写工具，也没有任何档外名字。
    assert "write" not in TOOL_TIERS[EXPLORE_TIER]
    assert "edit" not in TOOL_TIERS[EXPLORE_TIER]


def test_tier_names_rejects_an_unknown_tier():
    assert tier_names(EXPLORE_TIER) == TOOL_TIERS[EXPLORE_TIER]
    with pytest.raises(TierError) as excinfo:
        tier_names("oracle")
    message = str(excinfo.value)
    assert "oracle" in message
    assert EXPLORE_TIER in message and EXECUTE_TIER in message


def test_tier_face_names_intersect_with_the_loaded_face():
    loaded = ["read", "write", "code_exec", "file_io"]
    assert tier_face_names(loaded, EXECUTE_TIER) == ["read", "write"]
    assert tier_face_names(loaded, EXPLORE_TIER) == ["read"]
    assert tier_face_names([], EXECUTE_TIER) == []
    with pytest.raises(TierError):
        tier_face_names(loaded, "oracle")


def test_git_tools_appear_in_the_tiers_only_when_mounted(tmp_path):
    """档位是名字集合，实装面才是真相：git_enabled=false 时 git 四件自然消失。"""
    plain = make_settings(tmp_path / "plain")
    with_git = make_settings(tmp_path / "git", git_enabled=True)
    plain_tm = make_manager(plain, _CountingMock())
    git_tm = make_manager(with_git, _CountingMock())

    plain_face = [t.name for t in plain_tm._tools]
    git_face = [t.name for t in git_tm._tools]
    assert any(n.startswith("git_") for n in git_face)
    assert not any(n.startswith("git_") for n in plain_face)
    assert not [n for n in tier_face_names(plain_face, EXPLORE_TIER) if n.startswith("git_")]
    assert {"git_status", "git_diff", "git_log", "git_branch"} <= set(
        tier_face_names(git_face, EXPLORE_TIER)
    )
    assert tier_face_names(git_face, EXECUTE_TIER) == sorted(
        set(tier_face_names(git_face, EXPLORE_TIER)) | {"write", "edit"}
    )


def test_subtask_owner_label():
    assert subtask_owner("abc:sub:1") == "subtask:abc:sub:1"


def test_resolve_tool_face_narrows_but_never_widens(tmp_path):
    tm = make_manager(make_settings(tmp_path), _CountingMock())
    tools = tm._tools
    names = [t.name for t in tools]

    # subset=None -> the whole tier as it resolves against the real face.
    whole = resolve_tool_face(tools, EXPLORE_TIER)
    assert sorted(t.name for t in whole) == tier_face_names(names, EXPLORE_TIER)

    narrowed = resolve_tool_face(tools, EXECUTE_TIER, ["read", "grep"])
    assert sorted(t.name for t in narrowed) == ["grep", "read"]

    with pytest.raises(TierError) as excinfo:
        resolve_tool_face(tools, EXECUTE_TIER, ["read", "code_exec"])
    message = str(excinfo.value)
    assert "code_exec" in message  # the refused name is named, not silently dropped
    assert "read" in message  # and the tier members come along, so the model can fix it

    with pytest.raises(TierError):
        resolve_tool_face(tools, "oracle")


def test_check_face_resolves_without_running_anything(tmp_path):
    mock = _CountingMock()
    tm = make_manager(make_settings(tmp_path), mock)
    ex = SubAgentExecutor(tm, make_settings(tmp_path))

    assert ex.check_face(_spec(tier=EXECUTE_TIER, tools=["read"])) == ["read"]
    assert ex.check_face(_spec(tier=EXPLORE_TIER)) == tier_face_names(
        [t.name for t in tm._tools], EXPLORE_TIER
    )
    assert mock.turns == 0  # a face query is not a run


# ── tier reachability, judged on the real face (issue #56 AC) ────────────────
def test_no_tier_mounts_a_confirmation_gated_tool(tmp_path):
    """子任务图里没有确认闸门，所以「要问人」的工具必须在任何档位都拿不到。"""
    seen_gated: set = set()
    for label, tm, ex in _mounted_managers(tmp_path):
        gated = {
            t.name
            for t in tm._tools
            if getattr(t, "requires_confirm", False)
            or getattr(t, "needs_per_call_confirm", False)
        }
        assert gated, f"{label} 装载面没有闸门工具，下面的断言会空转"
        seen_gated |= gated
        names = [t.name for t in tm._tools]
        for tier in (EXPLORE_TIER, EXECUTE_TIER):
            face = ex.check_face(_spec(subtask_id=f"{label}:sub:1", tier=tier))
            assert face == tier_face_names(names, tier)
            leaked = set(face) & gated
            assert not leaked, f"{label}/{tier} 档漏进了需要确认的工具 {sorted(leaked)}"
    # The gated families the issue names are all really present somewhere above.
    assert {"code_exec", "spawn_subagent", "git_commit", "mcp__demo__fetch"} <= seen_gated


def test_external_write_family_is_out_of_reach_in_every_tier(tmp_path):
    """档外即不可达：外部写 / legacy file_io / 生成型工具一个都进不了子任务。"""
    seen_mounted: set = set()
    for label, tm, ex in _mounted_managers(tmp_path):
        names = {t.name for t in tm._tools}
        seen_mounted |= names
        for tier in (EXPLORE_TIER, EXECUTE_TIER):
            face = set(ex.check_face(_spec(subtask_id=f"{label}:sub:1", tier=tier)))
            for name in OUT_OF_TIER_NAMES:
                assert name not in face, f"{label}/{tier} 竟然给了子任务 {name}"
    # Every named tool must have been mounted by at least one configuration,
    # otherwise "absent from the tier" would be an artefact of an empty face.
    assert set(OUT_OF_TIER_NAMES) <= seen_mounted


def test_generated_openapi_tools_stay_on_the_parent_only(tmp_path):
    """Audit finding ①: a spec-generated POST is gated for the parent and must
    not reach a subtask, where the gate is structurally absent. Read-only
    generated tools are out of reach too — the tiers are a fixed name set."""
    settings = make_settings(
        tmp_path,
        openapi_enabled=True,
        openapi_spec_path=_write_pet_spec(tmp_path),
    )
    tm = make_manager(settings, _CountingMock())
    names = {t.name for t in tm._tools}
    assert {"listPets", "createPet"} <= names  # both generated onto the parent face
    ex = SubAgentExecutor(tm, settings)
    for tier in (EXPLORE_TIER, EXECUTE_TIER):
        face = set(ex.check_face(_spec(tier=tier)))
        assert "createPet" not in face
        assert "listPets" not in face


def _write_pet_spec(tmp_path) -> str:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "pets", "version": "1"},
        "paths": {
            "/pets": {
                "get": {"operationId": "listPets", "responses": {"200": {"description": "ok"}}},
                "post": {"operationId": "createPet", "responses": {"200": {"description": "ok"}}},
            },
        },
    }
    path = tmp_path / "pets.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return str(path)


def test_spawned_subtask_face_carries_the_sandbox_writes_only(tmp_path):
    """execute 档 = explore 档 + write/edit；legacy file_io 不在任何档里。"""
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _CountingMock())
    ex = SubAgentExecutor(tm, settings)
    execute = ex.check_face(_spec(tier=EXECUTE_TIER))
    explore = ex.check_face(_spec(tier=EXPLORE_TIER))

    assert set(execute) - set(explore) == {"write", "edit"}
    assert "file_io" not in execute and "file_io" in {t.name for t in tm._tools}
    assert "spawn_subagent" not in execute  # anti-recursion
    assert "web_search" in execute


# ── the surviving entry: run_subtask / spawn_subagent ────────────────────────
def test_run_subtask_single_spec(tmp_path):
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="single done")
    tm = make_manager(settings, mock)
    ex = SubAgentExecutor(tm, settings)
    res = ex.run_subtask(_spec(subtask_id="x:sub:1", name="x", instruction="do it"))

    assert res.status == "completed"
    assert "single done" in res.summary
    assert res.tool_face == tier_face_names([t.name for t in tm._tools], DEFAULT_TIER)
    # The subtask was offered exactly its tier face, not the parent's whole face.
    assert mock.offered == res.tool_face


def test_run_subtask_refuses_a_bad_request_without_a_single_round(tmp_path):
    """TierError 在 `_exec_one` 里不开跑：零 LLM 轮次、零工具调用、零副作用。"""
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="never")
    tm = make_manager(settings, mock)
    ex = SubAgentExecutor(tm, settings)

    bad_tier = ex.run_subtask(_spec(subtask_id="bad:sub:1", tier="oracle"))
    widened = ex.run_subtask(_spec(subtask_id="bad:sub:2", tier=EXECUTE_TIER, tools=["code_exec"]))

    assert bad_tier.status == "failed" and "oracle" in bad_tier.error
    assert widened.status == "failed" and "code_exec" in widened.error
    assert bad_tier.tool_face == [] and widened.tool_face == []
    assert mock.turns == 0
    assert list(settings.artifacts_path.glob("**/*") if settings.artifacts_path.exists() else []) == []


def test_spawn_subagent_tool_wired_and_runs(tmp_path):
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="spawned done")
    tm = make_manager(settings, mock)
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    assert tool.executor is not None

    res = tool.run(name="research", instruction="collect facts")
    assert res.success is True
    assert res.data["status"] == "completed"
    assert "spawned done" in res.data["summary"]
    assert res.data["subtask_id"]
    assert mock.offered == SubAgentExecutor(tm, settings).check_face(
        _spec(tier=DEFAULT_TIER)
    )


def test_spawn_subagent_advertises_the_two_tiers(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _CountingMock())
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    props = tool.args_schema["properties"]
    assert props["tier"]["enum"] == [EXECUTE_TIER, EXPLORE_TIER]
    assert "tools" in props


def test_spawn_subagent_narrows_to_the_requested_subset(tmp_path):
    """`tools` 生效：传子集后子任务被递给模型的面恰好是该子集。"""
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="narrowed")
    tm = make_manager(settings, mock)
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")

    res = tool.run(name="reader", instruction="read one file", tools=["read", "grep"])
    assert res.success is True
    assert mock.offered == ["grep", "read"]
    assert "write" not in mock.offered  # narrower than the default tier


def _recording_run(started: List[SubTaskSpec]):
    """A stand-in ``run_subtask`` that only records — proves it never ran."""

    def _fake(spec, publish=None):
        started.append(spec)
        return SubTaskResult(subtask_id=spec.subtask_id, name=spec.name, status="completed")

    return _fake


def test_spawn_subagent_refuses_a_widening_request_before_running(
    tmp_path, event_bus, monkeypatch
):
    """越界的 `tools` 名字：工具直接 success=False + 理由，executor 一次都没被调用。"""
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="never")
    tm = make_manager(settings, mock, event_bus=event_bus)
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    started: List[SubTaskSpec] = []
    assert tm._subagent is not None
    monkeypatch.setattr(tm._subagent, "run_subtask", _recording_run(started))

    res = tool.run(name="widening", instruction="try it", tools=["read", "code_exec"])

    assert res.success is False
    assert "code_exec" in (res.error or "") and "read" in (res.error or "")
    assert started == []
    assert mock.turns == 0
    assert list(settings.artifacts_path.glob("**/*") if settings.artifacts_path.exists() else []) == []


def test_spawn_subagent_rejects_an_unknown_tier(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="never")
    tm = make_manager(settings, mock)
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    started: List[SubTaskSpec] = []
    assert tm._subagent is not None
    monkeypatch.setattr(tm._subagent, "run_subtask", _recording_run(started))

    res = tool.run(name="bogus", instruction="hi", tier="oracle")

    assert res.success is False
    assert "oracle" in (res.error or "")
    assert started == [] and mock.turns == 0


def test_subagent_disabled_removes_the_spawn_tool(tmp_path):
    """`subagent_enabled=False` 现在摘的是工具本身，不再只是不挂执行器。"""
    settings = make_settings(tmp_path, subagent_enabled=False)
    tm = make_manager(settings, _CountingMock())
    names = {t.name for t in tm._tools}
    assert "spawn_subagent" not in names
    assert "spawn_subagent" not in {s["function"]["name"] for s in tm._tool_schemas}
    assert tm._subagent is None
    # The rest of the face is untouched.
    assert {"read", "write", "file_io"} <= names


class _StubExecutor:
    """Captures the spec the tool built, without running anything."""

    def __init__(self) -> None:
        self.specs: List[SubTaskSpec] = []

    def check_face(self, spec: SubTaskSpec) -> List[str]:
        return ["read"]

    def run_subtask(self, spec: SubTaskSpec, publish=None) -> SubTaskResult:
        self.specs.append(spec)
        return SubTaskResult(
            subtask_id=spec.subtask_id,
            name=spec.name,
            status="completed",
            summary="ok",
            artifacts=[],
            error="",
        )


def test_spawn_subagent_records_parent_task_id_and_tier(tmp_path):
    """The spawn path runs on the parent's thread, so the parent link and the
    requested tier/face must land on the spec — the parent id is what keeps the
    rollback ledger and the stop signal attached."""
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _CountingMock())
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    stub = _StubExecutor()
    tool.executor = stub
    set_current_task_id("parent-9")
    try:
        res = tool.run(name="research", instruction="collect facts", tier=EXPLORE_TIER)
    finally:
        set_current_task_id(None)

    assert res.success is True
    spec = stub.specs[0]
    assert spec.parent_task_id == "parent-9"
    assert spec.tier == EXPLORE_TIER
    assert spec.tools is None


def test_spawn_subagent_defaults_to_the_execute_tier(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _CountingMock())
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    stub = _StubExecutor()
    tool.executor = stub
    res = tool.run(name="n", instruction="i", tools=["read"])
    assert res.success is True
    assert stub.specs[0].tier == DEFAULT_TIER
    assert stub.specs[0].tools == ["read"]


# ── isolation / artifact hand-back through the surviving entry ───────────────
class _ResearchMock(MockLLMClient):
    """Parent delegates the research leg (and gets approved); the subtask writes
    its notes and reports back.

    Entry B reached this shape from the wording alone; this drives the surviving
    ``spawn_subagent`` path with the same script, which is why
    ``test_run_manifest`` imports it for the parent-manifest cases. The subtask
    turn is recognised by the marker in its instruction because one client
    instance serves both graphs.
    """

    INSTRUCTION = "检索素材并记下要点"

    def __init__(self):
        super().__init__(plan=["调研", "写作"], final_answer="父任务完成")
        self.subtask_turns = 0

    def complete(self, messages, tools=None, **kwargs):
        user = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        )
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        if self.INSTRUCTION in user:
            self.subtask_turns += 1
            if self.subtask_turns == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "sub1",
                            "name": "write",
                            "arguments": {
                                "path": "research_notes.txt",
                                "content": "RAG notes",
                            },
                        }
                    ],
                )
            return LLMResponse(content="研究完成")
        if self._executor_turn == 0:
            self._executor_turn += 1
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "spawn1",
                        "name": "spawn_subagent",
                        "arguments": {
                            "name": "研究子任务",
                            "instruction": self.INSTRUCTION,
                            "tier": DEFAULT_TIER,
                        },
                    }
                ],
            )
        return LLMResponse(content=self.final_answer)


class _EscalatingSubtaskMock(MockLLMClient):
    """Parent delegates once; the subtask then asks for tools it was never given.

    One client instance serves both graphs, so the subtask turn is identified by
    the marker in its instruction (the parent's own user text never carries it).
    """

    MARKER = "越界探针"
    REPORT = "subtask_probe_report.md"
    SIDE_EFFECT = "subtask_side_effect.txt"

    def __init__(self) -> None:
        super().__init__(plan=["委托子任务"], final_answer="主任务结束")
        self.parent_turns = 0
        self.subtask_turns = 0
        self.offered_to_subtask: List[str] = []

    def _user_text(self, messages: List[Dict[str, Any]]) -> str:
        return " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        )

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        if self.MARKER in self._user_text(messages):
            self.offered_to_subtask = sorted(t["function"]["name"] for t in tools)
            self.subtask_turns += 1
            if self.subtask_turns == 1:
                # The out-of-tier family: an external write and the legacy
                # bundle tool. Neither exists on this graph's face.
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "out1",
                            "name": "http_request",
                            "arguments": {
                                "method": "POST",
                                "url": "http://hook.example.invalid/side-effect",
                                "body": {"from": "subtask"},
                            },
                        },
                        {
                            "id": "out2",
                            "name": "file_io",
                            "arguments": {
                                "action": "write",
                                "path": self.SIDE_EFFECT,
                                "content": "must never exist",
                            },
                        },
                    ],
                )
            if self.subtask_turns == 2:
                # The tier-legal way to deliver a report.
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "ok1",
                            "name": "write",
                            "arguments": {
                                "path": self.REPORT,
                                "content": "探针结论：外部写工具不可达",
                            },
                        }
                    ],
                )
            return LLMResponse(content="外部写工具不在我的能力面内")
        self.parent_turns += 1
        if self.parent_turns == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "spawn1",
                        "name": "spawn_subagent",
                        "arguments": {
                            "name": "probe",
                            "instruction": f"{self.MARKER}：试着用外部写工具交差",
                            "tier": EXECUTE_TIER,
                        },
                    }
                ],
            )
        return LLMResponse(content=self.final_answer)


def test_approved_spawn_cannot_reach_external_writes_and_has_no_side_effect(
    tmp_path, event_bus, monkeypatch
):
    """票面验收 a：主任务批准派生后，子任务要不到「写外部系统」的工具，也没留下副作用。"""
    _RecordingHttpClient.calls = []
    monkeypatch.setattr(http_api.httpx, "Client", _RecordingHttpClient)

    settings = make_settings(tmp_path)
    mock = _EscalatingSubtaskMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="probe", user_input="派一个子任务去探针")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=True)
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    parent_events = event_bus.replay(task_id)

    # The gate really was the spawn call, and it was approved.
    confirm = [e for e in parent_events if e["type"] == "human_confirm_required"]
    assert [e["data"]["tool_name"] for e in confirm] == ["spawn_subagent"]

    spawn_results = [
        e["data"]
        for e in parent_events
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    ]
    assert spawn_results and spawn_results[0]["status"] == "success"
    subtask_id = spawn_results[0]["output"]["subtask_id"]

    # 1) Not on the face: neither from the executor's side nor from the model's.
    ex = tm._subagent
    assert ex is not None
    face = ex.check_face(_spec(subtask_id=subtask_id, tier=EXECUTE_TIER))
    for name in ("http_request", "file_io", "code_exec", "spawn_subagent"):
        assert name not in face
        assert name not in mock.offered_to_subtask
    assert set(mock.offered_to_subtask) <= set(TOOL_TIERS[EXECUTE_TIER])

    # 2) No side effect: the attempts failed as "unknown tool", nothing ran.
    sub_events = event_bus.replay(subtask_id)
    refused = [
        e["data"]
        for e in sub_events
        if e["type"] == "tool_result" and e["data"]["tool_name"] in ("http_request", "file_io")
    ]
    assert {r["tool_name"] for r in refused} == {"http_request", "file_io"}
    assert all(r["status"] == "failed" and "unknown tool" in r["error"] for r in refused)
    assert _RecordingHttpClient.calls == []  # the HTTP layer was never touched
    assert not (settings.artifacts_path / mock.SIDE_EFFECT).exists()

    # 3) The tier-legal write did happen, and it handed back to the parent.
    assert (settings.artifacts_path / mock.REPORT).exists()
    assert any(mock.REPORT in a.filename for a in task.artifacts)
    written = [
        e["data"]
        for e in sub_events
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "write"
    ]
    assert written and written[0]["status"] == "success"

    # 4) Isolation: the parent channel never sees the subtask's tool calls.
    parent_tools = {
        e["data"]["tool_name"] for e in parent_events if e["type"] in ("tool_call", "tool_result")
    }
    assert parent_tools == {"spawn_subagent"}
    # The subtask's own channel carries its plan and its tool calls.
    assert any(e["type"] == "plan_update" for e in sub_events)


def test_subtask_writes_and_events_do_not_pollute_the_parent(tmp_path, event_bus):
    """run_subtask 直连路径的隔离：内部事件在自己的频道，产物与账本挂父任务。"""
    settings = make_settings(tmp_path, snapshot_enabled=True)
    mock = _CountingMock(plan=["p"], final_answer="写完了")
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="parent", user_input="父任务：只要一句话结论")
    _run_until_done(tm, task_id)
    # The subtask's script is armed only after the parent run, so the file below
    # provably comes from the subtask and not from its parent.
    mock.tool_calls = [
        {"id": "w1", "name": "write", "arguments": {"path": "direct_note.md", "content": "x"}}
    ]

    ex = SubAgentExecutor(tm, settings)
    set_current_task_id(None)  # the pool thread re-seats it; the caller must not leak it
    res = ex.run_subtask(
        _spec(subtask_id="iso:sub:1", name="iso", instruction="写个文件", parent_task_id=task_id)
    )
    assert res.status == "completed"
    assert res.artifacts and Path(res.artifacts[0]).name == "direct_note.md"

    parent_types = {e["type"] for e in event_bus.replay(task_id)}
    assert "tool_call" not in parent_types  # internal rounds stayed away
    assert "artifact_created" in parent_types  # the hand-back is the parent's

    sub_types = {e["type"] for e in event_bus.replay("iso:sub:1")}
    assert {"plan_update", "tool_call", "tool_result"} <= sub_types

    ledger = settings.snapshots_path / task_id / "ledger.jsonl"
    assert ledger.exists()
    assert "direct_note.md" in ledger.read_text(encoding="utf-8")


# ── scheduling on the surviving pool ────────────────────────────────────────
class _SleepingMock(MockLLMClient):
    def __init__(self, sleep=0.4):
        super().__init__(plan=["p"], final_answer="done")
        self.sleep = sleep

    def complete(self, messages, tools=None, **kwargs):
        time.sleep(self.sleep)
        return super().complete(messages, tools, **kwargs)


def _run_two_specs_through_the_pool(settings, tm):
    """`subagent_max_concurrency` still sizes the executor's pool; the two
    timing tests below keep the cap honest now that entry B no longer submits."""
    ex = SubAgentExecutor(tm, settings)
    specs = [
        _spec(subtask_id=f"pool:sub:{i}", name=f"s{i}", instruction="think quietly")
        for i in range(2)
    ]
    start = time.time()
    futures = [ex._pool.submit(ex._exec_one, s) for s in specs]
    results = [f.result(timeout=60) for f in futures]
    return results, time.time() - start


def test_parallel_subtasks_overlap(tmp_path):
    settings = make_settings(tmp_path, subagent_max_concurrency=2)
    tm = make_manager(settings, _SleepingMock(sleep=0.4))
    results, elapsed = _run_two_specs_through_the_pool(settings, tm)

    assert len(results) == 2
    assert all(r.status == "completed" for r in results)
    # Two subtasks of two 0.4s rounds each; with 2 workers ~0.8s, not ~1.6s.
    assert elapsed < 1.2, f"subtasks did not overlap (elapsed={elapsed:.2f}s)"


def test_run_subtask_is_bounded_by_the_wall_clock_timeout(tmp_path):
    """`subagent_timeout_sec` 现役的执行点：跑不完就折成 failed，不挂住父线程。"""
    settings = make_settings(tmp_path, subagent_timeout_sec=1)
    tm = make_manager(settings, _SleepingMock(sleep=0.9))
    ex = SubAgentExecutor(tm, settings)

    res = ex.run_subtask(_spec(subtask_id="timeout:sub:1", instruction="think slowly"))

    assert res.status == "failed"
    assert "timeout" in res.error


def test_serial_subtasks_when_concurrency_1(tmp_path):
    settings = make_settings(tmp_path, subagent_max_concurrency=1)
    tm = make_manager(settings, _SleepingMock(sleep=0.4))
    results, elapsed = _run_two_specs_through_the_pool(settings, tm)

    assert len(results) == 2
    assert elapsed >= 1.4, f"subtasks ran in parallel despite concurrency=1 (elapsed={elapsed:.2f}s)"


# ── the simplified graph, stop propagation and failure folding ───────────────
def test_subtask_graph_compiles_simplified(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _CountingMock())
    runtime = AgentRuntime(
        task_id="sub",
        task_manager=tm,
        llm=tm._llm,
        tools=tm._tools,
        tool_schemas=tm._tool_schemas,
        confirm_enabled=False,
    )
    graph = build_graph(runtime, mode="subtask")
    assert graph is not None
    assert "human_confirm" not in set(graph.get_graph().nodes)


class _KeepLoopingMock(MockLLMClient):
    """Executor never converges: one tool call per turn, counted.

    Used to prove a stop signal actually short-circuits the loop — an unfixed
    child runs every ``max_steps`` turn, a stopped child barely calls the LLM.
    """

    def __init__(self) -> None:
        super().__init__(plan=["p"], final_answer="never reached")
        self.executor_calls = 0

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        self.executor_calls += 1
        return LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": f"c{self.executor_calls}",
                    "name": "write",
                    "arguments": {"path": "loop.txt", "content": "x"},
                }
            ],
        )


def _run_stopped_subtask(tmp_path, stop_key: str, parent_task_id: str):
    settings = make_settings(tmp_path)
    mock = _KeepLoopingMock()
    tm = make_manager(settings, mock)
    tm._stop_flags[stop_key] = True  # the authoritative signal nodes poll
    ex = SubAgentExecutor(tm, settings)
    spec = SubTaskSpec(
        subtask_id="p60:sub:1",
        name="looping",
        instruction="keep going",
        parent_task_id=parent_task_id,
    )
    return ex.run_subtask(spec), mock


def test_parent_stop_propagates_to_subtask(tmp_path):
    """Flagging the PARENT must stop the child run (Issue #60)."""
    res, mock = _run_stopped_subtask(tmp_path, stop_key="parent-1", parent_task_id="parent-1")
    assert res.status != "completed"
    assert mock.executor_calls < 5, f"child ignored the parent stop: {mock.executor_calls} turns"


def test_direct_stop_on_subtask_id_still_works(tmp_path):
    """Propagation must not replace the child's own stop key (single-parent trap)."""
    res, mock = _run_stopped_subtask(tmp_path, stop_key="p60:sub:1", parent_task_id="parent-1")
    assert res.status != "completed"
    assert mock.executor_calls < 5


def test_subtask_crash_folds_back_as_failed_and_the_parent_survives(
    tmp_path, event_bus, monkeypatch
):
    """崩掉的子任务只折回成一次失败的工具调用，父任务必须自己收场。"""
    settings = make_settings(tmp_path)
    mock = _EscalatingSubtaskMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    assert tm._subagent is not None

    def _boom(spec, publish=None):
        raise RuntimeError("subtask worker crashed")

    monkeypatch.setattr(tm._subagent, "_exec_one", _boom)
    task_id = tm.create_task(title="crash", user_input="派一个子任务去探针")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=True)
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    assert task.subtasks == []  # 入口 B 的折叠摘要字段已无生产者
    results = [
        e["data"]
        for e in event_bus.replay(task_id)
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    ]
    assert results and results[0]["status"] == "failed"
    assert "crashed" in results[0]["error"]
    # 入口 B 的事件词汇表随节点一起消失，父频道上只剩这次失败。
    assert not [e for e in event_bus.replay(task_id) if "subtask" in e["type"]]
    assert not (settings.artifacts_path / _EscalatingSubtaskMock.REPORT).exists()


def test_subtask_failure_reports_the_cause_not_a_silent_success(tmp_path):
    """A failed subtask must fold back as failed with the reason (Issue #12)."""
    settings = make_settings(tmp_path)

    class _CrashMock(MockLLMClient):
        def complete(self, messages, tools=None, **kwargs):
            if not tools:
                raise RuntimeError("simulated planner 403")
            return super().complete(messages, tools, **kwargs)

    tm = make_manager(settings, _CrashMock(plan=["p"]))
    res = tm._subagent.run_subtask(_spec(subtask_id="fail:sub:1", tier=EXPLORE_TIER))
    assert res.status == "failed"
    assert "403" in res.error
    assert res.summary == "(no final answer)"
    # The run did start, on the explore face only (no writes).
    assert res.tool_face == tier_face_names(
        [t.name for t in tm._tools], EXPLORE_TIER
    )
    assert "write" not in res.tool_face
