"""P1-A skills-runtime tests: the ``load_skill`` tool (spec
``docs/specs/p1-a-skills-runtime.md`` §四).

Seams under test — both fixed by the spec, not renegotiated here:

* ``LoadSkillTool.run`` — the tool's public interface. Assertions read the
  returned :class:`ToolResult` (``success`` / ``data``), never internal state.
* ``inject.discover_skills`` — the two-layer discovery shared by the injection
  block and the tool (single source of truth; the P1-A′ suite guards that the
  block itself still renders byte-identically after the extraction).

Construction mirrors ``test_context_injection.py``: ``make_settings(tmp_path)``
roots the sandbox at ``tmp_path/artifacts``; ``home_dir`` is a throwaway
directory passed into the tool so the real ``~/.agents`` is never read.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.config import Settings
from backend.core.agent.inject import (
    ENV_SECTION,
    SKILLS_SECTION,
    build_inject_block,
    discover_skills,
)
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.core.tools.base import ToolResult
from backend.core.tools.registry import build_tools
from backend.core.tools.skill_tool import LoadSkillTool
from backend.tests.conftest import make_manager, make_settings


# ── helpers ──────────────────────────────────────────────────────────────────
def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return make_settings(tmp_path, **overrides)


def _home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def _sandbox(settings: Settings) -> Path:
    return Path(settings.artifacts_path).resolve()


def _skills_root(base: Path) -> Path:
    return base / ".agents" / "skills"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _skill(
    root: Path,
    dir_name: str,
    *,
    name: str,
    description: str | None = "desc",
    body: str = "Body.",
) -> Path:
    """Write ``{root}/{dir_name}/SKILL.md`` with a YAML frontmatter block.

    ``description=None`` omits the key entirely (the loadable-but-unlisted case).
    """
    lines = ["---", f"name: {name}"]
    if description is not None:
        lines.append(f"description: {description}")
    lines.append("---")
    return _write(root / dir_name / "SKILL.md", "\n".join(lines) + f"\n{body}\n")


def _load(settings: Settings, home: Path, name: Any) -> ToolResult:
    return LoadSkillTool(settings, home_dir=home).run(name=name)


# ── tracer bullet ────────────────────────────────────────────────────────────
def test_load_from_workspace_layer(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_sandbox(settings)), "alpha", name="alpha-skill", body="Hello alpha body.")

    res = _load(settings, _home(tmp_path), "alpha-skill")

    assert res.success is True
    assert res.data["name"] == "alpha-skill"
    assert [d["layer"] for d in res.data["documents"]] == ["workspace"]
    assert "Hello alpha body." in res.data["documents"][0]["content"]


# ── A 发现与命中 ─────────────────────────────────────────────────────────────
def test_load_from_home_layer(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_home(tmp_path)), "alpha", name="alpha-skill", body="Home body.")

    res = _load(settings, _home(tmp_path), "alpha-skill")

    assert res.success is True
    assert [d["layer"] for d in res.data["documents"]] == ["home"]
    assert "Home body." in res.data["documents"][0]["content"]


def test_load_duplicate_returns_both_home_first(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_home(tmp_path)), "dup", name="dup-skill", body="HOME COPY.")
    _skill(_skills_root(_sandbox(settings)), "dup", name="dup-skill", body="WORKSPACE COPY.")

    res = _load(settings, _home(tmp_path), "dup-skill")

    assert res.success is True
    docs = res.data["documents"]
    assert [d["layer"] for d in docs] == ["home", "workspace"]
    assert "HOME COPY." in docs[0]["content"]
    assert "WORKSPACE COPY." in docs[1]["content"]


def test_load_name_from_frontmatter_not_dirname(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_sandbox(settings)), "dir-alias", name="real-name")

    assert _load(settings, _home(tmp_path), "real-name").success is True
    miss = _load(settings, _home(tmp_path), "dir-alias")
    assert miss.success is False
    assert "real-name" in miss.data["error"]  # the catalogue shows the frontmatter name


def test_load_ignores_manifest_budget(tmp_path: Path) -> None:
    card = "- alpha: d1"
    settings = _settings(tmp_path, context_inject_enabled=True, context_inject_skills_budget=len(card))
    root = _skills_root(_sandbox(settings))
    _skill(root, "a", name="alpha", description="d1")
    _skill(root, "b", name="beta", description="d2")

    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert "- alpha: d1" in block
    assert "- beta: d2" not in block  # dropped from the catalogue by the budget
    assert _load(settings, _home(tmp_path), "beta").success is True  # still loadable


def test_load_without_description_loadable_not_listed(tmp_path: Path) -> None:
    settings = _settings(tmp_path, context_inject_enabled=True)
    _skill(_skills_root(_sandbox(settings)), "solo", name="solo-skill", description=None)

    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert "solo-skill" not in block
    assert _load(settings, _home(tmp_path), "solo-skill").success is True


def test_load_invalid_frontmatter_not_loadable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    root = _skills_root(_sandbox(settings))
    _write(root / "bad-yaml" / "SKILL.md", "---\nname: [unclosed\n---\nBody.\n")
    _write(root / "no-frontmatter" / "SKILL.md", "Just a body, no frontmatter.\n")
    _write(root / "empty-name" / "SKILL.md", '---\nname: ""\ndescription: d\n---\nBody.\n')

    assert discover_skills(_home(tmp_path), _sandbox(settings)) == []
    res = _load(settings, _home(tmp_path), "unclosed")
    assert res.success is False
    assert "No skills found" in res.data["error"]


def test_load_content_verbatim(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = _skill(_skills_root(_sandbox(settings)), "alpha", name="alpha-skill", body="Exact body line.")
    raw = path.read_text(encoding="utf-8")

    res = _load(settings, _home(tmp_path), "alpha-skill")

    assert res.success is True
    assert res.data["documents"][0]["content"] == raw
    assert raw.startswith("---\nname: alpha-skill\n")


# ── B 输入与安全 ─────────────────────────────────────────────────────────────
def test_load_empty_name_param_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for bad in ("", "   ", None):
        res = _load(settings, _home(tmp_path), bad)
        assert res.success is False
        assert "required" in res.data["error"]


def test_load_numeric_name_not_found(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_sandbox(settings)), "alpha", name="alpha-skill")

    res = _load(settings, _home(tmp_path), 3)

    assert res.success is False
    assert "alpha-skill" in res.data["error"]


def test_load_rejects_traversal(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_sandbox(settings)), "alpha", name="alpha-skill")
    _write(tmp_path / "secret.txt", "SECRET-CONTENT")

    for bad in ("../secret", "..\\secret", "../../../etc/passwd"):
        res = _load(settings, _home(tmp_path), bad)
        assert res.success is False
        assert "SECRET-CONTENT" not in str(res.data)


def test_load_rejects_absolute_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_sandbox(settings)), "alpha", name="alpha-skill")
    outside = _write(tmp_path / "outside" / "SKILL.md", "---\nname: outside\n---\nOUTSIDE-BODY\n")

    for bad in (str(outside), "C:\\Windows\\win.ini"):
        res = _load(settings, _home(tmp_path), bad)
        assert res.success is False
        assert "OUTSIDE-BODY" not in str(res.data)


def test_load_rejects_separator_name(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_sandbox(settings)), "alpha", name="beta")  # dir "alpha", skill "beta"

    for bad in ("alpha/beta", "alpha\\beta"):
        res = _load(settings, _home(tmp_path), bad)
        assert res.success is False
        assert "beta" in res.data["error"]  # the real name is listed as available


def test_load_never_scans_outside_roots(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write(_sandbox(settings) / "rogue" / "SKILL.md", "---\nname: rogue-skill\n---\nROGUE BODY\n")

    res = _load(settings, _home(tmp_path), "rogue-skill")

    assert res.success is False
    assert "No skills found" in res.data["error"]


# ── C 未命中形态 ─────────────────────────────────────────────────────────────
def test_missing_lists_available_names(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    root = _skills_root(_sandbox(settings))
    _skill(root, "a", name="alpha")
    _skill(root, "b", name="beta")

    res = _load(settings, _home(tmp_path), "gamma")

    assert res.success is False
    err = res.data["error"]
    assert "gamma" in err and "alpha" in err and "beta" in err


def test_available_capped_at_20(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    root = _skills_root(_sandbox(settings))
    for i in range(25):
        _skill(root, f"s{i:02d}", name=f"skill-{i:02d}")

    res = _load(settings, _home(tmp_path), "nope")

    assert res.success is False
    err = res.data["error"]
    assert "(+5 more)" in err
    assert err.count("skill-") == 20


def test_empty_catalogue_message(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    res = _load(settings, _home(tmp_path), "anything")

    assert res.success is False
    assert "No skills found" in res.data["error"]
    assert "anything" in res.data["error"]


def test_failure_contract_data_carries_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    res = _load(settings, _home(tmp_path), "nope")

    assert res.success is False
    assert isinstance(res.data, dict) and res.data.get("error")
    assert res.error == res.data["error"]


# ── D 契约与注册 ─────────────────────────────────────────────────────────────
def test_registered_in_build_tools(settings: Settings) -> None:
    names = {t.name for t in build_tools(settings)}
    assert "load_skill" in names


def test_policy_flags(settings: Settings) -> None:
    tool = LoadSkillTool(settings)
    assert tool.requires_confirm is False
    assert tool.retryable is False
    assert tool.circuit_breaker is False
    assert tool.max_retries == 0


def test_openai_schema_required_name(settings: Settings) -> None:
    fn = LoadSkillTool(settings).to_openai_schema()["function"]
    assert fn["name"] == "load_skill"
    assert fn["parameters"]["required"] == ["name"]


def test_result_has_no_top_level_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _skill(_skills_root(_sandbox(settings)), "alpha", name="alpha-skill")

    res = _load(settings, _home(tmp_path), "alpha-skill")

    assert res.success is True
    assert "path" not in res.data


# ── E runtime 集成 ───────────────────────────────────────────────────────────
def _run_until_done(tm: Any, task_id: str, timeout: float = 15.0) -> Any:
    """Poll a task to a terminal state (same shape as the sibling suites)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return task
        time.sleep(0.05)
    return tm.get_task(task_id)


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` onto ``tmp_path`` (never read the real ``~``)."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class _CapturingMock(MockLLMClient):
    """Mock that records the message list handed to the model each turn."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen_messages: List[Dict[str, Any]] = []

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.seen_messages = [dict(m) for m in messages]
        return super().complete(messages, tools=tools, **kwargs)


def test_tool_node_feeds_skill_content_to_conversation(
    tmp_path: Path, event_bus: Any, tmp_home: Path
) -> None:
    settings = _settings(tmp_path, context_inject_enabled=True)
    _skill(_skills_root(_sandbox(settings)), "alpha", name="alpha-skill", body="SKILL BODY TOKEN.")
    mock = _CapturingMock(
        plan=["Load the skill", "Answer"],
        tool_calls=[{"id": "c1", "name": "load_skill", "arguments": {"name": "alpha-skill"}}],
        final_answer="Loaded alpha-skill.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="skill", user_input="use the alpha skill")

    task = _run_until_done(tm, task_id)

    assert task is not None
    assert task.status.value == "COMPLETED"
    tool_messages = [m for m in mock.seen_messages if m.get("role") == "tool"]
    assert tool_messages, "the skill body must be fed back as a tool message"
    assert "SKILL BODY TOKEN." in str(tool_messages[-1]["content"])
    assert task.artifacts == []


def test_catalogue_cards_all_loadable(tmp_path: Path) -> None:
    settings = _settings(tmp_path, context_inject_enabled=True)
    home = _home(tmp_path)
    _skill(_skills_root(home), "hh", name="home-card", description="home desc")
    _skill(_skills_root(_sandbox(settings)), "ww", name="ws-card", description="ws desc")
    _skill(_skills_root(_sandbox(settings)), "solo", name="no-desc-card", description=None)

    block = build_inject_block(settings, home_dir=home)
    skills_section = block.split(SKILLS_SECTION, 1)[1].split(ENV_SECTION, 1)[0]
    names = [line[2:].split(":", 1)[0].strip() for line in skills_section.splitlines() if line.startswith("- ")]

    assert names == ["home-card", "ws-card"]  # home first; the description-less card is unlisted
    for name in names:
        assert _load(settings, home, name).success is True