#!/usr/bin/env python3
"""Run a scenario module against one git worktree and write a JSON snapshot.

Usage
-----
    python collect.py \
      --worktree .qoder/tmp/fix-walkthrough/255/before \
      --scenario .qoder/tmp/fix-walkthrough/255/scenario_qianfan.py \
      --out      .qoder/tmp/fix-walkthrough/255/before.json \
      --ref      before

The scenario module must expose ``build()`` and return::

    {
      "scenarios": [
        {
          "id": "compress-pair",
          "title": "...",                 # optional, meta can override
          "steps": [
            {
              "id": "compress",
              "label": "压缩切点",
              "action": "compress_messages(..., keep_recent=3)",
              "state": {"kind": "messages", "messages": [...]},
              "metrics": {"dropped": 2},
              "checks": [{"label": "...", "ok": false, "detail": "..."}],
              "note": "..."
            }
          ]
        }
      ]
    }

The worktree is put first on ``sys.path`` before the module is loaded, so
``import backend...`` resolves to that ref, not the main checkout. The script
sets temp data dirs and offline flags so a scenario can build a ``Settings`` or
a FastAPI ``TestClient`` without touching the repo's ``data/``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_module(path: Path) -> Any:
    if not path.is_file():
        raise SystemExit(f"scenario module not found: {path}")
    spec = importlib.util.spec_from_file_location("fix_walkthrough_scenario", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load scenario module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _prepare_env(worktree: Path, runtime_data: Path) -> None:
    # Temp persistence for scenarios that build Settings / TestClient.
    runtime_data.mkdir(parents=True, exist_ok=True)
    for name, sub in (
        ("DATA_DIR", ""),
        ("ARTIFACTS_DIR", "artifacts"),
        ("TRACE_DIR", "traces"),
        ("CHECKPOINT_DIR", "checkpoints"),
    ):
        target = runtime_data / sub if sub else runtime_data
        target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target)
    os.environ["USE_MOCK_LLM"] = "true"
    os.environ["AUTH_ENABLED"] = "false"
    # Avoid inheriting a developer's live MCP / proxy setup.
    os.environ.setdefault("MCP_ENABLED", "false")
    # The scenario module may import backend.config; make sure the worktree is
    # the first place Python looks for `backend`.
    worktree_text = str(worktree)
    if worktree_text not in sys.path:
        sys.path.insert(0, worktree_text)
    else:
        sys.path.remove(worktree_text)
        sys.path.insert(0, worktree_text)
    old_pythonpath = os.environ.get("PYTHONPATH", "")
    parts = [worktree_text] + [p for p in old_pythonpath.split(os.pathsep) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join(parts)


def _drop_preloaded_backend() -> None:
    # A clean import is required: importing the main checkout's `backend`
    # before the worktree is on sys.path would silently run the wrong ref.
    for name in list(sys.modules):
        if name == "backend" or name.startswith("backend."):
            del sys.modules[name]


def _validate(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise SystemExit("scenario build() must return a JSON object")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise SystemExit("scenario build() must return `scenarios` as a non-empty array")
    for si, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict) or not str(scenario.get("id") or "").strip():
            raise SystemExit(f"scenarios[{si}] needs a non-empty `id`")
        steps = scenario.get("steps")
        if not isinstance(steps, list) or not steps:
            raise SystemExit(f"scenario {scenario.get('id')!r} needs a non-empty `steps` array")
        for ti, step in enumerate(steps):
            if not isinstance(step, dict) or not str(step.get("id") or "").strip():
                raise SystemExit(
                    f"scenario {scenario.get('id')!r} steps[{ti}] needs a non-empty `id`"
                )
            if "state" not in step:
                raise SystemExit(
                    f"scenario {scenario.get('id')!r} steps[{ti}] needs a `state`"
                )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect real before/after behavior into JSON")
    parser.add_argument("--worktree", required=True, type=Path, help="checkout to import backend from")
    parser.add_argument("--scenario", required=True, type=Path, help="scenario module exposing build()")
    parser.add_argument("--out", required=True, type=Path, help="output JSON path")
    parser.add_argument("--ref", default="", help="label for the snapshot (e.g. before / after)")
    parser.add_argument(
        "--runtime-data",
        type=Path,
        default=None,
        help="temp persistence root (default: <out parent>/_runtime_data)",
    )
    parser.add_argument("--verbose", action="store_true", help="print the full traceback on failure")
    args = parser.parse_args(argv)

    worktree = args.worktree.resolve()
    if not (worktree / "backend").is_dir():
        raise SystemExit(f"not a repo worktree (backend/ missing): {worktree}")
    scenario_path = args.scenario.resolve()
    out_path = args.out.resolve()
    runtime_data = (args.runtime_data or (out_path.parent / "_runtime_data")).resolve()
    _prepare_env(worktree, runtime_data)
    _drop_preloaded_backend()

    try:
        module = _load_module(scenario_path)
        build = getattr(module, "build", None)
        if not callable(build):
            raise SystemExit(f"{scenario_path} must define build()")
        payload = build()
        _validate(payload)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a CLI boundary should print a reason, not a traceback
        print(f"scenario failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1

    result: Dict[str, Any] = {
        "ref": args.ref,
        "worktree": str(worktree),
        "python": sys.version.split()[0],
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "scenarios": payload["scenarios"],
    }
    if isinstance(payload.get("meta"), dict):
        result["meta"] = payload["meta"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    step_count = sum(len(s["steps"]) for s in result["scenarios"])
    print(
        f"collected {len(result['scenarios'])} scenario(s), {step_count} step(s) "
        f"from {worktree.name} -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
