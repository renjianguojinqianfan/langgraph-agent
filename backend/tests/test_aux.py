"""P1 item 4 — auxiliary-model division of labour tests (fully offline).

Proves:

* with ``aux_llm_enabled=False`` (default) **zero** aux calls happen — the
  factory returns ``None`` and a full task run never touches an aux client;
* with aux configured (mock), summary and risk-semantic work are delegated to
  the aux model (``MockAuxLLMClient.call_count`` / ``roles_called`` assert it);
* a live aux config missing model/api_key degrades to ``None``.
"""

from __future__ import annotations

from backend.config import Settings
from backend.core.llm.client import MockAuxLLMClient, MockLLMClient
from backend.core.llm.openai_compat import (
    create_aux_llm_client,
    create_llm_client,
    get_aux_llm,
)
from backend.core.agent.nodes import AgentRuntime
from backend.tests.conftest import make_manager
from backend.tests.test_graph import _run_until_done


def _make_aux_settings(tmp_path, **overrides):
    base = dict(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        trace_dir=str(tmp_path / "traces"),
        kb_dir=str(tmp_path / "kb"),
        max_steps=50,
        sandbox_timeout=5,
        use_mock_llm=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_create_aux_llm_none_when_disabled():
    settings = Settings()
    assert create_aux_llm_client(settings) is None
    assert get_aux_llm(settings, main_llm=None) is None


def test_create_aux_llm_none_when_live_config_incomplete():
    settings = Settings(
        aux_llm_enabled=True,
        aux_llm_model="",  # missing model -> degrade
        aux_llm_api_key="",
    )
    assert create_aux_llm_client(settings) is None


def test_create_aux_llm_mock_when_enabled():
    settings = Settings(aux_llm_enabled=True, aux_llm_use_mock=True)
    aux = create_aux_llm_client(settings)
    assert isinstance(aux, MockAuxLLMClient)
    assert aux.call_count == 0


def test_runtime_aux_none_when_disabled(tmp_path):
    settings = _make_aux_settings(tmp_path)  # aux disabled by default
    mock = MockLLMClient(plan=["p"], final_answer="done")
    tm = make_manager(settings, mock)
    assert tm._aux_llm is None
    runtime = AgentRuntime(
        task_id="t", task_manager=tm, llm=mock, tools=tm._tools, tool_schemas=tm._tool_schemas
    )
    assert runtime.aux_llm is None  # lazy property resolves to None


def test_zero_extra_llm_calls_without_aux(tmp_path, event_bus):
    settings = _make_aux_settings(tmp_path)
    mock = MockLLMClient(plan=["p"], final_answer="done")
    tm = make_manager(settings, mock, event_bus=event_bus)
    assert tm._aux_llm is None
    task_id = tm.create_task(title="t", user_input="do something")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    # No aux client existed; therefore zero aux calls by construction.
    assert tm._aux_llm is None


def test_summary_uses_aux_when_configured(tmp_path):
    settings = _make_aux_settings(
        tmp_path,
        aux_llm_enabled=True,
        aux_llm_use_mock=True,
        context_compress_strategy="summarize",
        context_token_budget=1,  # force compression
    )
    mock = MockLLMClient(plan=["p"], final_answer="done")
    tm = make_manager(settings, mock)
    aux = tm._aux_llm
    assert isinstance(aux, MockAuxLLMClient)
    aux.role_responses["summary"] = "（摘要）任务要点已压缩。"

    runtime = AgentRuntime(
        task_id="t", task_manager=tm, llm=mock, tools=tm._tools, tool_schemas=tm._tool_schemas, aux_llm=aux
    )
    # > keep_recent(default 10) messages guarantee the summarize path runs.
    messages = [{"role": "user", "content": f"message number {i} for aux summary"} for i in range(12)]
    state: dict = {"messages": messages}
    built = runtime._build_messages(state, "system")

    assert aux.call_count > 0
    assert "summary" in aux.roles_called
    assert state.get("compressed") is True


def test_risk_semantic_uses_aux_when_configured(tmp_path, event_bus):
    settings = _make_aux_settings(
        tmp_path,
        aux_llm_enabled=True,
        aux_llm_use_mock=True,
        risk_semantic_enabled=True,
    )
    # "清理服务器上所有数据" has no keyword hit -> semantic path decides.
    mock = MockLLMClient(plan=["清理服务器上所有数据"], final_answer="done")
    tm = make_manager(settings, mock, event_bus=event_bus)
    aux = tm._aux_llm
    assert isinstance(aux, MockAuxLLMClient)
    aux.role_responses["risk"] = "high: 清理服务器数据属于高危操作"

    task_id = tm.create_task(title="t", user_input="maintenance")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    assert aux.call_count > 0
    assert "risk" in aux.roles_called
    assert any(it.level == "high" for it in task.risk_report)


def test_live_aux_config_returns_real_client():
    settings = Settings(
        aux_llm_enabled=True,
        aux_llm_model="gpt-4o-mini",
        aux_llm_api_key="sk-test",
        aux_llm_base_url="https://api.openai.com/v1",
    )
    aux = create_aux_llm_client(settings)
    assert aux is not None
    assert aux.model == "gpt-4o-mini"
