"""Offline guard for the SDK's own LangSmith tracing switch (issue #84).

``backend/tests/conftest.py`` neutralises the provider keys this repo reads, but
langchain-core / langsmith read *their own* tracing switches straight from the
process environment, and this repo's ``trace_enabled`` setting gates only
``backend/services/trace.py`` — it does not reach the SDK. So a developer shell
that exports ``LANGSMITH_TRACING=true`` with a key makes the "offline" suite POST
real runs to LangSmith while every test still passes: the suite silently stops
being offline.

The seam is the test **process** and its environment, observed from outside:

* a slice of the suite launched with the issue's trigger, with the ingest
  endpoint pointed at a loopback sink, must open zero connections;
* a fresh interpreter that imports the test conftest under the same trigger must
  end up holding neither the switch nor the credential.

Both tests bring their own poisoned environment, so they behave identically on a
machine that exports the pair and on CI that exports nothing. The endpoint is
forced to 127.0.0.1, which means neither test can leak a real trace even while
the guard is missing.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The switches the SDK resolves for itself: langsmith.utils.tracing_is_enabled
# walks (LANGSMITH, LANGCHAIN) x (TRACING_V2, TRACING), and langchain-core
# additionally raises when the legacy v1 switch is set without its v2 twin.
TRACING_SWITCHES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_HANDLER",
)
CREDENTIALS = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")

# The trigger from the issue, verbatim: the switch, plus a key that is not real.
POISON = {
    "LANGSMITH_TRACING": "true",
    "LANGSMITH_API_KEY": "lsv2_pt_offlineguard_offlineguard_offlineguard",
}

# One full kernel run through TaskManager + MockLLM — the path where
# langchain-core builds its callback manager and would attach a LangChainTracer.
SUITE_SLICE = "backend/tests/test_graph.py::test_engineer_smoke_passes"

# A real dial-out is what is being measured, and a red run retries before giving
# up; the green path costs a few seconds.
SLICE_TIMEOUT_SEC = 600


class Sink:
    """Loopback TCP listener that counts connection attempts.

    Counting at ``accept()`` is the whole point: an upload cannot avoid opening
    the connection, and the count says nothing about HTTP status — so a missing
    guard shows up here instead of as a flaky assertion. The inner process is
    pointed at this address, which keeps a red run on the loopback interface.
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.url = f"http://127.0.0.1:{self._sock.getsockname()[1]}"
        self.hits = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:  # timed out waiting, or the socket just closed
                continue
            self.hits += 1
            with conn:
                try:
                    conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                except OSError:
                    pass
        self._sock.close()

    def __enter__(self) -> "Sink":
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def poisoned_env(endpoint: str | None = None) -> dict[str, str]:
    """The current environment with the issue's trigger applied on top.

    Any pre-existing tracing switch / credential is dropped first, so the
    poisoned shape is the same whether or not the machine already exports one.
    ``endpoint`` redirects the ingest URL to the loopback sink.
    """
    env = {k: v for k, v in os.environ.items() if k not in TRACING_SWITCHES + CREDENTIALS}
    env.update(POISON)
    if endpoint is not None:
        env["LANGSMITH_ENDPOINT"] = endpoint
        env["LANGCHAIN_ENDPOINT"] = endpoint
    return env


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=SLICE_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        pytest.fail(f"offline slice hung past {SLICE_TIMEOUT_SEC}s under a poisoned LangSmith env")


def test_offline_slice_with_langsmith_tracing_exported_opens_no_connections() -> None:
    """AC1: LANGSMITH_TRACING=true + a key must not make the suite dial out."""
    with Sink() as sink:
        proc = _run(
            [sys.executable, "-m", "pytest", SUITE_SLICE, "-q", "-p", "no:cacheprovider"],
            poisoned_env(sink.url),
        )
        hits = sink.hits

    # Anti-vacuity: zero connections only means something if the slice ran.
    assert proc.returncode == 0, f"inner slice failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    assert "1 passed" in proc.stdout, f"inner slice collected nothing:\n{proc.stdout[-2000:]}"
    assert hits == 0, (
        f"the offline suite opened {hits} connection(s) to the LangSmith ingest endpoint while only "
        f"LANGSMITH_TRACING + a key were inherited from the shell — the conftest guard is not holding "
        f"(issue #84).\ninner output:\n{proc.stdout[-2000:]}"
    )


# Imports the conftest the way pytest does, then reports what survived in the
# environment. Names come in as argv so the list stays single-sourced here.
_PROBE = (
    "import json, os, sys, backend.tests.conftest;"
    "print(json.dumps({name: os.environ.get(name) for name in sys.argv[1:]}))"
)


def test_importing_the_suite_conftest_leaves_no_langsmith_switch_or_key() -> None:
    """The guard is a conftest-import side effect, not a per-test accident."""
    names = list(TRACING_SWITCHES + CREDENTIALS)
    proc = _run([sys.executable, "-c", _PROBE, *names], poisoned_env())

    assert proc.returncode == 0, f"probe failed to import the conftest:\n{proc.stderr[-2000:]}"
    survived = {name: value for name, value in json.loads(proc.stdout).items() if value is not None}
    assert survived == {}, f"the conftest let these LangSmith variables through: {sorted(survived)}"

