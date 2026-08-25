"""Live skill-grounded model test — skills -> KB -> real LLM.

Validates the full chain the .agents/skills assets enable:

1. Ingest the QianWen skill references (model-list / pricing /
   recommendation-matrix ...) into the agent's knowledge base (P1 RAG).
2. Drive a REAL LLM (qwen3.6-plus via DashScope) task that forces the model
   to consult the KB tools (memory_search / kb_query) before answering.
3. Assert the event stream shows a KB tool call and the final answer names
   a concrete Qwen model.

Run:
    LLM_API_KEY="$DASHSCOPE_API_KEY" python scripts/live_skill_test.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("USE_MOCK_LLM", "false")

from backend.config import get_settings  # noqa: E402
from backend.core.kb.knowledge_base import KnowledgeBase  # noqa: E402
from backend.core.llm.openai_compat import create_llm_client  # noqa: E402
from backend.core.tools.registry import build_tools  # noqa: E402
from backend.services.event_bus import EventBus  # noqa: E402
from backend.services.persistence import Persistence  # noqa: E402
from backend.services.task_manager import TaskManager  # noqa: E402

SKILL_REFS = ROOT / ".agents" / "skills" / "qianwen-model-selector" / "references"
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
    docs = kb.retrieve("qwen text chat model recommendation", top_k=5)
    return copied, dest


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

    for _ in range(900):  # up to ~90s
        time.sleep(0.1)
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            break

    task = tm.get_task(task_id)
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

    print("=== RESULT:", "PASS" if ok else "FAIL", "===\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
