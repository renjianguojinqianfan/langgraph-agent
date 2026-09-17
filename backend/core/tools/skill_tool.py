"""Skills runtime: the ``load_skill`` tool (P1-A, spec ``docs/specs/p1-a-skills-runtime.md``).

The injection layer (P1-A′) puts a ``name`` + ``description`` catalogue of the
available skills into the system prompt; this tool is the other half of
progressive disclosure — the model fetches a skill's full ``SKILL.md`` text on
demand, as an ordinary tool result (never into the system prompt).

Resolution rules (spec §三):

* Names come from the frontmatter ``name`` — exactly what the catalogue shows.
  The requested string is compared for equality only and never takes part in
  path construction, so traversal-shaped inputs simply miss.
* When both layers carry the same name, both files are returned (home first,
  then the workspace root), each labelled with its ``layer`` — no override
  semantics, mirroring the catalogue's "keep duplicates" rule.
* Failures carry their diagnostics inside ``data``: ``tool_node`` serialises
  only ``ToolResult.data`` into the conversation, so an ``error`` field alone
  would stay invisible to the model (``backend/core/agent/nodes.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...config import Settings, get_settings
from ...utils.logging import get_logger
from ..agent.inject import SkillEntry, discover_skills
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.skill")

#: Cap on the "Available skills" list echoed back on a miss (spec §3.4).
_AVAILABLE_MAX = 20

_ARGS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Skill name exactly as listed in the Available Skills section.",
        },
    },
    "required": ["name"],
}


@register
class LoadSkillTool(BaseTool):
    name = "load_skill"
    description = (
        "Load the full text of a skill's SKILL.md by name. Use it when the task "
        "matches a skill listed in the Available Skills section, before following "
        "that skill's instructions."
    )
    args_schema = _ARGS_SCHEMA
    requires_confirm = False
    # Local FS read: deterministic, no retry / breaker (read/glob/grep policy).
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def __init__(self, settings: Settings | None = None, home_dir: Optional[Path] = None) -> None:
        super().__init__(settings)
        self._home_dir = home_dir  # parameterised for tests (same shape as inject)

    def _resolve_home(self) -> Path:
        return self._home_dir if self._home_dir is not None else Path.home()

    def _resolve_sandbox(self) -> Path:
        settings = self.settings or get_settings()
        return Path(settings.artifacts_path).resolve()

    def run(self, **kwargs: Any) -> ToolResult:
        name = str(kwargs.get("name") or "").strip()
        if not name:
            message = "`name` is required."
            return ToolResult(success=False, data={"error": message}, error=message)

        home = self._resolve_home()
        sandbox = self._resolve_sandbox()
        try:
            entries = discover_skills(home, sandbox)
            documents: List[Dict[str, str]] = []
            for entry in [e for e in entries if e.name == name]:
                content = _read_skill_text(entry.path)
                if content is not None:
                    documents.append({"layer": entry.layer, "content": content})
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("load_skill failed")
            return ToolResult(success=False, error=str(exc))

        if not documents:
            message = _miss_message(name, entries, home, sandbox)
            return ToolResult(success=False, data={"error": message}, error=message)

        # No top-level ``path`` key on purpose: tool_node would otherwise
        # register the skill file as a task artifact (nodes.py).
        return ToolResult(success=True, data={"name": name, "documents": documents})


def _read_skill_text(path: Path) -> Optional[str]:
    """Strict UTF-8 read; unreadable / undecodable counts as absent (spec §3.3)."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _miss_message(name: str, entries: List[SkillEntry], home: Path, sandbox: Path) -> str:
    """Miss diagnostics: list the loadable names (capped) or report an empty catalogue."""
    if not entries:
        return (
            f"Unknown skill: '{name}'. No skills found under "
            f"{home / '.agents' / 'skills'} or {sandbox / '.agents' / 'skills'}."
        )
    names = [entry.name for entry in entries]
    listed = ", ".join(names[:_AVAILABLE_MAX])
    if len(names) > _AVAILABLE_MAX:
        listed += f" (+{len(names) - _AVAILABLE_MAX} more)"
    return f"Unknown skill: '{name}'. Available skills: {listed}"