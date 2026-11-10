#!/usr/bin/env python3
"""Render a fix-walkthrough demo bundle into self-contained HTML pages.

Inputs
------
``--before`` / ``--after``
    Output of ``collect.py`` for each side. Each file is a JSON object with a
    ``scenarios`` list; every scenario carries ``id`` and ``steps``, and every
    step carries ``id``, ``label``, ``state`` and optional ``metrics`` /
    ``checks`` / ``note``.
``--meta``
    The narrative: title, issue / PR links, refs, source fidelity, and per
    scenario ``code`` snippets. See ``references/data-contract.md``.

Outputs
-------
``before.html`` / ``after.html`` / ``compare.html`` plus ``demo.json``.
All three HTML files embed the same merged bundle and differ only in the
default view. They use inline CSS/JS and open from ``file://``.

The script is intentionally stdlib-only so it runs inside the repo's
``.venv311`` without installing anything.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return data


def _scenarios_by_id(payload: Dict[str, Any], label: str) -> Dict[str, Dict[str, Any]]:
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise SystemExit(f"{label}: `scenarios` must be a JSON array")
    out: Dict[str, Dict[str, Any]] = {}
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict) or not str(scenario.get("id") or "").strip():
            raise SystemExit(f"{label}: scenarios[{index}] needs a non-empty `id`")
        out[str(scenario["id"])] = scenario
    return out


def _steps_by_id(scenario: Dict[str, Any], label: str) -> Dict[str, Dict[str, Any]]:
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        raise SystemExit(f"{label}: scenario {scenario.get('id')!r} needs a `steps` array")
    out: Dict[str, Dict[str, Any]] = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not str(step.get("id") or "").strip():
            raise SystemExit(f"{label}: scenario {scenario.get('id')!r} steps[{index}] needs a non-empty `id`")
        out[str(step["id"])] = step
    return out


def _side(step: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if step is None:
        return None
    return {
        "state": step.get("state"),
        "metrics": step.get("metrics") or {},
        "checks": step.get("checks") or [],
        "note": step.get("note") or "",
    }


def _merged_steps(
    before: Dict[str, Any], after: Dict[str, Any], scenario_id: str
) -> List[Dict[str, Any]]:
    b_steps = _steps_by_id(before, "before")
    a_steps = _steps_by_id(after, "after")
    order: List[str] = list(b_steps.keys())
    for step_id in a_steps:
        if step_id not in order:
            order.append(step_id)
    if set(b_steps) != set(a_steps):
        only_before = sorted(set(b_steps) - set(a_steps))
        only_after = sorted(set(a_steps) - set(b_steps))
        print(
            f"warning: scenario {scenario_id!r} step ids differ "
            f"(only before: {only_before}; only after: {only_after})",
            file=sys.stderr,
        )
    merged: List[Dict[str, Any]] = []
    for step_id in order:
        b = b_steps.get(step_id)
        a = a_steps.get(step_id)
        merged.append(
            {
                "id": step_id,
                "label": (b or a or {}).get("label") or step_id,
                "action": (b or a or {}).get("action") or "",
                "before": _side(b),
                "after": _side(a),
            }
        )
    return merged


def _code_diff(entry: Dict[str, Any]) -> Dict[str, Any]:
    path = str(entry.get("path") or "")
    before_ref = str(entry.get("before_ref") or "")
    after_ref = str(entry.get("after_ref") or "")
    before = str(entry.get("before") or "").rstrip("\n")
    after = str(entry.get("after") or "").rstrip("\n")
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{path} ({before_ref})" if before_ref else path,
            tofile=f"{path} ({after_ref})" if after_ref else path,
            lineterm="",
        )
    )
    return {
        "path": path,
        "before_ref": before_ref,
        "after_ref": after_ref,
        "note": str(entry.get("note") or ""),
        "diff": "\n".join(diff) if diff else "(snippets are identical)",
    }


def _merge(
    before: Dict[str, Any],
    after: Dict[str, Any],
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    b_scenarios = _scenarios_by_id(before, "before")
    a_scenarios = _scenarios_by_id(after, "after")
    meta_scenarios = meta.get("scenarios") or []
    if not isinstance(meta_scenarios, list):
        raise SystemExit("meta: `scenarios` must be a JSON array")
    meta_by_id: Dict[str, Dict[str, Any]] = {}
    for index, scenario in enumerate(meta_scenarios):
        if not isinstance(scenario, dict) or not str(scenario.get("id") or "").strip():
            raise SystemExit(f"meta: scenarios[{index}] needs a non-empty `id`")
        meta_by_id[str(scenario["id"])] = scenario

    order: List[str] = list(meta_by_id.keys())
    for scenario_id in list(b_scenarios.keys()) + list(a_scenarios.keys()):
        if scenario_id not in order:
            order.append(scenario_id)
    if not order:
        raise SystemExit("no scenarios found in meta / before / after")

    merged_scenarios: List[Dict[str, Any]] = []
    for scenario_id in order:
        b = b_scenarios.get(scenario_id)
        a = a_scenarios.get(scenario_id)
        m = meta_by_id.get(scenario_id, {})
        if b is None or a is None:
            print(
                f"warning: scenario {scenario_id!r} missing on "
                f"{'before' if b is None else 'after'} side",
                file=sys.stderr,
            )
        if b is None or a is None:
            empty = {"scenarios": []}
            b = b or empty
            a = a or empty
        # A missing side still needs a `steps` array for the merge helper.
        if "steps" not in b:
            b = {**b, "steps": []}
        if "steps" not in a:
            a = {**a, "steps": []}
        title = str(m.get("title") or (b or a).get("title") or scenario_id)
        description = str(m.get("description") or (b or a).get("description") or "")
        code = [_code_diff(entry) for entry in (m.get("code") or []) if isinstance(entry, dict)]
        merged_scenarios.append(
            {
                "id": scenario_id,
                "title": title,
                "description": description,
                "why": str(m.get("why") or ""),
                "code": code,
                "steps": _merged_steps(b, a, scenario_id),
            }
        )

    before_info = dict(meta.get("before") or {})
    if before.get("ref") and not before_info.get("ref"):
        before_info["ref"] = before["ref"]
    after_info = dict(meta.get("after") or {})
    if after.get("ref") and not after_info.get("ref"):
        after_info["ref"] = after["ref"]

    bundle = {
        "meta": {
            "title": str(meta.get("title") or "行为对比"),
            "summary": str(meta.get("summary") or ""),
            "issue": meta.get("issue") or {},
            "pr": meta.get("pr") or {},
            "before": before_info,
            "after": after_info,
            "source": str(meta.get("source") or "real-function"),
            "generated_at": str(
                meta.get("generated_at")
                or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
            ),
            "command": str(meta.get("command") or " ".join(sys.argv)),
            "warnings": [],
        },
        "scenarios": merged_scenarios,
    }
    return bundle


def _embed_json(bundle: Dict[str, Any]) -> str:
    text = json.dumps(bundle, ensure_ascii=False)
    # Keep the JSON valid while making it inert inside a <script> data block.
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _render(
    template: str,
    bundle: Dict[str, Any],
    default_view: str,
) -> str:
    payload = _embed_json(bundle)
    html = template.replace("__DEFAULT_VIEW__", default_view)
    html = html.replace("__DEMO_JSON__", payload)
    return html


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render before/after behavior cards as self-contained HTML")
    parser.add_argument("--before", required=True, type=Path, help="collect.py output for the before ref")
    parser.add_argument("--after", required=True, type=Path, help="collect.py output for the after ref")
    parser.add_argument("--meta", required=True, type=Path, help="narrative JSON (title, refs, code, scenario copy)")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "template.html",
        help="HTML template path",
    )
    args = parser.parse_args(argv)

    before = _load(args.before)
    after = _load(args.after)
    meta = _load(args.meta)
    template = args.template.read_text(encoding="utf-8")
    bundle = _merge(before, after, meta)
    args.out.mkdir(parents=True, exist_ok=True)

    for name, view in (("before.html", "before"), ("after.html", "after"), ("compare.html", "compare")):
        (args.out / name).write_text(_render(template, bundle, view), encoding="utf-8")
    (args.out / "demo.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    scenario_count = len(bundle["scenarios"])
    step_count = sum(len(s["steps"]) for s in bundle["scenarios"])
    print(
        f"rendered {scenario_count} scenario(s), {step_count} step(s) -> "
        f"{args.out / 'before.html'}, {args.out / 'after.html'}, {args.out / 'compare.html'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
