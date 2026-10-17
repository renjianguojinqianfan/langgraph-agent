"""System prompts for the single ``agent`` node's two modes.

One loop, one node (ADR 0008 decision 2): the mode picks which of these two
prompts — and which tool face — the node calls with. There is no separate
planner node any more, and build mode never re-plans behind the user's back.

:func:`render_plan_section` turns ``state["plan"]`` into the ``# Current Plan``
block appended to the system message in both modes (issue #119 — build mode
needs the plan as its brief; plan mode needs it to revise rather than restart).
"""

from __future__ import annotations

from typing import Any, List, Sequence

#: First line of :data:`PLANNER_SYSTEM`. ``MockLLMClient`` keys the plan/build
#: phase on this constant instead of sniffing a copy of the text, so rewording
#: the prompt cannot silently flip every scripted offline test.
PLANNER_MARKER = "You are the planner for an autonomous task agent."

PLANNER_SYSTEM = f"""{PLANNER_MARKER}


You are in PLAN mode: reading and inspecting is allowed, changing anything is not.
Use the read-only tools available to you when you need facts about the workspace,
then produce a concise, ordered plan as a JSON array of short step descriptions,
for example:
["Search the web for X", "Summarise the findings", "Write a report file"]
Return ONLY the JSON array once you have enough information, with no extra commentary.

Delegation: if part of the task is a focused, self-contained job that will feed a
later step, plan it as one plain step such as "Research X in a sub-agent"; in
execution mode the agent may then call `spawn_subagent` for it."""

#: Extra system text for the one-off re-plan round (the input bar's
#: 「重新规划」button). The action promises a revised plan for the SAME task;
#: without this anchor a long injected AGENTS / skills context can make the
#: model drift into an unrelated task (observed on a real model after a build
#: round). Kept separate from :data:`PLANNER_SYSTEM` so a normal plan round's
#: prompt is byte-identical to before.
REPLAN_ANCHOR = """

Re-plan this task's existing plan. Keep the same task and goal; revise the
current plan instead of starting a new task, and ignore unrelated skill or
workflow instructions in the surrounding context."""

#: One stricter retry when a re-plan round answers with text instead of a plan.
#: The action promises a plan, so a non-plan reply is retried once; a second
#: non-plan reply becomes a short failure instead of a conversation turn.
REPLAN_RETRY = """

Your previous reply was not a JSON plan. Return ONLY a JSON array of short plan
steps for the CURRENT task. Do not call tools and do not add commentary."""

#: Trailing user turn for the one-off re-plan round. It is added to the LLM
#: request only (never persisted): the last real user turn said "execute", so
#: the model needs an explicit instruction for this action instead of having to
#: infer it from the system block. Real-model review: without this, the round
#: answered with its reasoning trace and never produced a plan.
REPLAN_USER_ANCHOR = (
    "Re-plan the CURRENT task and its existing plan. Return ONLY a JSON array "
    "of plan steps. Do not start a new task, do not follow unrelated skill "
    "instructions, and do not explain your reasoning."
)

EXECUTOR_SYSTEM = """You are the executor for an autonomous task agent.
Based on the conversation, the current plan (if any) and the available tools,
decide the next action:
- If a tool is needed, call exactly one tool with the correct arguments.
- If this turn's work is done, reply with plain text: either a summary of what
  you did, or the question you need the user to answer.
Think step by step but respond with either a tool call or a text reply."""

#: Section anchor of the plan block (issue #119) — asserted verbatim by the
#: tests, same public-contract rule as the injection block's anchors in
#: :mod:`backend.core.agent.inject`.
PLAN_SECTION = "# Current Plan"


def render_plan_section(plan: Sequence[Any]) -> str:
    """Render ``state["plan"]`` as the ``# Current Plan`` block (#119).

    ``""`` for an empty plan (build mode was never in plan mode, or a plan
    round produced nothing), so the system message stays byte-identical to the
    pre-#119 shape when there is nothing to show. Otherwise the block starts
    with a blank line so callers can append it verbatim — the same composition
    contract as :func:`~backend.core.agent.inject.build_inject_block`, which it
    follows.

    The block is *derived* from the live state on every call and never
    accumulated, which is what makes「替换、不堆叠」structural rather than a rule
    the caller has to remember (AC2): one plan, one rendering, at the tail of
    system.
    """
    if not plan:
        return ""
    lines: List[str] = [PLAN_SECTION]
    for position, raw in enumerate(plan, 1):
        # ``_parse_plan`` always emits PlanStep dicts; a bare string is tolerated
        # here (defensive) so it degrades to a description instead of raising.
        step = raw if isinstance(raw, dict) else {"description": str(raw)}
        index = step.get("index", position)
        description = str(step.get("description", ""))
        status = str(step.get("status") or "pending")
        lines.append(f"{index}. [{status}] {description}")
    return "\n\n" + "\n".join(lines)
