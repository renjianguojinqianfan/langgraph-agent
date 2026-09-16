"""P1-A′ context injection layer (spec ``docs/specs/p1-a-prime-context-injection.md`` §三).

The agent cold-starts with amnesia: project conventions (AGENTS.md), the
available-skills catalogue and the environment facts only exist on disk — not
injecting them means the model never sees them. This module builds that block
with pure, deterministic functions; :class:`~backend.core.agent.nodes.AgentRuntime`
calls :func:`build_inject_block` once per task and reuses the result for every
LLM call (task-level cache).

Discovery chain — exactly two layers: ``{home}/.agents/AGENTS.md`` (home layer,
first) then ``{sandbox_root}/AGENTS.md`` (workspace root == settings
``artifacts_path``, last). Drive roots and intermediate directories are never
read, and only the literal ``AGENTS.md`` filename is recognised. Skills share
the same two-layer shape: ``{layer}/.agents/skills/*/SKILL.md``.
"""

from __future__ import annotations

import platform
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ...config import Settings

#: Section anchors — asserted verbatim by the tests (public contract of the block).
AGENTS_SECTION = "# Project Instructions (AGENTS.md)"
SKILLS_SECTION = "# Available Skills"
ENV_SECTION = "# Environment"

#: Skills-budget fallback for settings fakes that predate the field.
_DEFAULT_SKILLS_BUDGET = 4000


def build_inject_block(settings: Settings, *, home_dir: Optional[Path] = None) -> str:
    """Assemble the task-level injection block.

    ``home_dir`` defaults to ``Path.home()`` and is parameterised so tests can
    point it at a throwaway directory. Returns ``""`` when every section is
    empty; otherwise the block starts with a blank line (``"\\n\\n"``) so
    callers can append it to the system prompt verbatim.
    """
    sandbox_root = Path(settings.artifacts_path).resolve()
    home = Path(home_dir) if home_dir is not None else Path.home()
    budget = int(getattr(settings, "context_inject_skills_budget", _DEFAULT_SKILLS_BUDGET))
    sections = [
        _render_agents_md(home, sandbox_root),
        _render_skills(home, sandbox_root, budget),
        _render_env_facts(sandbox_root),
    ]
    parts = [s for s in sections if s]
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


# ── internals ────────────────────────────────────────────────────────────────
def _read_text(path: Path) -> Optional[str]:
    """Read a UTF-8 text file; missing/undecodable counts as an absent layer."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _render_agents_md(home_dir: Path, sandbox_root: Path) -> str:
    """Two-layer AGENTS.md merge (home first, workspace root last; no dedupe)."""
    entries: List[str] = []
    for path in (home_dir / ".agents" / "AGENTS.md", sandbox_root / "AGENTS.md"):
        text = _read_text(path)
        if text is None:
            continue
        entries.append(f"## {path}\n{text}")
    if not entries:
        return ""
    return f"{AGENTS_SECTION}\n" + "\n".join(entries)


def _render_skills(home_dir: Path, sandbox_root: Path, budget: int) -> str:
    """Two-layer skills catalogue rendered as ``- {name}: {description}`` cards."""
    cards: List[str] = []
    for layer in (home_dir / ".agents" / "skills", sandbox_root / ".agents" / "skills"):
        for skill_dir in _sorted_subdirs(layer):
            card = _skill_card(skill_dir / "SKILL.md")
            if card is not None:
                cards.append(card)
    kept = _fit_budget(cards, budget)
    if not kept:
        return ""
    return f"{SKILLS_SECTION}\n" + "\n".join(kept)


def _sorted_subdirs(root: Path) -> List[Path]:
    """Sub-directories of ``root`` by name (missing directory -> empty list)."""
    try:
        return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return []


def _skill_card(path: Path) -> Optional[str]:
    """Render one ``SKILL.md`` card, or ``None`` when it must be dropped.

    Only the frontmatter whitelist (``name`` / ``description``) is used; a
    missing key — or missing/invalid frontmatter — drops the whole card
    (deterministic tolerance, no guessed defaults).
    """
    text = _read_text(path)
    if text is None:
        return None
    meta = _frontmatter(text)
    if meta is None:
        return None
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    if not name.strip() or not description.strip():
        return None
    return f"- {name.strip()}: {description.strip()}"


def _frontmatter(text: str) -> Optional[Dict[str, Any]]:
    """The leading ``---`` YAML block as a mapping (missing/invalid -> ``None``)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:i]))
            except Exception:
                return None
            return data if isinstance(data, dict) else None
    return None


def _fit_budget(cards: List[str], budget: int) -> List[str]:
    """Longest prefix whose rendered-card length sum fits ``budget``.

    Cards are counted by their own text length; over-budget drops whole cards
    from the tail until the sum fits, so a single over-budget card is dropped
    too and the catalogue may end up empty.
    """
    kept: List[str] = []
    total = 0
    for card in cards:
        if total + len(card) > budget:
            break
        kept.append(card)
        total += len(card)
    return kept


def _render_env_facts(sandbox_root: Path) -> str:
    """The three environment facts (sandbox root / OS / date); never trimmed."""
    return (
        f"{ENV_SECTION}\n"
        f"- Sandbox root: {sandbox_root}\n"
        f"- OS: {platform.system()}\n"
        f"- Date: {date.today().isoformat()}"
    )