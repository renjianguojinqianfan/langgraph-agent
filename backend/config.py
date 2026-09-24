"""Application settings loaded from environment / .env file.

All business code must obtain configuration through :func:`get_settings`
rather than reading environment variables directly. Never hard-code keys or
paths here; override them through ``.env`` (see ``.env.example``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, List

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent directory of this file's package (langgraph-agent/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed application configuration.

    Values are resolved from environment variables or a ``.env`` file located
    at the project root. Relative ``data_dir`` / ``artifacts_dir`` paths are
    resolved against :data:`PROJECT_ROOT` so the app behaves identically
    regardless of the current working directory.
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── LLM ──
    llm_provider: str = "openai"  # openai | deepseek | ollama | mock
    llm_base_url: str = ""  # 空 -> factory 回退到 provider preset
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_request_timeout_sec: int = 90  # per-request wall clock (SDK default 600s ate live budgets, #13)
    use_mock_llm: bool = False  # when True, an offline MockLLMClient is used

    # ── Agent / orchestration ──
    max_steps: int = 15  # default max planner/executor iterations (P0-1)

    # ── Completion verification (P0-B) ──
    verify_enabled: bool = True  # reflect re-checks deliverables before allowing finish
    verify_max_retries: int = 2  # bounded verify-fail loop-backs before degrading to COMPLETED

    # ── Sandbox ──
    sandbox_timeout: int = 30  # seconds for code execution

    # ── Web search ──
    search_provider: str = "duckduckgo"  # duckduckgo | serpapi
    serpapi_key: str = ""

    # ── Persistence paths (relative to PROJECT_ROOT unless absolute) ──
    data_dir: str = "data"
    artifacts_dir: str = "data/artifacts"

    # ── Context compression (P0 item 1) ──
    context_token_budget: int = 32000  # estimated tokens (chars/4) above this triggers compression; 对标 dsh ratio 制（0.8×窗口）按 128K 窗口保守折算——chars/4 对中文低估真实 token 2~3 倍，故不直接抄 ~105K
    context_keep_recent: int = 10  # keep the most recent N raw messages when compressing
    context_max_messages: int = 0  # 0 = only token-triggered; >0 also triggers by message count
    context_compress_strategy: str = "truncate"  # truncate | summarize (default: zero extra LLM calls)
    context_summary_max_tokens: int = 300  # only used by the `summarize` strategy

    # ── Context injection (P1-A′, context family sub-prefix) ──
    context_inject_enabled: bool = True  # master switch: AGENTS.md + skills + env facts into system
    context_inject_skills_budget: int = 4000  # chars; drop whole cards from the tail when over

    # ── Tool-result eviction (T1.4, context family sub-prefix) ──
    context_evict_enabled: bool = True  # false = pre-T1.4 behaviour (no eviction)
    context_evict_threshold_chars: int = 4000  # evict a single tool message longer than this
    context_evict_protect_recent: int = 10  # never evict the most recent N messages (positional)
    context_evict_head_chars: int = 800  # head preview kept inside the placeholder
    context_evict_tail_chars: int = 400  # tail preview kept inside the placeholder

    # ── Tool resilience (P0 item 2) ──
    tool_failure_threshold: int = 3  # consecutive failures before the circuit opens
    tool_cooldown_sec: int = 30  # seconds the circuit stays open before half-open probe
    tool_backoff_base: float = 1.0  # retry backoff base (seconds)
    tool_backoff_factor: int = 2  # exponential backoff factor
    tool_max_retries: int = 2  # global default max retries (tools may override)

    # ── Plugins (P0 item 3) ──
    plugins_dir: str = "backend/plugins"
    plugins_autoload: bool = True

    # ── Trace (P0 item 4) ──
    trace_enabled: bool = True
    trace_dir: str = "data/traces"

    # ── Risk scan (P1 item 1) ──
    risk_scan_enabled: bool = True  # master switch (false -> skip, zero regression)
    risk_semantic_enabled: bool = False  # LLM semantic analysis (needs aux/main model)
    risk_policy: str = "confirm"  # confirm | pause (default: per-call human confirm)
    risk_danger_keywords: str = ""  # optional JSON array override of the built-in table

    # ── Sub-agent (P1 item 2) ──
    subagent_enabled: bool = True  # master switch
    subagent_max_concurrency: int = 2  # thread-pool parallelism (1 = serial)
    subagent_timeout_sec: int = 120  # single subtask timeout

    # ── Knowledge base (P1 item 3) ──
    kb_enabled: bool = True  # master switch (false -> empty instance)
    kb_dir: str = "data/kb"
    kb_auto_index_artifacts: bool = True  # register artifacts into the KB
    kb_chunk_size: int = 2000  # chunk character cap
    kb_embedding_enabled: bool = False  # vector retrieval placeholder (false = keyword)
    kb_top_k: int = 5  # default retrieval hit count

    # ── Aux LLM (P1 item 4) ──
    aux_llm_enabled: bool = False  # master switch (false -> zero extra LLM calls)
    aux_llm_provider: str = "openai"  # openai | deepseek | ollama | mock
    aux_llm_base_url: str = ""  # empty -> provider preset
    aux_llm_api_key: str = ""
    aux_llm_model: str = ""  # empty -> no aux client (degradation)
    aux_llm_use_mock: bool = False  # offline mock aux (with use_mock_llm for the main)

    # ── Auth (P1 item 5, disabled for local demo) ──
    auth_enabled: bool = False
    auth_token: str = "changeme"
    auth_token_ttl_sec: int = 86400  # issued token lifetime

    # ── OpenAPI tools (P1 item 6) ──
    openapi_enabled: bool = False  # master switch
    openapi_spec_path: str = ""  # local YAML/JSON spec file
    openapi_spec_url: str = ""  # remote spec URL (used when path is empty)
    openapi_api_key: str = ""  # injected into apiKey security schemes
    openapi_global_headers: str = "{}"  # JSON string of headers applied to every call

    # ── MCP client (P2 item 1) ──
    mcp_enabled: bool = True  # master switch (false -> zero MCP tools / subprocesses)
    mcp_servers: str = "[]"  # JSON array: [{name, command, args[], env{}, enabled, cwd?, transport?, url?}]
    mcp_timeout_sec: float = 30.0  # single call_tool timeout
    mcp_connect_timeout_sec: float = 15.0  # initialize + list_tools timeout per server
    mcp_force_confirm: str = "[]"  # JSON array of mcp__{server}__{tool} names that always confirm

    # ── Git tools (P2 item 2) ──
    git_enabled: bool = True  # master switch (false -> zero Git tools)
    git_repo_dir: str = "data/repos"  # Git operations root (relative to PROJECT_ROOT)
    git_timeout_sec: float = 30.0  # single git call timeout

    # ── Checkpoints (spec Issue #4: durable state for stop/resume) ──
    checkpoint_enabled: bool = True  # master switch (false -> no saver mounted)
    checkpoint_dir: str = ""  # "" -> <data_dir>/checkpoints

    # ── Snapshots (P1-B: workspace file rollback, docs/specs/p1-b-rollback.md) ──
    snapshot_enabled: bool = True  # master switch: per-write before-image capture + /rollback
    snapshot_dir: str = ""  # "" -> <data_dir>/snapshots (outside the sandbox root)
    snapshot_retention_days: int = 30  # startup sweep drops task dirs older than this (<=0 = keep)

    # ── Server ──
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "*"  # comma separated

    # ── Derived absolute paths ──
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def artifacts_path(self) -> Path:
        p = Path(self.artifacts_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def plugins_path(self) -> Path:
        p = Path(self.plugins_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def trace_path(self) -> Path:
        p = Path(self.trace_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def kb_path(self) -> Path:
        p = Path(self.kb_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def checkpoint_path(self) -> Path:
        """Checkpoint store directory (defaults under ``data_path``)."""
        p = Path(self.checkpoint_dir)
        if not self.checkpoint_dir or not p.is_absolute():
            p = self.data_path / "checkpoints"
        return p

    @property
    def git_repo_path(self) -> Path:
        p = Path(self.git_repo_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def snapshots_path(self) -> Path:
        """P1-B snapshot store root (defaults under ``data_path``).

        Outside the sandbox on purpose: snapshots must not be visible to
        ``glob`` / ``grep`` / ``read`` nor auto-indexed into the KB, and the
        headless run root takes them with it when the temp dir goes away.
        """
        p = Path(self.snapshot_dir)
        if not self.snapshot_dir or not p.is_absolute():
            p = self.data_path / "snapshots"
        return p

    @staticmethod
    def _parse_json_list(raw: str, default: List[Any]) -> List[Any]:
        """Parse a JSON-array string field, falling back to ``default``."""
        if not raw or not str(raw).strip():
            return list(default)
        try:
            data = json.loads(str(raw))
            return data if isinstance(data, list) else list(default)
        except Exception:
            return list(default)

    @property
    def mcp_servers_list(self) -> List[dict]:
        """``mcp_servers`` as a parsed list of dicts (invalid JSON -> [])."""
        return self._parse_json_list(self.mcp_servers, [])

    @property
    def mcp_force_confirm_list(self) -> List[str]:
        """``mcp_force_confirm`` as a parsed list of tool full names."""
        return [str(x) for x in self._parse_json_list(self.mcp_force_confirm, [])]

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins in ("*", ""):
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton."""
    return Settings()


def reset_settings() -> None:
    """Clear the cached settings (used by tests to re-read overrides)."""
    get_settings.cache_clear()
