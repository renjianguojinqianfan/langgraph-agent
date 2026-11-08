"""System prompts for the single `agent` node's two modes.

One loop, one node (ADR 0008 decision 2): the mode picks which of these two
prompts — and which tool face — the node calls with. There is no separate
planner node any more, and build mode never re-plans behind the user's back.

The plan itself is a Markdown file (`backend/services/plan_file.py`); it
enters the conversation as one message, not as a system-prompt block (C6).

D-56 (#67): a disabled capability package must not be mentioned in the text
that reaches the model either. `planner_system` swaps the delegation
paragraph for a package-neutral one when the `subagent` package is off; the
default `PLANNER_SYSTEM` text is byte-identical to pre-#67.
"""

from __future__ import annotations

from typing import FrozenSet, Optional

#: First line of `PLANNER_SYSTEM`. `MockLLMClient` keys the plan/build
#: phase on this constant instead of sniffing a copy of the text, so rewording
#: the prompt cannot silently flip every scripted offline test.
PLANNER_MARKER = "You are the planner for an autonomous task agent."

_DELEGATION_BLOCK = """
Delegation: if part of the task is a focused, self-contained job that will feed a
later step, plan it as one plain step such as "Research X in a sub-agent"; in
execution mode the agent may then call `spawn_subagent` for it."""

#: Package-neutral replacement used when the `subagent` capability package is off
#: (no unavailable tool name may be advertised in the context).
_GENERIC_DELEGATION_BLOCK = """
If part of the task is a focused, self-contained job that will feed a later
step, plan it as one plain step."""

PLANNER_SYSTEM = f"""{PLANNER_MARKER}


You are in PLAN mode: reading and inspecting is allowed, changing anything is not.
Use the read-only tools available to you when you need facts about the workspace,
then produce a concise, ordered plan as Markdown (a heading and a numbered list).
Wrap the final plan exactly in <qianfan-plan> and </qianfan-plan>; put no other
commentary around it. The system stores that Markdown as the task's plan file.
If you need a clarification, reply with your question as plain text and no markers.{_DELEGATION_BLOCK}"""

_PLANNER_SYSTEM_WITHOUT_SUBAGENT = PLANNER_SYSTEM.replace(
    _DELEGATION_BLOCK, _GENERIC_DELEGATION_BLOCK
)

EXECUTOR_SYSTEM = """You are the executor for an autonomous task agent.
Based on the conversation, the current plan (if any) and the available tools,
decide the next action:
- If a tool is needed, call exactly one tool with the correct arguments.
- If this turn's work is done, reply with plain text: either a summary of what
  you did, or the question you need the user to answer.
Think step by step but respond with either a tool call or a text reply."""


def planner_system(packages: Optional[FrozenSet[str]] = None) -> str:
    """Planner prompt for the enabled capability packages.

    `packages=None` keeps the pre-#67 behaviour (full text) for direct runtime
    tests; the manager always passes its resolved set. The sub-agent delegation
    paragraph is the only package-dependent part today.
    """
    if packages is None or "subagent" in packages:
        return PLANNER_SYSTEM
    return _PLANNER_SYSTEM_WITHOUT_SUBAGENT
