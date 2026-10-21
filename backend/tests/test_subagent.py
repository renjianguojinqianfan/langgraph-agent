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
import threading
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
    tier_face_names,
    tier_names,
)
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.core.runtime_context import set_current_task_id
from backend.core.tools.mcp_tool import McpTool
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import (
    _declared_edges,
    _run_until_done,
    _runtime,
)

#: Everything #56 puts out of a subtask's reach by construction: shell access,
#: MCP tools, and the spawn tool itself (anti-recursion).
OUT_OF_TIER_NAMES = (
    "bash",
    "spawn_subagent",
    "mcp__demo__fetch",
    "mcp__demo__create_issue",
)

EXPLORE_MEMBERS = [
    "read",
    "ls",
    "glob",
    "grep",
    "load_skill",
    "web_search",
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
        if self._turn < len(self.tool_calls):
            tc = self.tool_calls[self._turn]
            self._turn += 1
            return LLMResponse(content="", tool_calls=[tc])
        return LLMResponse(content=self.final_answer)


def _spec(subtask_id: str = "probe:sub:1", **overrides: Any) -> SubTaskSpec:
    base: Dict[str, Any] = dict(subtask_id=subtask_id, name="probe", instruction="do it")
    base.update(overrides)
    return SubTaskSpec(**base)


@pytest.fixture
def ghost_tier(monkeypatch):
    """Plant a declared-but-unmounted tier member.

    The shipped tiers only name mounted tools, so the #74 refusal path needs a
    synthetic name to stay covered.
    """
    monkeypatch.setitem(
        TOOL_TIERS, EXPLORE_TIER, [*TOOL_TIERS[EXPLORE_TIER], "ghost_tool"]
    )


def _mounted_managers(tmp_path) -> List[Tuple[str, TaskManager, SubAgentExecutor]]:
    """One manager per face-growing configuration (plain / MCP).

    The tier assertions are only worth anything if they run against tools that
    actually exist, so every configuration that appends tools to the parent face
    gets its own manager here.
    """
    out: List[Tuple[str, TaskManager, SubAgentExecutor]] = []
    for label in ("plain", "mcp"):
        settings = make_settings(tmp_path / label)
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
    # #139: one loop, one agent node — no planner/executor split left to hang a
    # keyword-triggered node off.
    assert ("agent", "tool") in edges


def test_research_wording_no_longer_produces_subtasks(tmp_path, event_bus):
    """票面验收：含「调研…写报告」的输入在主图跑完 = 零子任务、零 subtask_* 事件。"""
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["调研", "写作"], final_answer="父任务自己完成了")
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="t", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "INTERRUPTED"
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
    loaded = ["read", "write", "bash", "spawn_subagent"]
    assert tier_face_names(loaded, EXECUTE_TIER) == ["read", "write"]
    assert tier_face_names(loaded, EXPLORE_TIER) == ["read"]
    assert tier_face_names([], EXECUTE_TIER) == []
    with pytest.raises(TierError):
        tier_face_names(loaded, "oracle")


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
        resolve_tool_face(tools, EXECUTE_TIER, ["read", "bash"])
    message = str(excinfo.value)
    assert "bash" in message  # the refused name is named, not silently dropped
    assert "read" in message  # and the tier members come along, so the model can fix it

    with pytest.raises(TierError):
        resolve_tool_face(tools, "oracle")


def test_resolve_tool_face_refuses_a_tier_member_that_is_not_mounted(
    tmp_path, ghost_tier
):
    """#74: 档内但未装载的名字与档外名字同等即拒，不再静默交集成空面。

    ``Loaded:`` 钉的是「档∩实装面」：既不含被拒的那个名字（列了等于让模型照着
    清单再递一次、第二次撞档外拒绝），也不含 ``write`` / ``bash`` 这些永远
    收窄不进去的档外名。清单是字面量，不用 ``tier_face_names`` 自算。
    """
    tm = make_manager(make_settings(tmp_path), _CountingMock())
    names = [t.name for t in tm._tools]
    assert "ghost_tool" in TOOL_TIERS[EXPLORE_TIER] and "ghost_tool" not in names

    with pytest.raises(TierError) as excinfo:
        resolve_tool_face(tm._tools, EXPLORE_TIER, ["ghost_tool"])
    message = str(excinfo.value)
    assert "ghost_tool" in message
    assert "not mounted" in message
    assert message.endswith("Loaded: glob, grep, load_skill, ls, read, web_search")


def test_out_of_tier_refusal_lists_the_loaded_face_too(tmp_path, ghost_tier):
    """#94: 档外拒绝的理由清单与上面 ``Loaded:`` 同源同口径——档位 ∩ 实装面。

    这份清单的承诺是「供你改投」，所以档位全貌里那些声明了却没装载的名字
    不能进消息：模型照单改投，会再吃一次未装载拒绝、白跳一轮。被拒的名字
    照旧点名，清单也照旧是字面量，不用 ``tier_face_names`` 自算。
    """
    tm = make_manager(make_settings(tmp_path), _CountingMock())
    names = [t.name for t in tm._tools]
    assert "ghost_tool" in TOOL_TIERS[EXPLORE_TIER] and "ghost_tool" not in names

    with pytest.raises(TierError) as excinfo:
        resolve_tool_face(tm._tools, EXPLORE_TIER, ["read", "bash"])
    message = str(excinfo.value)

    assert "bash" in message  # 拒的还是拒，被拒的名照旧在
    assert "ghost_tool" not in message  # 但清单里没有改投不进去的名字
    assert message.endswith("Loaded: glob, grep, load_skill, ls, read, web_search")


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
def test_no_tier_mounts_a_danger_marked_tool(tmp_path):
    """任何档位都拿不到被标成危险的工具（#56 收窄；#138 之后主图也无闸门了）。"""
    seen_marked: set = set()
    for label, tm, ex in _mounted_managers(tmp_path):
        marked = {
            t.name
            for t in tm._tools
            if getattr(t, "requires_confirm", False)
        }
        assert marked, f"{label} 装载面没有危险标记工具，下面的断言会空转"
        seen_marked |= marked
        names = [t.name for t in tm._tools]
        for tier in (EXPLORE_TIER, EXECUTE_TIER):
            face = ex.check_face(_spec(subtask_id=f"{label}:sub:1", tier=tier))
            assert face == tier_face_names(names, tier)
            leaked = set(face) & marked
            assert not leaked, f"{label}/{tier} 档漏进了危险标记的工具 {sorted(leaked)}"
    # The dangerous families the issue names are all really present somewhere above.
    assert {"bash", "spawn_subagent"} <= seen_marked


def test_external_write_family_is_out_of_reach_in_every_tier(tmp_path):
    """档外即不可达：外部写 / 逃逸舱 / 生成型工具一个都进不了子任务。"""
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


def test_spawned_subtask_face_carries_the_sandbox_writes_only(tmp_path):
    """execute 档 = explore 档 + write/edit；ls 属 explore，file_io 已整个退役。"""
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _CountingMock())
    ex = SubAgentExecutor(tm, settings)
    execute = ex.check_face(_spec(tier=EXECUTE_TIER))
    explore = ex.check_face(_spec(tier=EXPLORE_TIER))

    assert set(execute) - set(explore) == {"write", "edit"}
    assert "ls" in explore  # 目录可见性是只读档的原语（#17）
    assert "file_io" not in {t.name for t in tm._tools}
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
    widened = ex.run_subtask(_spec(subtask_id="bad:sub:2", tier=EXECUTE_TIER, tools=["bash"]))

    assert bad_tier.status == "failed" and "oracle" in bad_tier.error
    assert widened.status == "failed" and "bash" in widened.error
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

    res = tool.run(name="widening", instruction="try it", tools=["read", "bash"])

    assert res.success is False
    assert "bash" in (res.error or "") and "read" in (res.error or "")
    assert started == []
    assert mock.turns == 0
    assert list(settings.artifacts_path.glob("**/*") if settings.artifacts_path.exists() else []) == []


def test_spawn_subagent_refuses_an_unmounted_tier_member_before_running(
    tmp_path, ghost_tier, monkeypatch
):
    """#74: 档内但本次没装载的名字与档外同等处理——预检即拒，探子都不派。

    与上一条同形，只是被拒的名字这次**在档内**（planted ``ghost_tool``，本仓
    shipped tiers 只点名已挂载的工具）。改前这里静默交集出空面、子任务照跑照
    `completed`，父模型收到的是假成功。
    """
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="never")
    tm = make_manager(settings, mock)
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    started: List[SubTaskSpec] = []
    assert tm._subagent is not None
    monkeypatch.setattr(tm._subagent, "run_subtask", _recording_run(started))

    res = tool.run(name="probe", instruction="try it", tier=EXPLORE_TIER, tools=["ghost_tool"])

    assert res.success is False
    assert "ghost_tool" in (res.error or "") and "not mounted" in (res.error or "")
    assert started == []
    assert mock.turns == 0
    assert list(settings.artifacts_path.glob("**/*") if settings.artifacts_path.exists() else []) == []


def test_unmounted_name_folds_the_same_reason_on_both_paths(tmp_path, ghost_tier):
    """双路径一致（#74 AC3）：不经工具层直调 ``run_subtask``，理由逐字相同。"""
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="never")
    tm = make_manager(settings, mock)
    ex = SubAgentExecutor(tm, settings)
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")

    direct = ex.run_subtask(
        _spec(subtask_id="um:sub:1", tier=EXPLORE_TIER, tools=["ghost_tool"])
    )
    via_tool = tool.run(
        name="um", instruction="i", tier=EXPLORE_TIER, tools=["ghost_tool"]
    )

    assert direct.status == "failed" and direct.tool_face == []
    assert via_tool.success is False
    assert direct.error == (via_tool.error or "")
    assert "ghost_tool" in direct.error and "not mounted" in direct.error
    assert mock.turns == 0


class _UnmountedAskMock(MockLLMClient):
    """父模型递一个「档位声明了、本次却没装载」的名字（planted ``ghost_tool``）。

    只在第一轮递 spawn，之后一律直接答——所以「探子有没有被派出去」由事件频道
    分辨，不由轮次分辨。
    """

    def __init__(self) -> None:
        super().__init__(plan=["委托"], final_answer="父任务自己收场")
        self.rounds = 0

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        self.rounds += 1
        if self.rounds == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "um-spawn",
                        "name": "spawn_subagent",
                        "arguments": {
                            "name": "probe",
                            "instruction": "查一下不存在的工具",
                            "tier": EXPLORE_TIER,
                            "tools": ["ghost_tool"],
                        },
                    }
                ],
            )
        return LLMResponse(content=self.final_answer)


def test_refused_spawn_lands_the_reason_on_the_event_stream_and_starts_nothing(
    tmp_path, event_bus, ghost_tier
):
    """票面「观测」条：被拒的 spawn 不派出探子，失败走 ``tool_result`` 事件——这里钉住事件。

    被拒的 spawn 在事件里带 error 文本；它没有派出探子，所以父频道上连一条
    subtask 事件都没有。
    """
    settings = make_settings(tmp_path)
    mock = _UnmountedAskMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="t", user_input="派个子任务查不存在的工具")
    _run_until_done(tm, task_id)

    events = event_bus.replay(task_id)
    spawn = [
        e["data"]
        for e in events
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    ]
    assert len(spawn) == 1  # 父模型没有被同一个拒绝反复放行
    assert spawn[0]["status"] == "failed"
    assert "ghost_tool" in spawn[0]["error"] and "not mounted" in spawn[0]["error"]
    assert not [e for e in events if "subtask" in e["type"]]
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
    assert {"read", "write", "ls"} <= names


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
                # The out-of-tier family: shell access could write anything.
                # It does not exist on this graph's face.
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "out1",
                            "name": "bash",
                            "arguments": {
                                "command": f"echo nope > {self.SIDE_EFFECT}",
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


def test_approved_spawn_cannot_reach_external_writes_and_has_no_side_effect(tmp_path, event_bus):
    """票面验收 a：主任务批准派生后，子任务要不到「写外部系统」的工具，也没留下副作用。"""
    settings = make_settings(tmp_path)
    mock = _EscalatingSubtaskMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="probe", user_input="派一个子任务去探针")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "INTERRUPTED"
    parent_events = event_bus.replay(task_id)

    # The delegation ran as an ordinary tool call (the spawn gate is gone, #138).
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
    for name in ("bash", "spawn_subagent"):
        assert name not in face
        assert name not in mock.offered_to_subtask
    assert set(mock.offered_to_subtask) <= set(TOOL_TIERS[EXECUTE_TIER])

    # 2) No side effect: the attempt failed as "unknown tool", nothing ran.
    sub_events = event_bus.replay(subtask_id)
    refused = [
        e["data"]
        for e in sub_events
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "bash"
    ]
    assert {r["tool_name"] for r in refused} == {"bash"}
    assert all(r["status"] == "failed" and "unknown tool" in r["error"] for r in refused)
    assert not (settings.artifacts_path / mock.SIDE_EFFECT).exists()

    # 3) The tier-legal write did happen, and it handed back to the parent.
    assert (settings.artifacts_path / mock.REPORT).exists()
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
    # The subtask's own channel carries its own rounds and tool calls.
    assert any(e["type"] == "step_start" for e in sub_events)


def test_subtask_writes_and_events_do_not_pollute_the_parent(tmp_path, event_bus):
    """A direct subtask run keeps its rounds on its own channel; its write lands
    in the shared workspace and never fires a parent-channel event."""
    settings = make_settings(tmp_path)
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
    assert (settings.artifacts_path / "direct_note.md").is_file()

    parent_types = {e["type"] for e in event_bus.replay(task_id)}
    assert "tool_call" not in parent_types  # internal rounds stayed away

    sub_types = {e["type"] for e in event_bus.replay("iso:sub:1")}
    assert {"step_start", "tool_call", "tool_result"} <= sub_types


def test_subtask_that_only_reads_hands_no_product_to_the_parent(tmp_path, event_bus):
    """#72: 「产物 = 写出的文件」也管子任务。explore 档只读，读过的文件既不是
    子任务的产物，也不该向父任务登记——父任务频道不该出现 artifact_created。

    The read itself must succeed, otherwise the empty product list would pass on
    a subtask that never ran the tool."""
    settings = make_settings(tmp_path)
    mock = _CountingMock(plan=["p"], final_answer="读完了")
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="parent", user_input="父任务：只要一句话结论")
    _run_until_done(tm, task_id)

    seen = settings.artifacts_path / "already_there.txt"
    seen.parent.mkdir(parents=True, exist_ok=True)
    seen.write_text("body", encoding="utf-8")

    mock.tool_calls = [{"id": "r1", "name": "read", "arguments": {"path": "already_there.txt"}}]
    ex = SubAgentExecutor(tm, settings)
    res = ex.run_subtask(
        _spec(
            subtask_id="read:sub:1",
            name="read only",
            instruction="读那个文件并复述一行",
            parent_task_id=task_id,
            tier=EXPLORE_TIER,
        )
    )

    assert res.status == "completed"
    assert res.artifacts == []

    sub_reads = [
        e["data"]
        for e in event_bus.replay("read:sub:1")
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "read"
    ]
    assert sub_reads and sub_reads[0]["status"] == "success"


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
    # #139: one 0.4s LLM round per subtask (the automatic planner round is
    # gone). With 2 workers ~0.4s, not ~0.8s.
    assert elapsed < 0.7, f"subtasks did not overlap (elapsed={elapsed:.2f}s)"


def test_run_subtask_is_bounded_by_the_wall_clock_timeout(tmp_path):
    """`subagent_timeout_sec` 现役的执行点：跑不完就折成 failed，不挂住父线程。"""
    settings = make_settings(tmp_path, subagent_timeout_sec=1)
    # #139: one LLM round now, so the single call must outlast the bound.
    tm = make_manager(settings, _SleepingMock(sleep=1.4))
    ex = SubAgentExecutor(tm, settings)

    res = ex.run_subtask(_spec(subtask_id="timeout:sub:1", instruction="think slowly"))

    assert res.status == "failed"
    assert "timeout" in res.error


def test_serial_subtasks_when_concurrency_1(tmp_path):
    settings = make_settings(tmp_path, subagent_max_concurrency=1)
    tm = make_manager(settings, _SleepingMock(sleep=0.4))
    results, elapsed = _run_two_specs_through_the_pool(settings, tm)

    assert len(results) == 2
    # One 0.4s round each, serialized: ~0.8s. Parallel would be ~0.4s.
    assert elapsed >= 0.7, f"subtasks ran in parallel despite concurrency=1 (elapsed={elapsed:.2f}s)"


# ── the shared graph, stop propagation and failure folding ───────────────────
def test_graph_compiles_without_a_confirm_node(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _CountingMock())
    runtime = AgentRuntime(
        task_id="sub",
        task_manager=tm,
        llm=tm._llm,
        tools=tm._tools,
        tool_schemas=tm._tool_schemas,
    )
    graph = build_graph(runtime)
    assert graph is not None
    nodes = set(graph.get_graph().nodes)
    assert "human_confirm" not in nodes
    assert "risk_scan" not in nodes


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


class _TimeoutLoopMock(_KeepLoopingMock):
    """#71 版死循环：每轮再睡 ``per_round``，并可被 ``halt`` 提前放手。

    轮数 × ``per_round`` 必须明显大于 ``subagent_timeout_sec``，否则折叠发生时
    worker 早已自然跑完，收窗断言失去对象（测试用 ``at_fold`` 把这件事变成机械检查）。

    ``halt`` 是给「红跑」留的阀：没收窗机制的那一侧会把 ``max_steps`` 轮一路跑满，
    set 它让循环立刻收敛，测试不必真占着池位等一个坏行为跑完。
    """

    def __init__(self, per_round: float = 0.2, halt: threading.Event | None = None) -> None:
        super().__init__()
        self.per_round = per_round
        self.halt = halt

    def complete(self, messages, tools=None, **kwargs):
        time.sleep(self.per_round)
        if tools and self.halt is not None and self.halt.is_set():
            return LLMResponse(content=self.final_answer)
        return super().complete(messages, tools, **kwargs)


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


def test_timeout_fold_stops_the_worker_at_the_next_node_boundary(tmp_path):
    """#71 AC：超时折 failed 之后，池 worker 必须真的退出，而不是继续烧轮次。

    「旗标消失」是 worker 退出的**目击证人**而非定义：清旗挂在子任务 future 的 done
    callback 上，回调在 ``_exec_one`` 返回后、同一池线程取下一件工作之前触发，所以
    等到旗标不见 = 等到那次退出 = 池位归还。（反向不成立：callback 抛异常会被
    concurrent.futures 吞掉，那时这里读到的是「还活着」。）

    两条断言互为保险：置旗/清旗钉住所选机制，``executor_calls`` 的增量与机制无关，
    单独就能证明窗口真收窄了，而不是「worker 恰好自己跑完了」。残余窗口按代码事实是
    两段（在途那次 LLM 调用 + 已过 ``tool_node`` 入口检查的批次），本 mock 每轮只排
    一个工具调用，走的是「旗标先于入口命中 ⇒ 整批跳过」这条更窄的路径。
    """
    sid = "timeout71:sub:1"
    settings = make_settings(tmp_path, subagent_timeout_sec=1, max_steps=15)
    halt = threading.Event()
    mock = _TimeoutLoopMock(per_round=0.2, halt=halt)
    tm = make_manager(settings, mock)
    ex = SubAgentExecutor(tm, settings)
    try:
        res = ex.run_subtask(_spec(subtask_id=sid, instruction="keep looping"))
        assert res.status == "failed"
        assert "timeout" in res.error

        at_fold = mock.executor_calls
        assert at_fold < settings.max_steps, (
            f"折叠前死循环就自己跑满了 {settings.max_steps} 轮，收窗断言失去对象"
        )
        assert tm.is_stop_flagged(sid), (
            "超时分支没置子任务自身的 stop 标志："
            "worker 只能在 max_steps 处自己收场，池位一路被占着"
        )

        deadline = time.time() + 3.0
        while tm.is_stop_flagged(sid) and time.time() < deadline:
            time.sleep(0.02)
        assert not tm.is_stop_flagged(sid), (
            "旗标没被清：worker 还没退出（done callback 没跑），"
            "或子任务 id 泄漏在进程级 _stop_flags 里没人 pop"
        )
        # 在途那一轮的宽限；口径对齐 #60 的 `executor_calls < 5`。
        extra = mock.executor_calls - at_fold
        assert extra <= 2, f"折叠后又烧了 {extra} 轮，窗口没收窄到下一个节点边界"
    finally:
        halt.set()


def test_a_queued_subtask_that_never_starts_leaves_no_stop_flag(tmp_path):
    """``future.cancel()`` 成功 = 从未开跑：既不置旗，也不在进程级字典里留脏 key。"""
    sid = "queue71:sub:1"
    settings = make_settings(
        tmp_path, subagent_timeout_sec=1, subagent_max_concurrency=1
    )
    mock = _CountingMock()
    tm = make_manager(settings, mock)
    ex = SubAgentExecutor(tm, settings)
    gate = threading.Event()

    def _hold_the_only_slot() -> str:
        gate.wait(10)
        return "released"

    holder = ex._pool.submit(_hold_the_only_slot)  # 占住唯一池位 → 下面的 spec 只能排队
    try:
        res = ex.run_subtask(_spec(subtask_id=sid, instruction="never starts"))
        assert res.status == "failed"
        assert "timeout" in res.error
        assert not tm.is_stop_flagged(sid), "排队未启动的 future 被误置旗"
        assert mock.turns == 0, "被取消的排队项不该跑过一轮"
    finally:
        gate.set()
        holder.result(timeout=10)


def test_subtask_crash_folds_back_as_failed_and_the_parent_survives(
    tmp_path, event_bus, monkeypatch
):
    """崩掉的子任务只折回成一次失败的工具调用，父任务必须自己收场。

    #137 重裁（#46 曾把这里改判成 UNVERIFIED）：验证层退役后不再有机器判决，
    「零成功却声称完成」照常收尾成 COMPLETED；「不跟着子任务一起崩」这条钉子没动
    （终态不是 FAILED、也没有异常）。
    """
    settings = make_settings(tmp_path)
    mock = _EscalatingSubtaskMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    assert tm._subagent is not None

    def _boom(spec, publish=None):
        raise RuntimeError("subtask worker crashed")

    monkeypatch.setattr(tm._subagent, "_exec_one", _boom)
    task_id = tm.create_task(title="crash", user_input="派一个子任务去探针")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "INTERRUPTED"
    # #70 裁定 A 重裁（原断 == []，钉 #56「无生产者」）：崩掉的子任务也如实登一条
    # failed——派出去过又折回来的，面板与 REST 都得说得出。
    assert len(task.subtasks) == 1
    assert task.subtasks[0].status == "failed"
    assert "crashed" in (task.subtasks[0].error or "")
    results = [
        e["data"]
        for e in event_bus.replay(task_id)
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    ]
    assert results and results[0]["status"] == "failed"
    assert "crashed" in results[0]["error"]
    # #70 裁定 A 重裁（原断「父频道没有任何 subtask 事件」，钉 #56 的退役词汇表）：
    # 崩溃的派生同样在父频道留下汇总事件——一条 start、一条 failed，没有 result；
    # 「父频道上只剩这次失败」的那条失败 tool_result 原样还在（上面已断）。
    parent_events = event_bus.replay(task_id)
    types = [e["type"] for e in parent_events]
    assert types.count("subtask_start") == 1
    assert types.count("subtask_failed") == 1
    assert "subtask_result" not in types
    assert not (settings.artifacts_path / _EscalatingSubtaskMock.REPORT).exists()


def test_subtask_failure_reports_the_cause_not_a_silent_success(tmp_path):
    """A failed subtask must fold back as failed with the reason (Issue #12)."""
    settings = make_settings(tmp_path)

    class _CrashMock(MockLLMClient):
        def complete(self, messages, tools=None, **kwargs):
            # #139: there is one LLM entry, so a provider failure fires there.
            raise RuntimeError("simulated provider 403")

    tm = make_manager(settings, _CrashMock(plan=["p"]))
    res = tm._subagent.run_subtask(_spec(subtask_id="fail:sub:1", tier=EXPLORE_TIER))
    assert res.status == "failed"
    assert "403" in res.error
    assert "403" in res.summary
    # The run did start, on the explore face only (no writes).
    assert res.tool_face == tier_face_names(
        [t.name for t in tm._tools], EXPLORE_TIER
    )
    assert "write" not in res.tool_face

