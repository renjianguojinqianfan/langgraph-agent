"""System prompts for the planner / executor nodes.

These are intentionally provider-agnostic natural-language instructions. The
planner is asked to return a JSON array of step descriptions; the executor is
asked to either call one of the supplied tools or return a final answer.

:func:`render_plan_section` belongs here too: it turns ``state["plan"]`` into
the block appended to the executor's system message (issue #119 — the plan had
no consumer on the execution face at all).
"""

from __future__ import annotations

from typing import Any, List, Sequence

PLANNER_SYSTEM = """You are the planner for an autonomous task agent.
Given the user's request, produce a concise, ordered plan as a JSON array of
short step descriptions, for example:
["Search the web for X", "Summarise the findings", "Write a report file"]
Return ONLY the JSON array, no extra commentary.

Delegation: if part of the task is a focused, self-contained job that will
feed a later step, plan it as one plain step such as "Research X in a
sub-agent"; the executor may then call `spawn_subagent` for it. Never assume
sub-agents get dispatched on their own -- the only way one runs is the
executor calling that tool (and a human approving it)."""

EXECUTOR_SYSTEM = """You are the executor for an autonomous task agent.
Based on the conversation and the available tools, decide the next action:
- If a tool is needed, call exactly one tool with the correct arguments.
- If the task is fully complete, return a final answer as plain text that
  summarises what was accomplished (do NOT call a tool).
Think step by step but respond with either a tool call or a final answer."""

#: Section anchor of the plan block (issue #119) — asserted verbatim by the
#: tests, same public-contract rule as the injection block's anchors in
#: :mod:`backend.core.agent.inject`.
PLAN_SECTION = "# Current Plan"


def render_plan_section(plan: Sequence[Any]) -> str:
    """Render ``state["plan"]`` as the executor's ``# Current Plan`` block (#119).

    ``""`` for an empty plan (before the first planner round, or after a planner
    failure left it empty), so the system message stays byte-identical to the
    pre-#119 shape. Otherwise the block starts with a blank line so callers can
    append it verbatim — the same composition contract as
    :func:`~backend.core.agent.inject.build_inject_block`, which it follows.

    The block is *derived* from the live state on every call and never
    accumulated, which is what makes「替换、不堆叠」structural rather than a rule
    the caller has to remember (AC2): one plan, one rendering, at the tail of
    system.
    """
    if not plan:
        return ""
    lines: List[str] = [PLAN_SECTION]
    for position, step in enumerate(plan, 1):
        # ``_parse_plan`` always emits PlanStep dicts; the non-dict branch is the
        # same legacy shape ``_mark_high_steps_skipped`` already tolerates.
        if isinstance(step, dict):
            index = step.get("index", position)
            description = str(step.get("description", ""))
            status = str(step.get("status") or "pending")
        else:
            index, description, status = position, str(step), "pending"
        lines.append(f"{index}. [{status}] {description}")
    return "\n\n" + "\n".join(lines)
