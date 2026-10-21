"""C5: workspace file listing, preview and download (ADR 0010 decision 6)."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.core.llm.client import MockLLMClient
from backend.main import app
from backend.tests.conftest import make_manager, make_settings

PREVIEW_BYTES = 200_000


def _client(tmp_path, workspace):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task("files", "list files", workspace=str(workspace))
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    return client, task_id


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "a.txt").write_text("hello workspace", encoding="utf-8")
    (ws / "sub" / "b.txt").write_text("nested", encoding="utf-8")
    (ws / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    (ws / "big.txt").write_text("x" * (PREVIEW_BYTES + 50), encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "secret.txt").write_text("hidden", encoding="utf-8")
    return ws


def test_list_files_in_the_task_workspace(workspace, tmp_path):
    client, task_id = _client(tmp_path, workspace)
    try:
        r = client.get(f"/api/tasks/{task_id}/files")
        assert r.status_code == 200
        body = r.json()["data"]
        paths = {entry["path"] for entry in body["files"]}
        assert {"a.txt", "sub/b.txt", "bin.dat", "big.txt"} <= paths
        assert not any(path.startswith(".git/") for path in paths)
        assert body["truncated"] is False
        a = next(e for e in body["files"] if e["path"] == "a.txt")
        assert a["size"] == len("hello workspace")
    finally:
        client.__exit__(None, None, None)


def test_list_skips_gitignored_files(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required")
    ws = tmp_path / "gitws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True, capture_output=True)
    (ws / ".gitignore").write_text("ignored.txt\nnode_modules/\n", encoding="utf-8")
    (ws / "kept.txt").write_text("kept", encoding="utf-8")
    (ws / "ignored.txt").write_text("ignored", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    client, task_id = _client(tmp_path, ws)
    try:
        paths = {e["path"] for e in client.get(f"/api/tasks/{task_id}/files").json()["data"]["files"]}
        assert "kept.txt" in paths
        assert "ignored.txt" not in paths
        assert "node_modules/x.js" not in paths
    finally:
        client.__exit__(None, None, None)


def _dir_link(link, target) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        if result.returncode != 0:
            pytest.skip(
                "cannot create a junction: "
                + result.stderr.decode("mbcs", errors="replace").strip()
            )
    else:
        link.symlink_to(target, target_is_directory=True)


def test_list_does_not_escape_through_a_directory_link(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    _dir_link(workspace / "escape", outside)
    client, task_id = _client(tmp_path, workspace)
    try:
        paths = {
            entry["path"]
            for entry in client.get(f"/api/tasks/{task_id}/files").json()["data"]["files"]
        }
    finally:
        client.__exit__(None, None, None)

    assert not any(path.startswith("escape/") for path in paths)
    assert {"a.txt", "sub/b.txt"} <= paths


def test_list_does_not_loop_on_a_directory_link_cycle(workspace, tmp_path):
    _dir_link(workspace / "sub" / "loop", workspace)
    client, task_id = _client(tmp_path, workspace)
    try:
        paths = {
            entry["path"]
            for entry in client.get(f"/api/tasks/{task_id}/files").json()["data"]["files"]
        }
    finally:
        client.__exit__(None, None, None)

    assert "a.txt" in paths
    assert not any(path.startswith("sub/loop/") for path in paths)


def test_preview_text_and_truncation(workspace, tmp_path):
    client, task_id = _client(tmp_path, workspace)
    try:
        ok = client.get(f"/api/tasks/{task_id}/files/preview", params={"path": "a.txt"})
        assert ok.status_code == 200
        assert ok.json()["data"]["content"] == "hello workspace"
        assert ok.json()["data"]["truncated"] is False

        big = client.get(f"/api/tasks/{task_id}/files/preview", params={"path": "big.txt"})
        assert big.status_code == 200
        assert big.json()["data"]["truncated"] is True
        assert len(big.json()["data"]["content"]) == PREVIEW_BYTES
    finally:
        client.__exit__(None, None, None)


def test_preview_rejects_binary_and_escapes(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    client, task_id = _client(tmp_path, workspace)
    try:
        binary = client.get(f"/api/tasks/{task_id}/files/preview", params={"path": "bin.dat"})
        assert binary.status_code == 415

        escaped = client.get(
            f"/api/tasks/{task_id}/files/preview", params={"path": "../outside.txt"}
        )
        assert escaped.status_code == 400
        absolute = client.get(
            f"/api/tasks/{task_id}/files/preview", params={"path": str(outside)}
        )
        assert absolute.status_code in (400, 404)

        missing = client.get(
            f"/api/tasks/{task_id}/files/preview", params={"path": "nope.txt"}
        )
        assert missing.status_code == 404
        directory = client.get(
            f"/api/tasks/{task_id}/files/preview", params={"path": "sub"}
        )
        assert directory.status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_download_streams_the_file(workspace, tmp_path):
    client, task_id = _client(tmp_path, workspace)
    try:
        r = client.get(f"/api/tasks/{task_id}/files/download", params={"path": "a.txt"})
        assert r.status_code == 200
        assert r.content == b"hello workspace"
        assert "attachment" in r.headers["content-disposition"]
        assert "a.txt" in r.headers["content-disposition"]

        escaped = client.get(
            f"/api/tasks/{task_id}/files/download", params={"path": "../outside.txt"}
        )
        assert escaped.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_missing_task_and_workspace(workspace, tmp_path):
    client, task_id = _client(tmp_path, workspace)
    try:
        assert client.get("/api/tasks/nope/files").status_code == 404
        shutil.rmtree(workspace)
        assert client.get(f"/api/tasks/{task_id}/files").status_code == 409
        assert (
            client.get(f"/api/tasks/{task_id}/files/preview", params={"path": "a.txt"}).status_code
            == 409
        )
    finally:
        client.__exit__(None, None, None)
