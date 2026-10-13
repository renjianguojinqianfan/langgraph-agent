"""API-layer tests using FastAPI's ``TestClient``.

The default :class:`TaskManager` is replaced with one driven by a
:class:`MockLLMClient` so every endpoint runs fully offline. Covers:

* ``POST /api/tasks`` create + unified envelope;
* ``GET /api/tasks`` list + ``GET /api/tasks/{id}`` query;
* ``POST /api/tasks/{id}/stop`` interrupt;
* ``GET /api/tasks/{id}/events`` SSE stream emitting events (tested via the
  ``sse_response`` generator the route uses, with a disconnected request so it
  terminates immediately after replaying buffered events — no network, no hang);
* ``POST /api/tasks/{id}/confirm`` envelope + 404 handling.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.api.schemas import Task, TaskStatus
from backend.api.sse import sse_response
from backend.core.llm.client import MockLLMClient
from backend.core.tools.registry import build_tools
from backend.main import app
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_settings


class _FakeRequest:
    """A request that reports itself disconnected so the SSE generator exits
    right after the replay buffer (no 15s idle wait on the client side)."""

    async def is_disconnected(self):
        return True


@pytest.fixture
def client(tmp_path):
    settings = make_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(
        plan=["Create a file"],
        tool_calls=[
            {
                "id": "c1",
                "name": "write",
                "arguments": {"path": "api_out.txt", "content": "via api"},
            }
        ],
        final_answer="Created api_out.txt.",
    )
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))

    with TestClient(app) as test_client:
        # Inject offline, deterministic components.
        test_client.app.state.settings = settings
        test_client.app.state.event_bus = eb
        test_client.app.state.persistence = persistence
        test_client.app.state.task_manager = tm
        yield test_client


def _wait_done(test_client, task_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = test_client.get(f"/api/tasks/{task_id}")
        status = r.json()["data"]["status"]
        if status in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return status
        time.sleep(0.05)
    return "TIMEOUT"


def test_create_task_returns_envelope_with_task_id(client):
    r = client.post("/api/tasks", json={"input": "write a file via api"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert "task_id" in body["data"]
    assert body["message"] == "ok"


def test_create_task_rejects_empty_input(client):
    r = client.post("/api/tasks", json={"input": "   "})
    assert r.status_code == 400


def test_create_task_accepts_declared_expectations(client):
    """#46 输入面的 REST 一侧：声明要落进任务记录并原样读得回来。"""
    tid = client.post(
        "/api/tasks",
        json={
            "input": "把结论写成 report.md",
            "expectations": [{"type": "artifact", "target": "report.md"}],
        },
    ).json()["data"]["task_id"]
    task = client.get(f"/api/tasks/{tid}").json()["data"]
    assert task["expectations"] == [{"type": "artifact", "target": "report.md"}]


def test_create_task_rejects_undecidable_expectation(client):
    """判不动的声明在门口就拒（422），不留到运行中变成一个说不清的验收失败。"""
    for body in (
        {"input": "x", "expectations": [{"type": "sha256", "target": "a.txt"}]},
        {"input": "x", "expectations": [{"type": "artifact", "target": ""}]},
    ):
        assert client.post("/api/tasks", json=body).status_code == 422


# ── #46 第二步：rubric 声明面（REST 一侧）────────────────────────────────────
def test_create_task_accepts_declared_rubric(client):
    """「做完长什么样」是一段调用方给的自然语言，也要落进任务记录、原样读得回来。
    机械 expectation 说得出「哪个文件」，说不清「报告得有料」——后者就是 rubric。"""
    tid = client.post(
        "/api/tasks",
        json={
            "input": "调研并写成 report.md",
            "expectations": [{"type": "artifact", "target": "report.md"}],
            "rubric": "报告含结论小节，且引用了至少两个来源",
        },
    ).json()["data"]["task_id"]
    task = client.get(f"/api/tasks/{tid}").json()["data"]
    assert task["rubric"] == "报告含结论小节，且引用了至少两个来源"
    # 两条声明互不覆盖：机械条件仍在同一条契约上
    assert task["expectations"] == [{"type": "artifact", "target": "report.md"}]


def test_create_task_without_rubric_keeps_today_shape(client):
    """没声明 rubric = 今天的任务：字段是空串，裁判一次都不该被叫起。"""
    tid = client.post("/api/tasks", json={"input": "首都是哪里"}).json()["data"]["task_id"]
    task = client.get(f"/api/tasks/{tid}").json()["data"]
    assert task["rubric"] == ""


def test_get_task_returns_full_task(client):
    tid = client.post("/api/tasks", json={"input": "write a file via api"}).json()["data"]["task_id"]
    r = client.get(f"/api/tasks/{tid}")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["id"] == tid
    assert "status" in body["data"]


def test_get_unknown_task_returns_404(client):
    r = client.get("/api/tasks/nope")
    assert r.status_code == 404


def test_list_tasks_envelope(client):
    client.post("/api/tasks", json={"input": "task one"})
    r = client.get("/api/tasks")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert isinstance(body["data"]["tasks"], list)


def test_stop_task_interrupts(client):
    tid = client.post("/api/tasks", json={"input": "long task"}).json()["data"]["task_id"]
    time.sleep(0.2)
    r = client.post(f"/api/tasks/{tid}/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["ok"] is True
    assert body["data"]["status"] in ("INTERRUPTED", "COMPLETED", "FAILED")


def test_stop_unknown_task_returns_404(client):
    r = client.post("/api/tasks/nope/stop")
    assert r.status_code == 404


def test_sse_response_emits_replayed_event_frames(client):
    tid = client.post("/api/tasks", json={"input": "write a file via api"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "COMPLETED"

    eb = client.app.state.event_bus

    # The TestClient fixture leaves a running event loop in the main thread, so
    # calling asyncio.run() here raises "cannot be called from a running event
    # loop". Drive the async SSE generator from a dedicated worker thread that
    # owns its own loop instead. sse_response is also constructed inside that
    # thread so its internal asyncio.Queue binds to the same loop.
    import asyncio as _asyncio
    import threading as _threading

    result: dict = {}

    def _run() -> None:
        async def _collect():
            frames = []
            # sse_response() returns a StreamingResponse; iterate its async
            # body_iterator to pull the SSE frames.
            agen = sse_response(tid, eb, _FakeRequest()).body_iterator
            for _ in range(100):
                try:
                    frame = await agen.__anext__()
                except StopAsyncIteration:
                    break
                frames.append(frame)
                if "final_answer" in frame or "task_completed" in frame:
                    break
            return frames

        result["frames"] = _asyncio.run(_collect())

    t = _threading.Thread(target=_run)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "SSE frame collection timed out"
    frames = result["frames"]

    joined = "".join(frames)
    assert "event: task_created" in joined
    assert "event: final_answer" in joined or "event: task_completed" in joined
    # Frames must follow the SSE wire format.
    assert joined.startswith("event: ")


def test_sse_route_is_registered(client):
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/tasks/{task_id}/events" in paths


def test_confirm_endpoint_returns_envelope(client):
    tid = client.post("/api/tasks", json={"input": "write a file via api"}).json()["data"]["task_id"]
    r = client.post(
        f"/api/tasks/{tid}/confirm",
        json={"tool_call_id": "cc1", "approved": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert "ok" in body["data"]


def test_health_endpoint_reports_status(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── #46 第二步（切片 B）：人工复核出口 POST /api/tasks/{id}/review ──────────────
def _seed_unverified(client, task_id: str = "rev1") -> str:
    """Put a settled UNVERIFIED record in the store without running a graph for it.

    The route's subject is the HTTP face of the door (status mapping + the envelope),
    so the record is seeded; the state machine behind it is covered end-to-end in
    ``test_unverified_review.py``.
    """
    client.app.state.persistence.save_task(
        Task(
            id=task_id,
            title="t",
            user_input="把调研写成 report.md",
            status=TaskStatus.UNVERIFIED,
            verification={
                "passed": False,
                "failures": ["声明的产物缺失：report.md"],
                "attempts": 0,
                "reason": "retry_exhausted",
            },
        )
    )
    return task_id


def test_review_endpoint_accepts_unverified_task(client):
    tid = _seed_unverified(client)
    r = client.post(
        f"/api/tasks/{tid}/review",
        json={"reviewer": "opencode", "note": "人工看过 report.md，结论齐了"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "COMPLETED"
    assert data["review"]["overruled_reason"] == "retry_exhausted"

    task = client.get(f"/api/tasks/{tid}").json()["data"]
    assert task["status"] == "COMPLETED"
    # 机器判决原样在场：接受改的是终态，不是判定
    assert task["verification"]["passed"] is False
    assert task["review"]["note"] == "人工看过 report.md，结论齐了"


def test_review_endpoint_rejects_blank_reason(client):
    tid = _seed_unverified(client, "rev2")
    assert (
        client.post(f"/api/tasks/{tid}/review", json={"reviewer": "me", "note": "  "}).status_code
        == 400
    )
    assert client.get(f"/api/tasks/{tid}").json()["data"]["status"] == "UNVERIFIED"


def test_review_endpoint_requires_both_fields(client):
    tid = _seed_unverified(client, "rev3")
    # 缺字段 / 空字段 = 422（pydantic 的形状门），不是 400
    assert client.post(f"/api/tasks/{tid}/review", json={"note": "x"}).status_code == 422
    assert (
        client.post(f"/api/tasks/{tid}/review", json={"reviewer": "me", "note": ""}).status_code
        == 422
    )


def test_review_endpoint_conflict_when_not_unverified(client):
    tid = client.post("/api/tasks", json={"input": "write a file via api"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "COMPLETED"
    r = client.post(f"/api/tasks/{tid}/review", json={"reviewer": "me", "note": "想给它盖个章"})
    assert r.status_code == 409


def test_review_endpoint_second_round_keeps_first_record(client):
    tid = _seed_unverified(client, "rev5")
    assert (
        client.post(
            f"/api/tasks/{tid}/review", json={"reviewer": "a", "note": "第一条理由"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/tasks/{tid}/review", json={"reviewer": "b", "note": "换个理由再来"}
        ).status_code
        == 409
    )
    assert client.get(f"/api/tasks/{tid}").json()["data"]["review"]["reviewer"] == "a"


def test_review_endpoint_unknown_task_is_404(client):
    r = client.post("/api/tasks/nope/review", json={"reviewer": "me", "note": "x"})
    assert r.status_code == 404


def test_review_route_is_registered(client):
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/tasks/{task_id}/review" in paths
