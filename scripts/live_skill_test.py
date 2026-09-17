"""Live skill-grounded model test — skills -> KB -> real LLM.

Validates the full chain the .agents/skills assets enable:

1. Ingest the QianWen skill references (model-list / pricing /
   recommendation-matrix ...) into the agent's knowledge base (P1 RAG).
2. Drive a REAL LLM (qwen3.6-plus via DashScope) task that forces the model
   to consult the KB tools (memory_search / kb_query) before answering.
3. Assert the event stream shows a KB tool call and the final answer names
   a concrete Qwen model.

Second leg (P1-A skills runtime, spec ``docs/specs/p1-a-skills-runtime.md``
§5.5): the repo's ``qianwen-model-selector/SKILL.md`` is copied into a
throwaway workspace's ``.agents/skills/`` layer, and the real model must call
``load_skill``, receive the body verbatim and quote a real line from it.

Run:
    LLM_API_KEY="$DASHSCOPE_API_KEY" python scripts/live_skill_test.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("USE_MOCK_LLM", "false")

from backend.config import Settings, get_settings  # noqa: E402
from backend.core.agent.inject import build_inject_block  # noqa: E402
from backend.core.kb.knowledge_base import KnowledgeBase  # noqa: E402
from backend.core.llm.openai_compat import create_llm_client  # noqa: E402
from backend.core.tools.registry import build_tools  # noqa: E402
from backend.services.event_bus import EventBus  # noqa: E402
from backend.services.persistence import Persistence  # noqa: E402
from backend.services.task_manager import TaskManager  # noqa: E402

SKILL_REFS = ROOT / ".agents" / "skills" / "qianwen-model-selector" / "references"
SKILL_MAIN = ROOT / ".agents" / "skills" / "qianwen-model-selector" / "SKILL.md"
KB_TARGET_REFS = ("model-list.md", "pricing.md", "recommendation-matrix.md")


def ingest_skills_into_kb() -> tuple[int, Path]:
    """Copy skill references into the KB dir and (re)build the index."""
    settings = get_settings()
    kb_dir = settings.kb_path
    dest = kb_dir / "qianwen-skills"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in KB_TARGET_REFS:
        src = SKILL_REFS / name
        if src.exists():
            shutil.copy2(src, dest / name)
            copied += 1
    kb = KnowledgeBase(settings)
    kb.rebuild()  # rescan the whole kb dir and rebuild .index.json
    # 只验“重建后的索引可查”（不抛异常即通过）；命中数由 main() 里的真实模型环节验。
    kb.retrieve("qwen text chat model recommendation", top_k=5)
    return copied, dest


def _wait_for_completion(
    tm: TaskManager, event_bus: EventBus, task_id: str, *, timeout_sec: float
) -> Any:
    """Poll to a terminal state, answering confirm gates the way an operator would.

    The real model occasionally reaches for a confirm-gated tool (e.g.
    ``code_exec``) mid-task; a live script has no human, so it approves pending
    confirmations explicitly — the production gate is still consulted, just
    answered. Without this, one gated call parks the whole batch, including the
    ``load_skill`` call queued behind it.
    """
    deadline = time.time() + timeout_sec
    seen = 0
    task = tm.get_task(task_id)
    while time.time() < deadline:
        time.sleep(0.1)
        events = event_bus.replay(task_id)
        for e in events[seen:]:
            if e["type"] != "human_confirm_required":
                continue
            key = e["data"].get("tool_call_id")
            if key:
                tm.confirm(task_id, key, True)
                print(f"  [gate] approved {e['data'].get('tool_name')} ({key})")
        seen = len(events)
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return task
    return task


def _skills_runtime_leg() -> bool:
    """P1-A second leg: a real model must load a skill and quote its body.

    The repo's ``qianwen-model-selector/SKILL.md`` is copied into a throwaway
    workspace (``<tmp>/workspace/.agents/skills/...``) so the workspace layer of
    the discovery chain serves both the catalogue and the ``load_skill`` tool
    deterministically. Unrelated subsystems (checkpoints / risk scan / subagents
    / MCP / git) are switched off — this leg verifies the skills runtime only.
    """
    root = Path(tempfile.mkdtemp(prefix="skill-runtime-leg-"))
    workspace = root / "workspace"
    dest = workspace / ".agents" / "skills" / "qianwen-model-selector" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILL_MAIN, dest)
    skill_text = dest.read_text(encoding="utf-8")

    settings = Settings(
        data_dir=str(root / "data"),
        artifacts_dir=str(workspace),
        trace_dir=str(root / "traces"),
        kb_dir=str(root / "kb"),
        checkpoint_enabled=False,
        risk_scan_enabled=False,
        subagent_enabled=False,
        mcp_enabled=False,
        git_enabled=False,
    )

    # Offline sanity: the catalogue must list the card before the model can use it.
    catalogued = "- qianwen-model-selector:" in build_inject_block(settings, home_dir=root / "empty-home")

    eb = EventBus()
    persistence = Persistence(settings)
    llm = create_llm_client(settings)
    tools = build_tools(settings)
    tm = TaskManager(settings, eb, persistence, llm_client=llm, tools=tools)

    task_id = tm.create_task(
        title="live-skill-runtime-test",
        user_input=(
            "用 load_skill 工具读取 qianwen-model-selector 技能的完整正文，并按其指示推荐一个"
            "适合日常文本对话的千问模型；回答里请原样引用正文中的一句话，用 <quote>...</quote> "
            "包裹。不要执行代码，读取后直接回答即可。"
        ),
    )
    print(f"[leg2] task_id={task_id} — waiting for the real model...")

    task = _wait_for_completion(tm, eb, task_id, timeout_sec=150)
    events = eb.replay(task_id)
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    load_results = [
        e for e in events if e["type"] == "tool_result" and e["data"].get("tool_name") == "load_skill"
    ]
    answers = [e for e in events if e["type"] == "final_answer"]
    answer_text = str(answers[-1]["data"]) if answers else ""

    verbatim = False
    if load_results:
        payload = load_results[-1]["data"].get("output") or {}
        docs = payload.get("documents") or []
        # A home-layer copy of the same skill would be returned first (#5 keeps
        # duplicates); the workspace copy must be among the returned documents.
        verbatim = any(d.get("content") == skill_text for d in docs)

    quoted = False
    if "<quote>" in answer_text and "</quote>" in answer_text:
        inner = answer_text.split("<quote>", 1)[1].split("</quote>", 1)[0].strip()
        quoted = bool(inner) and inner in skill_text

    checks = {
        "catalogue card injectable (offline)": catalogued,
        "task COMPLETED": task is not None and task.status.value == "COMPLETED",
        "real LLM called load_skill": any(e["data"].get("tool_name") == "load_skill" for e in tool_calls),
        "tool_result == SKILL.md verbatim": verbatim,
        "answer quotes a real body line": quoted,
    }
    print("\n=== SKILLS RUNTIME LEG (P1-A) ===")
    print("tool calls:", [e["data"].get("tool_name") for e in tool_calls])
    print("--- final_answer (truncated 600 chars) ---")
    print(answer_text[:600])
    ok = True
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    return ok


def main() -> int:
    settings = get_settings()
    print(f"LLM: model={settings.llm_model} base_url={settings.llm_base_url} mock={settings.use_mock_llm}")
    if settings.use_mock_llm or not settings.llm_api_key:
        print("ABORT: needs a real LLM (LLM_API_KEY env + .env with use_mock_llm=false).")
        return 2

    # 1. skills -> KB
    copied, dest = ingest_skills_into_kb()
    print(f"[1] ingested {copied} skill reference(s) into KB at {dest}")

    # 2. real-LLM task that must consult the KB
    eb = EventBus()
    persistence = Persistence(settings)
    llm = create_llm_client(settings)
    tools = build_tools(settings)
    tm = TaskManager(settings, eb, persistence, llm_client=llm, tools=tools)
    print(f"[2] tools available: {[t.name for t in tools]}")

    task_id = tm.create_task(
        title="live-skill-kb-test",
        user_input=(
            "请先使用 memory_search 或 kb_query 工具检索知识库中关于千问(Qwen)模型的信息，"
            "然后根据检索到的内容，推荐一个适合日常文本对话的千问模型，"
            "并简要说明推荐理由（模型名必须来自知识库内容）。"
        ),
    )
    print(f"[3] task_id={task_id} — waiting for the real model...")

    task = _wait_for_completion(tm, eb, task_id, timeout_sec=120)
    events = eb.replay(task_id)
    event_types = {e["type"] for e in events}
    tool_calls = [e["data"].get("tool_name") for e in events if e["type"] == "tool_call"]
    final_answers = [e for e in events if e["type"] == "final_answer"]

    print("\n=== LIVE SKILL TEST REPORT ===")
    print("final status:", task.status.value if task else "MISSING")
    print("event types :", sorted(event_types))
    print("tool calls  :", tool_calls)

    kb_tools_used = [t for t in tool_calls if t in ("memory_search", "kb_query")]
    answer_text = str(final_answers[-1]["data"]) if final_answers else ""
    print("\n--- final_answer (truncated 600 chars) ---")
    print(answer_text[:600])

    qwen_models = ["qwen3.6-plus", "qwen3.5-flash", "qwen-turbo", "qwen3-max",
                   "qwen3.5-plus", "qwen3.6-flash", "qwen3-coder-plus"]
    named_model = [m for m in qwen_models if m in answer_text]

    checks = {
        "task COMPLETED": task is not None and task.status.value == "COMPLETED",
        "skill refs ingested": copied == len(KB_TARGET_REFS),
        "KB tool called by real LLM": len(kb_tools_used) >= 1,
        "final answer names a Qwen model": len(named_model) >= 1,
        "final_answer event": "final_answer" in event_types,
    }
    ok = True
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    if named_model:
        print("  recommended model(s):", named_model)

    ok = _skills_runtime_leg() and ok

    print("=== RESULT:", "PASS" if ok else "FAIL", "===\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
