"""Offline smoke test — exercises the full LangGraph kernel with a scripted
MockLLMClient (no API key, no network).

Run:
    python backend/tests/test_smoke.py
or:
    pytest backend/tests/test_smoke.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Ensure we never accidentally hit a real LLM during the test.
os.environ.setdefault("USE_MOCK_LLM", "false")

from backend.config import get_settings  # noqa: E402
from backend.core.llm.client import MockLLMClient  # noqa: E402
from backend.core.tools.registry import build_tools  # noqa: E402
from backend.services.event_bus import EventBus  # noqa: E402
from backend.services.persistence import Persistence  # noqa: E402
from backend.services.task_manager import TaskManager  # noqa: E402


def build_manager() -> TaskManager:
    settings = get_settings()
    eb = EventBus()
    persistence = Persistence(settings)

    mock = MockLLMClient(
        plan=["Create a text file with the requested content"],
        tool_calls=[
            {
                "id": "c1",
                "name": "file_io",
                "arguments": {
                    "action": "write",
                    "path": "hello.txt",
                    "content": "Hello from the autonomous agent!",
                },
            }
        ],
        final_answer="I created hello.txt with the requested content.",
    )
    tools = build_tools(settings)
    return TaskManager(settings, eb, persistence, llm_client=mock, tools=tools), eb


def main() -> int:
    tm, eb = build_manager()

    task_id = tm.create_task(
        title="smoke", user_input="create a text file and write content"
    )

    # Wait for the background run to finish.
    for _ in range(200):  # up to ~20s
        time.sleep(0.1)
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            break

    events = eb.replay(task_id)
    event_types = {e["type"] for e in events}

    task = tm.get_task(task_id)
    print("\n=== SMOKE TEST REPORT ===")
    print("task_id     :", task_id)
    print("final status:", task.status.value if task else "MISSING")
    print("event types :", sorted(event_types))
    print("artifacts   :", [a.filename for a in (task.artifacts if task else [])])

    hello = get_settings().artifacts_path / "hello.txt"
    file_ok = hello.exists() and "autonomous agent" in hello.read_text(encoding="utf-8")

    checks = {
        "task persisted": task is not None,
        "status COMPLETED": task is not None and task.status.value == "COMPLETED",
        "plan_update event": "plan_update" in event_types,
        "tool_call event": "tool_call" in event_types,
        "tool_result event": "tool_result" in event_types,
        "artifact_created event": "artifact_created" in event_types,
        "final_answer event": "final_answer" in event_types,
        "task_completed event": "task_completed" in event_types,
        "hello.txt written": file_ok,
    }

    ok = True
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    print("=== RESULT:", "PASS" if ok else "FAIL", "===\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
