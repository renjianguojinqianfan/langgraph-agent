"""Live end-to-end verification against a real OpenAI-compatible LLM host
(validated with the QianWen / DashScope qwen3.6-plus model).

Run (Linux/macOS bash or Git Bash):
    LLM_API_KEY="$DASHSCOPE_API_KEY" python scripts/live_e2e.py
or, after putting llm_api_key into .env:
    python scripts/live_e2e.py

Requirements:
  - An OpenAI-compatible endpoint + API key (env LLM_API_KEY or .env llm_api_key)
  - .env configured with llm_base_url (e.g. https://dashscope.aliyuncs.com/compatible-mode/v1)
    and llm_model (e.g. qwen3.6-plus), use_mock_llm=false
  - The chosen model must have quota on the account

This script drives the REAL LangGraph kernel end-to-end with a natural-language
task, verifying planner -> executor -> tool -> reflect -> final_answer and that
the file_io tool actually writes an artifact.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts/ -> project root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("USE_MOCK_LLM", "false")

from backend.config import get_settings  # noqa: E402
from backend.core.llm.openai_compat import create_llm_client  # noqa: E402
from backend.core.tools.registry import build_tools  # noqa: E402
from backend.services.event_bus import EventBus  # noqa: E402
from backend.services.persistence import Persistence  # noqa: E402
from backend.services.task_manager import TaskManager  # noqa: E402


def main() -> int:
    settings = get_settings()
    print(f"LLM config: provider={settings.llm_provider} model={settings.llm_model} base_url={settings.llm_base_url}")
    print(f"use_mock_llm={settings.use_mock_llm} key_set={'yes' if settings.llm_api_key else 'NO'}")

    if settings.use_mock_llm or not settings.llm_api_key:
        print("ABORT: use_mock_llm must be false and llm_api_key must be set (via LLM_API_KEY env or .env).")
        return 2

    eb = EventBus()
    persistence = Persistence(settings)
    llm = create_llm_client(settings)
    tools = build_tools(settings)
    tm = TaskManager(settings, eb, persistence, llm_client=llm, tools=tools)

    task_id = tm.create_task(
        title="live-qianwen-e2e",
        user_input="Write a short summary of your own capabilities into a file named agent_summary.txt, "
        "then tell me what you did.",
    )
    print(f"task_id: {task_id}\n")

    for _ in range(600):  # up to ~60s
        time.sleep(0.1)
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            break

    task = tm.get_task(task_id)
    events = eb.replay(task_id)
    event_types = {e["type"] for e in events}

    print("\n=== LIVE E2E REPORT ===")
    print("final status:", task.status.value if task else "MISSING")
    print("event types :", sorted(event_types))

    artifacts = [a.filename for a in (task.artifacts if task else [])]
    print("artifacts   :", artifacts)

    final_answers = [e for e in events if e["type"] == "final_answer"]
    if final_answers:
        print("\n--- final_answer (truncated) ---")
        print(str(final_answers[-1]["data"])[:500])

    # Verify the artifact actually landed.
    artifact = None
    for a in (task.artifacts if task else []):
        if a.filename == "agent_summary.txt":
            artifact = a
            break
    file_ok = False
    if artifact:
        p = settings.artifacts_path / artifact.filename
        file_ok = p.exists() and p.read_text(encoding="utf-8").strip() != ""

    checks = {
        "task persisted": task is not None,
        "status COMPLETED": task is not None and task.status.value == "COMPLETED",
        "plan_update event": "plan_update" in event_types,
        "tool_call event": "tool_call" in event_types,
        "tool_result event": "tool_result" in event_types,
        "artifact_created event": "artifact_created" in event_types,
        "final_answer event": "final_answer" in event_types,
        "task_completed event": "task_completed" in event_types,
        "agent_summary.txt written": file_ok,
    }

    ok = True
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    print("=== RESULT:", "PASS" if ok else "FAIL", "===\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
