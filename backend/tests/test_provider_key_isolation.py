"""Guard: a developer's exported provider keys must not reach the offline suite.

``ModelStore`` reads ``provider.api_key_env`` straight from ``os.environ`` (not
only through ``Settings``), so an exported ``LLM_API_KEY`` / ``DASHSCOPE_API_KEY``
makes "no key configured" assertions fail locally while CI (which exports
nothing) stays green. ``backend/tests/conftest.py`` neutralises the known names
at import; this test observes that contract from a fresh interpreter with its
own poisoned environment, so it fails on CI too if the guard is removed.

The key list is spelled out here instead of imported from the conftest guard on
purpose: the test states what the contract is, otherwise it would pass whatever
the guard happens to clear.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROVIDER_KEY_ENVS = (
    "LLM_API_KEY",
    "DASHSCOPE_API_KEY",
    "EXA_API_KEY",
    "SERPAPI_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)

SENTINEL = "sentinel-provider-key-do-not-use"

_PROBE = (
    "import json, os, sys;"
    "names = sys.argv[1:];"
    "before = {name: os.environ.get(name) for name in names};"
    "import backend.tests.conftest;"
    "after = {name: os.environ.get(name) for name in names};"
    "print(json.dumps({'before': before, 'after': after}))"
)


def test_conftest_neutralises_ambient_provider_keys() -> None:
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEY_ENVS}
    env.update({name: SENTINEL for name in PROVIDER_KEY_ENVS})
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if part
    )
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, *PROVIDER_KEY_ENVS],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"probe failed to import the conftest:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    # Anti-vacuity: the child really did receive the poisoned environment.
    assert data["before"] == {name: SENTINEL for name in PROVIDER_KEY_ENVS}
    survived = {name: value for name, value in data["after"].items() if value}
    assert survived == {}, (
        "the conftest let these provider keys reach the offline suite: "
        f"{sorted(survived)}"
    )
