"""P1-A′ context-injection tests (spec ``docs/specs/p1-a-prime-context-injection.md`` §四).

The injection semantics are carried by these deterministic tests (TDD is the
soul here, #29 拍板 3). Two construction rules from the spec:

* the discovery functions are pure and parameterised — ``tmp_path`` builds the
  two layers, and a ``tmp_home`` fixture redirects ``Path.home()`` so the real
  ``~/.agents`` is never read;
* runtime-side cases reuse conftest's ``make_settings`` / fake ``tm`` shape.

Section anchors (``# Project Instructions (AGENTS.md)`` / ``# Available Skills``
/ ``# Environment``) are asserted as literal strings — they are the block's
public contract.
"""

from __future__ import annotations

import platform
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, cast

import pytest

from backend.config import Settings
from backend.core.agent.inject import (
    AGENTS_SECTION,
    ENV_SECTION,
    SKILLS_SECTION,
    build_inject_block,
)
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.prompts import EXECUTOR_SYSTEM, PLANNER_SYSTEM
from backend.core.agent.state import AgentState
from backend.core.llm.client import LLMResponse
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings


# ── helpers ──────────────────────────────────────────────────────────────────
def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Injection-ON settings rooted at ``tmp_path`` (sandbox = ``artifacts_dir``)."""
    base: Dict[str, Any] = dict(context_inject_enabled=True)
    base.update(overrides)
    return make_settings(tmp_path, **base)


def _sandbox(settings: Settings) -> Path:
    """The resolved sandbox root == workspace root for the discovery chain."""
    return Path(settings.artifacts_path).resolve()


def _home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _skill(root: Path, dir_name: str, frontmatter: str, body: str = "Body.") -> Path:
    """Write ``{root}/{dir_name}/SKILL.md`` with a YAML frontmatter block."""
    return _write(root / dir_name / "SKILL.md", f"---\n{frontmatter}\n---\n{body}\n")


def _card(name: str, description: str) -> str:
    return f"- {name}: {description}"


def _desc_of_len(name: str, total: int) -> str:
    """A description padding the rendered card to exactly ``total`` characters."""
    return "x" * (total - len(f"- {name}: "))


def _fake_tm(settings: Settings) -> SimpleNamespace:
    return SimpleNamespace(
        settings=settings, event_bus=EventBus(), add_artifact=lambda *a, **k: None
    )


def _runtime(
    settings: Settings,
    *,
    task_id: str = "t1",
    aux: Any = None,
    confirm_enabled: bool = True,
) -> AgentRuntime:
    return AgentRuntime(
        task_id=task_id,
        task_manager=_fake_tm(settings),
        llm=SimpleNamespace(),
        tools=[],
        tool_schemas=[],
        aux_llm=aux,
        confirm_enabled=confirm_enabled,
    )


def _state(messages: Optional[List[Dict[str, Any]]] = None, **kw: Any) -> AgentState:
    base: Dict[str, Any] = {
        "messages": messages if messages is not None else [{"role": "user", "content": "hi"}],
        "step_index": 0,
    }
    base.update(kw)
    # Partial/extra keys by design: these tests drive one node, not a whole run.
    return cast(AgentState, base)


def _system_of(rt: AgentRuntime, state: AgentState, system: str) -> str:
    """The system message ``_build_messages`` actually sends to the LLM."""
    return str(rt._build_messages(state, system)[0]["content"])


def _big_messages() -> List[Dict[str, Any]]:
    """A > 8K-token history (default budget) with room to drop."""
    return [
        {"role": "user", "content": "x" * 40000},
        {"role": "assistant", "content": "y" * 1000},
        {"role": "user", "content": "z" * 1000},
        {"role": "assistant", "content": "w" * 1000},
    ]


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` onto ``tmp_path`` (never read the real ``~``)."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class _RecordingLLM:
    """Aux stand-in: records every payload it is asked to complete."""

    def __init__(self) -> None:
        self.payloads: List[List[Dict[str, Any]]] = []

    def complete(
        self, messages: List[Dict[str, Any]], tools: Any = None, **kwargs: Any
    ) -> LLMResponse:
        self.payloads.append(messages)
        return LLMResponse(content="（摘要）")


# ─ 4.1 discovery chain (two layers + reverse assertions) ────────────────────
def test_discover_both_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert AGENTS_SECTION not in block  # 缺失层静默跳过：连空标题都不出现
    assert ENV_SECTION in block  # 环境事实永不缺席


def test_discover_home_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    agents = _write(home / ".agents" / "AGENTS.md", "HOME-RULES")
    block = build_inject_block(settings, home_dir=home)
    assert "HOME-RULES" in block
    assert str(agents) in block  # 该层路径小标题
    assert str(_sandbox(settings) / "AGENTS.md") not in block  # 无工作区根小标题


def test_discover_sandbox_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    agents = _write(_sandbox(settings) / "AGENTS.md", "SANDBOX-RULES")
    block = build_inject_block(settings, home_dir=home)
    assert "SANDBOX-RULES" in block
    assert str(agents) in block
    assert str(home / ".agents" / "AGENTS.md") not in block


def test_discover_both_order(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _write(home / ".agents" / "AGENTS.md", "HOME-RULES")
    _write(_sandbox(settings) / "AGENTS.md", "SANDBOX-RULES")
    block = build_inject_block(settings, home_dir=home)
    assert block.index("HOME-RULES") < block.index("SANDBOX-RULES")  # 根→子，近者靠后


def test_discover_ignores_alias(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _write(home / ".agents" / "CLAUDE.md", "HOME-CLAUDE")
    _write(_sandbox(settings) / "CLAUDE.md", "SANDBOX-CLAUDE")
    block = build_inject_block(settings, home_dir=home)
    assert "HOME-CLAUDE" not in block
    assert "SANDBOX-CLAUDE" not in block
    assert AGENTS_SECTION not in block  # 只认 AGENTS.md 文件名


def test_discover_ignores_drive_root(tmp_path: Path) -> None:
    """#13: the drive root's AGENTS.md must never be read (two layers only).

    The drive root is normally not writable (needs admin on Windows; ``/`` on
    POSIX), so the bait degrades to ``tmp_path`` — the highest writable
    ancestor. Whichever level carries the bait, the locked property is the
    same: only the home layer and the sandbox root are read, never an ancestor.
    """
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    baits = {
        "DRIVE-ROOT-BAIT": Path(tmp_path.anchor) / "AGENTS.md",
        "TOP-ANCESTOR-BAIT": tmp_path / "AGENTS.md",
    }
    planted: List[tuple] = []
    for marker, path in baits.items():
        if path.exists():
            continue  # 别人的文件：不碰
        try:
            _write(path, marker)
        except OSError:
            continue  # 不可写层（盘根的常态）跳过
        planted.append((path, marker))
    try:
        block = build_inject_block(settings, home_dir=home)
        for _, marker in planted:
            assert marker not in block
    finally:
        for path, _ in planted:
            path.unlink(missing_ok=True)


def test_discover_ignores_parent_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _write(tmp_path / "AGENTS.md", "PARENT-BAIT")  # 沙箱根的父目录
    block = build_inject_block(settings, home_dir=home)
    assert "PARENT-BAIT" not in block
    assert AGENTS_SECTION not in block


def test_discover_ignores_child_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _write(_sandbox(settings) / "sub" / "AGENTS.md", "CHILD-BAIT")  # #14 懒加载暂缓
    block = build_inject_block(settings, home_dir=home)
    assert "CHILD-BAIT" not in block
    assert AGENTS_SECTION not in block


# ─ 4.2 merge semantics ──────────────────────────────────────────────────────
def test_merge_duplicates_kept(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _write(home / ".agents" / "AGENTS.md", "SHARED-RULES")
    _write(_sandbox(settings) / "AGENTS.md", "SHARED-RULES")
    block = build_inject_block(settings, home_dir=home)
    assert block.count("SHARED-RULES") == 2  # 全部追加，无去重


def test_merge_never_truncates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    content = "BEGIN-MARKER\n" + "x" * 102400 + "\nEND-MARKER"  # 100KB 病态大手册
    _write(_sandbox(settings) / "AGENTS.md", content)
    block = build_inject_block(settings, home_dir=home)
    assert content in block  # 逐字包含全文（永不裁）


# ─ 4.3 environment facts ────────────────────────────────────────────────────
def test_env_facts_sandbox_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert f"- Sandbox root: {_sandbox(settings)}" in block


def test_env_facts_os(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert f"- OS: {platform.system()}" in block


def test_env_facts_date(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert f"- Date: {date.today().isoformat()}" in block


def test_env_facts_immune_to_budget(tmp_path: Path) -> None:
    settings = _settings(tmp_path, context_inject_skills_budget=1)
    home = _home(tmp_path)
    _skill(home / ".agents" / "skills", "alpha", "name: alpha\ndescription: does A")
    block = build_inject_block(settings, home_dir=home)
    assert SKILLS_SECTION not in block  # 预算 1 字符：整卡被丢
    assert f"- Sandbox root: {_sandbox(settings)}" in block  # 环境事实不受影响
    assert f"- OS: {platform.system()}" in block
    assert f"- Date: {date.today().isoformat()}" in block


# ── 4.4 skills catalogue & budget boundaries ─────────────────────────────────
def test_skills_both_layers_collected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _skill(home / ".agents" / "skills", "alpha", "name: alpha\ndescription: does A")
    _skill(
        _sandbox(settings) / ".agents" / "skills",
        "beta",
        "name: beta\ndescription: does B",
    )
    block = build_inject_block(settings, home_dir=home)
    assert SKILLS_SECTION in block
    assert _card("alpha", "does A") in block
    assert _card("beta", "does B") in block
    assert block.index(_card("alpha", "does A")) < block.index(_card("beta", "does B"))


def test_skills_card_whitelist(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _skill(
        home / ".agents" / "skills",
        "alpha",
        "name: alpha\ndescription: does A\ncompatibility: needs-curl\n"
        "license: MIT\nallowed-tools: Bash",
    )
    block = build_inject_block(settings, home_dir=home)
    assert _card("alpha", "does A") in block
    assert "compatibility" not in block
    assert "needs-curl" not in block
    assert "MIT" not in block
    assert "allowed-tools" not in block


def test_skills_missing_name_dropped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    root = home / ".agents" / "skills"
    _skill(root, "keep", "name: keepme\ndescription: kept card")
    _skill(root, "drop", "description: dropped card")
    block = build_inject_block(settings, home_dir=home)
    assert _card("keepme", "kept card") in block
    assert "dropped card" not in block


def test_skills_missing_desc_dropped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    root = home / ".agents" / "skills"
    _skill(root, "keep", "name: keepme\ndescription: kept card")
    _skill(root, "drop", "name: droppedname")
    block = build_inject_block(settings, home_dir=home)
    assert _card("keepme", "kept card") in block
    assert "droppedname" not in block


def test_skills_no_frontmatter_dropped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _write(
        home / ".agents" / "skills" / "plain" / "SKILL.md",
        "# Plain skill\n\nNO-FRONTMATTER-CARD\n",
    )
    block = build_inject_block(settings, home_dir=home)
    assert "NO-FRONTMATTER-CARD" not in block
    assert SKILLS_SECTION not in block


def test_skills_budget_exact_fit(tmp_path: Path) -> None:
    settings = _settings(tmp_path, context_inject_skills_budget=4000)
    root = _home(tmp_path) / ".agents" / "skills"
    for dir_name, name in (("a-first", "card-one"), ("b-second", "card-two")):
        _skill(
            root,
            dir_name,
            f"name: {name}\ndescription: {_desc_of_len(name, 2000)}",
        )
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert _card("card-one", _desc_of_len("card-one", 2000)) in block
    assert _card("card-two", _desc_of_len("card-two", 2000)) in block  # 恰好 4000，全保留


def test_skills_budget_overflow_drops_tail(tmp_path: Path) -> None:
    settings = _settings(tmp_path, context_inject_skills_budget=4000)
    root = _home(tmp_path) / ".agents" / "skills"
    _skill(root, "a-first", f"name: card-one\ndescription: {_desc_of_len('card-one', 2000)}")
    _skill(root, "b-second", f"name: card-two\ndescription: {_desc_of_len('card-two', 2001)}")
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert _card("card-one", _desc_of_len("card-one", 2000)) in block
    assert _card("card-two", _desc_of_len("card-two", 2001)) not in block  # 4001 超 1 字符


def test_skills_budget_multi_drop(tmp_path: Path) -> None:
    settings = _settings(tmp_path, context_inject_skills_budget=2500)
    root = _home(tmp_path) / ".agents" / "skills"
    names = [f"card-{i}" for i in range(1, 6)]
    for i, name in enumerate(names):
        _skill(root, f"{chr(ord('a') + i)}-slot", f"name: {name}\ndescription: {_desc_of_len(name, 1000)}")
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert _card("card-1", _desc_of_len("card-1", 1000)) in block
    assert _card("card-2", _desc_of_len("card-2", 1000)) in block
    for name in names[2:]:
        assert _card(name, _desc_of_len(name, 1000)) not in block  # 从末尾逐张丢


def test_skills_single_card_over_budget(tmp_path: Path) -> None:
    settings = _settings(tmp_path, context_inject_skills_budget=10)
    name = "only-card"
    _skill(
        _home(tmp_path) / ".agents" / "skills",
        "only",
        f"name: {name}\ndescription: {_desc_of_len(name, 100)}",
    )
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert _card(name, _desc_of_len(name, 100)) not in block
    assert SKILLS_SECTION not in block  # 清单可为空：段标题不出现


def test_skills_dir_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    block = build_inject_block(settings, home_dir=_home(tmp_path))
    assert SKILLS_SECTION not in block  # 两处 skills 目录都不存在：空清单，零告警
    assert AGENTS_SECTION not in block


def test_skills_duplicate_names_kept(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    home = _home(tmp_path)
    _skill(home / ".agents" / "skills", "dup", "name: dup\ndescription: from home")
    _skill(
        _sandbox(settings) / ".agents" / "skills",
        "dup",
        "name: dup\ndescription: from sandbox",
    )
    block = build_inject_block(settings, home_dir=home)
    assert _card("dup", "from home") in block
    assert _card("dup", "from sandbox") in block  # 同名不去重


# ── 4.5 switch & isolation ───────────────────────────────────────────────────
def test_disabled_byte_identical(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, context_inject_enabled=False)
    home = _home(tmp_path)
    _write(home / ".agents" / "AGENTS.md", "HOME-RULES")
    _write(_sandbox(settings) / "AGENTS.md", "SANDBOX-RULES")
    rt = _runtime(settings)
    state = _state()
    assert rt._build_messages(state, PLANNER_SYSTEM) == [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": "hi"},
    ]
    assert rt._build_messages(_state(), EXECUTOR_SYSTEM) == [
        {"role": "system", "content": EXECUTOR_SYSTEM},
        {"role": "user", "content": "hi"},
    ]


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest 隔离行已在进程级设了 CONTEXT_INJECT_ENABLED=false：先清掉再构造。
    monkeypatch.delenv("CONTEXT_INJECT_ENABLED", raising=False)
    monkeypatch.delenv("CONTEXT_INJECT_SKILLS_BUDGET", raising=False)
    settings = Settings()
    assert settings.context_inject_enabled is True
    assert settings.context_inject_skills_budget == 4000


# ── 4.6 per-task cache ───────────────────────────────────────────────────────
def test_cache_frozen_within_task(tmp_path: Path, tmp_home: Path) -> None:
    settings = _settings(tmp_path)
    _write(tmp_home / ".agents" / "AGENTS.md", "ORIGINAL-HOME")
    _write(_sandbox(settings) / "AGENTS.md", "ORIGINAL-SANDBOX")
    rt = _runtime(settings)
    first = _system_of(rt, _state(), PLANNER_SYSTEM)
    assert "ORIGINAL-HOME" in first
    assert "ORIGINAL-SANDBOX" in first

    # 任务中途改写两层文件：本任务不生效（发现+合并只发生一次）
    _write(tmp_home / ".agents" / "AGENTS.md", "CHANGED-HOME")
    _write(_sandbox(settings) / "AGENTS.md", "CHANGED-SANDBOX")
    second = _system_of(rt, _state(), PLANNER_SYSTEM)
    assert second == first
    assert "CHANGED-HOME" not in second
    assert "CHANGED-SANDBOX" not in second


def test_cache_new_runtime_rereads(tmp_path: Path, tmp_home: Path) -> None:
    settings = _settings(tmp_path)
    _write(_sandbox(settings) / "AGENTS.md", "ORIGINAL-SANDBOX")
    _runtime(settings)  # 旧任务（缓存冻结）
    _write(_sandbox(settings) / "AGENTS.md", "CHANGED-SANDBOX")

    fresh = _system_of(_runtime(settings, task_id="t2"), _state(), PLANNER_SYSTEM)
    assert "CHANGED-SANDBOX" in fresh  # 下任务生效
    assert "ORIGINAL-SANDBOX" not in fresh


def test_cache_planner_executor_share(tmp_path: Path, tmp_home: Path) -> None:
    settings = _settings(tmp_path)
    _write(_sandbox(settings) / "AGENTS.md", "ORIGINAL-SANDBOX")
    rt = _runtime(settings)
    planner_system = _system_of(rt, _state(), PLANNER_SYSTEM)
    executor_system = _system_of(rt, _state(), EXECUTOR_SYSTEM)
    assert planner_system.startswith(PLANNER_SYSTEM)
    assert executor_system.startswith(EXECUTOR_SYSTEM)
    # 两个节点经同一 _build_messages 取到完全一致的注入块
    assert planner_system[len(PLANNER_SYSTEM):] == executor_system[len(EXECUTOR_SYSTEM):]
    assert "ORIGINAL-SANDBOX" in planner_system


# ── 4.7 coexistence with compression ─────────────────────────────────────────
def test_compress_keeps_inject_block(tmp_path: Path, tmp_home: Path) -> None:
    settings = _settings(tmp_path, context_keep_recent=2)
    _write(_sandbox(settings) / "AGENTS.md", "ORIGINAL-SANDBOX")
    rt = _runtime(settings)
    state = _state(_big_messages())
    system = _system_of(rt, state, PLANNER_SYSTEM)

    # 注入块逐字保留（不进压缩，也不被压缩改写）
    assert system == PLANNER_SYSTEM + build_inject_block(settings, home_dir=tmp_home)
    assert "ORIGINAL-SANDBOX" in system
    assert ENV_SECTION in system

    # 历史被压缩
    assert state["compressed"] is True
    assert len(state["messages"]) == 3
    assert str(state["messages"][0]["content"]).startswith("[上下文已截断")


def test_compress_budget_excludes_inject(tmp_path: Path, tmp_home: Path) -> None:
    on = _settings(tmp_path, context_keep_recent=2)
    _write(_sandbox(on) / "AGENTS.md", "ORIGINAL-SANDBOX")
    state_on = _state(_big_messages())
    _system_of(_runtime(on), state_on, PLANNER_SYSTEM)

    off = make_settings(tmp_path, context_inject_enabled=False, context_keep_recent=2)
    state_off = _state(_big_messages())
    _system_of(_runtime(off), state_off, PLANNER_SYSTEM)

    assert state_on["compressed"] is True and state_off["compressed"] is True
    # 注入块不计入压缩预算口径：写回的 context_tokens 与无注入时相等
    assert state_on["context_tokens"] == state_off["context_tokens"]


# ── 4.8 injection nodes ──────────────────────────────────────────────────────
def test_subtask_runtime_shares(tmp_path: Path, tmp_home: Path) -> None:
    settings = _settings(tmp_path)
    _write(_sandbox(settings) / "AGENTS.md", "ORIGINAL-SANDBOX")
    main_rt = _runtime(settings)
    # subagent.py 的构造路径：同一 tm/settings，confirm_enabled=False，无 subagent 执行器
    sub_rt = _runtime(settings, task_id="research:sub:abcd1234", confirm_enabled=False)
    main_system = _system_of(main_rt, _state(), PLANNER_SYSTEM)
    sub_system = _system_of(sub_rt, _state(), PLANNER_SYSTEM)
    assert sub_system == main_system
    assert "ORIGINAL-SANDBOX" in sub_system


def test_aux_llm_not_injected(tmp_path: Path, tmp_home: Path) -> None:
    settings = _settings(
        tmp_path, context_keep_recent=2, context_compress_strategy="summarize"
    )
    _write(_sandbox(settings) / "AGENTS.md", "ORIGINAL-SANDBOX")
    aux = _RecordingLLM()
    rt = _runtime(settings, aux=aux)
    system = _system_of(rt, _state(_big_messages()), PLANNER_SYSTEM)

    assert system != PLANNER_SYSTEM  # 主 system 侧确实带注入块
    assert aux.payloads, "summarize 策略应调用 aux"
    payload = " ".join(
        str(m.get("content") or "") for msgs in aux.payloads for m in msgs
    )
    # aux 只碰 messages：注入段标题一个都不该出现
    assert AGENTS_SECTION not in payload
    assert SKILLS_SECTION not in payload
    assert ENV_SECTION not in payload
    assert "ORIGINAL-SANDBOX" not in payload


# ─ 4.9 config parsing ───────────────────────────────────────────────────────
def test_env_override_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_INJECT_ENABLED", "false")
    settings = Settings(data_dir=str(tmp_path), artifacts_dir=str(tmp_path / "artifacts"))
    assert settings.context_inject_enabled is False


def test_env_override_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_INJECT_SKILLS_BUDGET", "1000")
    settings = Settings(data_dir=str(tmp_path), artifacts_dir=str(tmp_path / "artifacts"))
    assert settings.context_inject_skills_budget == 1000