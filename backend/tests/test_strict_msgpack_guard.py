"""Offline guard for langgraph's own ``LANGGRAPH_STRICT_MSGPACK`` switch (#212).

The checkpoint sentinel only exercises the #9251 reconstruction fallback when
the serde is permissive. The switch is read once at import time in
``langgraph.checkpoint.serde._msgpack``, so a per-test ``monkeypatch`` cannot
undo it — the suite conftest has to drop it before the first import. This test
launches a fresh interpreter with the switch inherited as ``true`` and reports
what the suite would actually see.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = (
    "import json, os, backend.tests.conftest;"
    "import langgraph.checkpoint.serde._msgpack as m;"
    "print(json.dumps({'env': os.environ.get('LANGGRAPH_STRICT_MSGPACK'),"
    "'strict': m.STRICT_MSGPACK_ENABLED}))"
)


def test_inherited_strict_msgpack_switch_is_neutralised() -> None:
    env = dict(os.environ)
    env["LANGGRAPH_STRICT_MSGPACK"] = "true"
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if p)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"probe failed to import the suite:\n{proc.stderr[-2000:]}"
    observed = json.loads(proc.stdout)
    assert observed["env"] is None, "the suite conftest let LANGGRAPH_STRICT_MSGPACK through"
    assert observed["strict"] is False, (
        "langgraph's import-time STRICT_MSGPACK flag is on: the serde sentinel "
        "would pass via the allowlist path without exercising #9251"
    )
