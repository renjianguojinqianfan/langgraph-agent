
"""Model providers, model catalog and credentials (M1 / ADR 0010, #243 P2-2).

`data/models.json` is at schema version 2: a list of **providers**
(id / name / protocol / base_url / api_key_env / preset) and a list of **model
entries** (id / provider_id / model / name / source); `default` names a model
entry id. That format is the only accepted contract (ADR 0013 decision 1): a
v1 file (a flat `models` list, or any other schema version below 2) is refused
with a readable error and left byte-for-byte untouched — no migration, no
backup chain, no compatibility alias.

The runtime view of one entry is :class:`ResolvedModel` (``ModelStore.resolve``):
the entry plus its provider, with the D-36 values already inherited (entry
override else provider default). The current call path, the task-manager client
cache and the startup key check resolve through it; there is no second flat
projection.

Pasted keys live in `~/.qianfan/credentials.json` (outside the repo) keyed by
**provider id**; the environment variable named by the provider is the fallback.
Neither file is in `Settings` (the settings object is cached at startup).
No API response ever returns a key: only `key_set` / `key_source`.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..config import Settings
from ..core.llm.identity import QIANFAN_USER_AGENT
from ..core.llm.protocols import (
    IMPLEMENTED_PROTOCOLS,
    PROTOCOL_OPENAI,
    SUPPORTED_PROTOCOLS,
)
from ..core.llm.protocols import PROTOCOL_ANTHROPIC as PROTOCOL_ANTHROPIC  # re-exported
from ..utils.logging import get_logger

logger = get_logger("models")

DEFAULT_MODEL_ID = "default"
_DEEPSEEK_FALLBACK = "https://api.deepseek.com/v1"

#: Current on-disk schema version (D-33). A v1 file omits this key.
SCHEMA_VERSION = 2

#: Provider ids are used to derive the credentials key, so they are constrained
#: for new providers (D-34). Migrated v1 ids are opaque hex strings and stay
#: readable as-is; only creation validates the pattern.
PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Model discovery (D-34): one GET, bounded time and response size; the body is
#: never stored and error text never contains the key.
DISCOVER_TIMEOUT_SEC = 10.0
DISCOVER_MAX_BYTES = 1_000_000
_DISCOVERY_UNSUPPORTED_PROTOCOL = (
    "协议暂不支持发现：当前只有 OpenAI 兼容协议支持自动获取模型目录，"
    "请手动添加模型。"
)

#: Clock seam for the discovery **body-phase** deadline (tests replace this one
#: function). It cuts slow-drip bodies off; connect / write / header waits are
#: still bounded only by httpx's per-operation timeout, so the overall request
#: ceiling is the sum of those phases, not a strict total (LOW-A rework: the
#: earlier "total wall clock" wording overstated it).
_monotonic = time.monotonic


class UnsupportedModelsFileError(RuntimeError):
    """`models.json` is a format this build does not accept (ADR 0013).

    Raised while loading, before anything is written: v1 (a flat `models` list
    without a version stamp) and any schema version below 2 are refused with
    the file path, the expected contract and a next step. The file itself is
    left byte-for-byte untouched — no migration, rename or backup.
    """


class CredentialsError(RuntimeError):
    """The credential file outside the repo could not be written (#244).

    Written as a domain error so the API can answer 4xx with the path and a
    next step, instead of leaking an `OSError` as a 500 with no reason.
    """


class ProviderDiscoveryError(RuntimeError):
    """A readable "why the model catalog could not be fetched" (D-34)."""


def require_implemented_protocol(protocol: str) -> None:
    """Reject a protocol this build cannot store or speak (D-34 write face).

    Both supported protocols are implemented as of D-35 (the Anthropic adapter
    lands with this slice); only a value outside SUPPORTED_PROTOCOLS is refused.
    The second check keeps a seam for a future stored-but-unwired protocol.
    """
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(
            f"不支持的协议：{protocol!r}；可选：{', '.join(SUPPORTED_PROTOCOLS)}"
        )
    if protocol not in IMPLEMENTED_PROTOCOLS:  # pragma: no cover - future gap seam
        raise ValueError(
            f"协议尚未实现：{protocol!r}；当前可用：{', '.join(IMPLEMENTED_PROTOCOLS)}"
        )


def _optional_float(value: Any) -> Optional[float]:
    """Lenient load of an optional numeric field (bad value = not configured)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _optional_str(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


#: Sentinel for "the update did not mention this field". Explicit `None` is a
#: real value on the D-36 fields: it clears the override and falls back to the
#: provider default / client default (MEDIUM-2b, PR #298 rework).
_UNSET: Any = object()


def _pick(value: Any, fallback: Any) -> Any:
    """Sentinel-aware update: omitted keeps the stored value, None clears it."""
    return fallback if value is _UNSET else value


@dataclass
class Provider:
    """One API host: several model entries share its address, key and protocol.

    The four sampling fields (D-36) are provider-level **defaults**; a model
    entry may override any of them. ``None`` means "not configured": the
    client falls back to its documented default (temperature 0.2; Anthropic
    ``max_tokens`` 4096) or, for ``thinking`` / ``reasoning_effort``, sends
    nothing at all.
    """

    id: str
    name: str
    protocol: str = PROTOCOL_OPENAI
    base_url: str = ""
    api_key_env: str = "LLM_API_KEY"
    preset: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None


@dataclass
class ModelEntry:
    """One selectable model under a provider (`Task.model` stores its id)."""

    id: str
    provider_id: str
    model: str
    name: str
    source: str = "manual"  # manual | migrated
    #: D-36 overrides: when set they win over the provider-level default.
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None


@dataclass(frozen=True)
class ResolvedModel:
    """The runtime view of one model entry: what a call needs, nothing else.

    Replaces the old flat `ModelProfile` projection on the call path (ADR 0013
    decision 1). ``id`` is the model entry id a task pins; the provider fields
    decide the adapter; the four D-36 values are already inherited (entry
    override else provider default) and ``None`` means "the client's own
    default".
    """

    id: str
    provider_id: str
    name: str
    model: str
    protocol: str
    base_url: str
    api_key_env: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None


class ModelStore:
    def __init__(self, settings: Settings, *, home_dir: Optional[Path] = None) -> None:
        self.settings = settings
        self.data_file = settings.data_path / "models.json"
        self.credentials_file = (home_dir or Path.home()) / ".qianfan" / "credentials.json"
        self.providers: Dict[str, Provider] = {}
        self.model_entries: Dict[str, ModelEntry] = {}
        self.default_id = DEFAULT_MODEL_ID
        self._load()

    # ── seeding / persistence ──
    def _seed(self) -> None:
        # DeepSeek is the only built-in preset (M1): an explicit base_url is a
        # custom host, anything else falls back to the DeepSeek endpoint.
        base_url = self.settings.llm_base_url or _DEEPSEEK_FALLBACK
        if not self.settings.llm_base_url and (self.settings.llm_provider or "").lower() not in (
            "",
            "deepseek",
        ):
            logger.warning(
                "llm_provider %r has no preset; seeding the DeepSeek endpoint",
                self.settings.llm_provider,
            )
        preset = not self.settings.llm_base_url and (
            self.settings.llm_provider or "deepseek"
        ).lower() == "deepseek"
        provider = Provider(
            id=DEFAULT_MODEL_ID,
            name="默认配置",
            protocol=PROTOCOL_OPENAI,
            base_url=base_url,
            api_key_env="LLM_API_KEY",
            preset=preset,
        )
        entry = ModelEntry(
            id=DEFAULT_MODEL_ID,
            provider_id=DEFAULT_MODEL_ID,
            model=self.settings.llm_model,
            name="默认配置",
            source="manual",
        )
        self.providers = {provider.id: provider}
        self.model_entries = {entry.id: entry}
        self.default_id = entry.id
        self._save()

    @staticmethod
    def _quarantine(path: Path) -> None:
        """Move an unreadable state file aside instead of overwriting it."""
        backup = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            os.replace(path, backup)
            logger.warning("unreadable %s moved to %s", path.name, backup.name)
        except OSError:  # pragma: no cover - defensive
            pass

    def _load(self) -> None:
        try:
            text = self.data_file.read_text(encoding="utf-8")
        except OSError:
            self._seed()
            return
        try:
            raw = json.loads(text)
        except ValueError:
            self._quarantine(self.data_file)
            self._seed()
            return
        if not isinstance(raw, dict):
            self._quarantine(self.data_file)
            self._seed()
            return
        try:
            version = int(raw.get("schema_version") or 0)
        except (TypeError, ValueError):
            version = 0
        if version == SCHEMA_VERSION:
            self._load_v2(raw)
        elif version < SCHEMA_VERSION:
            # v1 had no version stamp at all: a `models` list of flat profiles.
            # ADR 0013 decision 1: refuse it readably and leave the bytes alone.
            raise UnsupportedModelsFileError(
                f"{self.data_file} 是旧版 models.json（schema_version={version}）；"
                f"本版本只接受 schema_version {SCHEMA_VERSION}"
                "（Provider + 模型目录）。文件保持原样未改动："
                "请先备份并删除该文件后，在设置页重新添加 Provider 与模型。"
            )
        else:
            # A newer build wrote this file. Treat it like any unreadable state
            # file: move it aside, never rewrite it in place, and say why.
            logger.warning(
                "models.json schema_version %d is newer than this build (%d); "
                "moving it aside and seeding the default provider",
                version,
                SCHEMA_VERSION,
            )
            self._quarantine(self.data_file)
            self._seed()

    def _load_v2(self, raw: Dict[str, Any]) -> None:
        providers: Dict[str, Provider] = {}
        for item in raw.get("providers") or []:
            if not isinstance(item, dict):
                continue
            try:
                provider = Provider(
                    id=str(item["id"]),
                    name=str(item.get("name") or item["id"]),
                    protocol=str(item.get("protocol") or PROTOCOL_OPENAI),
                    base_url=str(item.get("base_url") or ""),
                    api_key_env=str(item.get("api_key_env") or "LLM_API_KEY"),
                    preset=bool(item.get("preset")),
                    temperature=_optional_float(item.get("temperature")),
                    max_tokens=_optional_int(item.get("max_tokens")),
                    thinking=_optional_bool(item.get("thinking")),
                    reasoning_effort=_optional_str(item.get("reasoning_effort")),
                )
            except (KeyError, TypeError):
                continue
            if not (provider.id and provider.base_url):
                continue
            if provider.protocol not in SUPPORTED_PROTOCOLS:
                # A protocol this build does not know how to store is a bad
                # file entry, not a runtime surprise: drop the provider (its
                # entries fall with it); a file left with no usable entry is
                # quarantined below, same as any other broken state file.
                logger.warning(
                    "dropping provider %r: unknown protocol %r",
                    provider.id,
                    provider.protocol,
                )
                continue
            providers[provider.id] = provider
        entries: Dict[str, ModelEntry] = {}
        for item in raw.get("models") or []:
            if not isinstance(item, dict):
                continue
            try:
                entry = ModelEntry(
                    id=str(item["id"]),
                    provider_id=str(item["provider_id"]),
                    model=str(item.get("model") or ""),
                    name=str(item.get("name") or item["id"]),
                    source=str(item.get("source") or "manual"),
                    temperature=_optional_float(item.get("temperature")),
                    max_tokens=_optional_int(item.get("max_tokens")),
                    thinking=_optional_bool(item.get("thinking")),
                    reasoning_effort=_optional_str(item.get("reasoning_effort")),
                )
            except (KeyError, TypeError):
                continue
            if entry.id and entry.provider_id in providers and entry.model:
                entries[entry.id] = entry
        if not entries:
            self._quarantine(self.data_file)
            self._seed()
            return
        self.providers = providers
        self.model_entries = entries
        default = str(raw.get("default") or "")
        self.default_id = default if default in entries else next(iter(entries))

    def _save(self) -> None:
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "default": self.default_id,
            "providers": [asdict(provider) for provider in self.providers.values()],
            "models": [asdict(entry) for entry in self.model_entries.values()],
        }
        tmp = self.data_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.data_file)

    # ── model entries ──
    def get_model(self, model_id: str) -> Optional[ModelEntry]:
        return self.model_entries.get(model_id)

    @staticmethod
    def _effective_params(provider: Provider, entry: ModelEntry) -> Dict[str, Any]:
        """D-36 precedence: the entry overrides the provider default; a
        still-None value is the client's documented default (temperature 0.2 /
        Anthropic max_tokens 4096 / nothing sent for thinking)."""
        return {
            "temperature": (
                entry.temperature
                if entry.temperature is not None
                else provider.temperature
            ),
            "max_tokens": (
                entry.max_tokens if entry.max_tokens is not None else provider.max_tokens
            ),
            "thinking": (
                entry.thinking if entry.thinking is not None else provider.thinking
            ),
            "reasoning_effort": (
                entry.reasoning_effort
                if entry.reasoning_effort is not None
                else provider.reasoning_effort
            ),
        }

    def resolve(self, model_id: Optional[str]) -> Optional[ResolvedModel]:
        """The one runtime view a model call is built from (ADR 0013).

        An explicit id must exist; only an empty id means "the default".
        Falling back to the default for a deleted id would silently send the
        task's content to another provider, so that case stays ``None`` and the
        caller fails loudly.
        """
        entry = self.model_entries.get(model_id or self.default_id)
        if entry is None:
            return None
        provider = self.providers.get(entry.provider_id)
        if provider is None:  # pragma: no cover - load() drops orphan entries
            return None
        return ResolvedModel(
            id=entry.id,
            provider_id=provider.id,
            name=entry.name,
            model=entry.model,
            protocol=provider.protocol,
            base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            **self._effective_params(provider, entry),
        )

    def model_public(self, model_id: str) -> Dict[str, Any]:
        """One model entry as stored (id / provider_id / model / name / source)."""
        entry = self.model_entries.get(model_id)
        if entry is None:
            raise KeyError(model_id)
        return asdict(entry)

    # ── validation ──
    @staticmethod
    def _validate_provider_fields(
        *,
        base_url: str,
        api_key_env: str,
        protocol: str,
        provider_id: Optional[str] = None,
    ) -> None:
        if provider_id is not None and not PROVIDER_ID_RE.fullmatch(provider_id):
            raise ValueError(
                f"invalid provider id {provider_id!r}: must match ^[a-z][a-z0-9_-]*$"
            )
        if not base_url:
            raise ValueError("base_url is required")
        if urlparse(base_url).scheme not in ("http", "https"):
            raise ValueError("base_url must be an http(s) URL")
        if not _ENV_NAME_RE.fullmatch(api_key_env):
            raise ValueError("api_key_env must be an environment variable name")
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError(
                f"unsupported protocol {protocol!r} (choose from "
                f"{', '.join(SUPPORTED_PROTOCOLS)})"
            )

    @staticmethod
    def _validate_model_params(
        *,
        protocol: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        thinking: Optional[bool],
        reasoning_effort: Optional[str],
    ) -> None:
        """Validate the D-36 sampling fields (None = not configured).

        temperature's legal range is protocol-specific (OpenAI-compatible
        0..2, Anthropic Messages 0..1): checking at the write face turns an
        upstream 400 into a readable 400 before the card exists.
        """
        limit = 1.0 if protocol == PROTOCOL_ANTHROPIC else 2.0
        if temperature is not None and not 0.0 <= float(temperature) <= limit:
            raise ValueError(
                f"temperature must be between 0 and {limit:g} "
                f"for protocol {protocol!r}"
            )
        if max_tokens is not None and int(max_tokens) < 1:
            raise ValueError("max_tokens must be a positive integer")
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError("thinking must be a boolean")
        if reasoning_effort is not None and reasoning_effort not in (
            "low",
            "medium",
            "high",
        ):
            raise ValueError("reasoning_effort must be one of low / medium / high")

    @staticmethod
    def _validate_protocol_params(
        *,
        protocol: str,
        thinking: Optional[bool],
        reasoning_effort: Optional[str],
    ) -> None:
        """Reject an effective combination that could never run (ADR 0011).

        Anthropic extended thinking needs the previous assistant thinking block
        with its signature on the next tool turn; this codebase rebuilds history
        from OpenAI-style messages and cannot carry it. A card with
        protocol=anthropic + effective thinking=True / reasoning_effort would
        therefore only fail at run time — refuse it at the write face instead.
        """
        if protocol != PROTOCOL_ANTHROPIC:
            return
        if thinking:
            raise ValueError(
                "Anthropic 适配器暂不支持 thinking（需要保留 thinking 签名）；"
                "请先关闭或清空 thinking 再使用 anthropic 协议"
            )
        if reasoning_effort is not None:
            raise ValueError(
                "Anthropic 适配器暂不支持 reasoning_effort；"
                "请先清空 reasoning_effort 再使用 anthropic 协议"
            )

    def _validate_provider_entries_runnable(self, provider: Provider) -> None:
        """Check every entry's effective params against the provider protocol.

        A protocol switch (openai-compatible -> anthropic) must not leave an
        entry behind that only ran under the old rules: a thinking entry, or an
        entry-level temperature=1.5 which is legal for OpenAI-compatible but
        above Anthropic's 0..1 (MEDIUM-2a / PR #298 rework batch 2). The check
        uses the *effective* values (entry override else provider default).
        """
        for entry in self.model_entries.values():
            if entry.provider_id != provider.id:
                continue
            params = self._effective_params(provider, entry)
            self._validate_model_params(protocol=provider.protocol, **params)
            self._validate_protocol_params(
                protocol=provider.protocol,
                thinking=params["thinking"],
                reasoning_effort=params["reasoning_effort"],
            )

    @staticmethod
    def _require_model_name(model: Any) -> str:
        """Trimmed non-empty model name.

        An empty value would be accepted, then dropped by the next load, and
        the entry would silently disappear from disk with the next save.
        """
        cleaned = str(model or "").strip()
        if not cleaned:
            raise ValueError("model is required")
        return cleaned

    def _new_entry_id(self, reserved: Optional[set[str]] = None) -> str:
        candidate = uuid.uuid4().hex[:12]
        while candidate in self.model_entries or (
            reserved is not None and candidate in reserved
        ):
            candidate = uuid.uuid4().hex[:12]
        return candidate

    def _build_entry(
        self,
        provider: Provider,
        *,
        model: str,
        name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        reasoning_effort: Optional[str] = None,
        reserved: Optional[set[str]] = None,
    ) -> ModelEntry:
        """Validate one entry's D-36 params and build it without persisting.

        Shared by the single-entry and atomic batch create paths: the caller
        owns the provider/duplicate checks and the rollback boundary.
        """
        self._validate_model_params(
            protocol=provider.protocol,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
        self._validate_protocol_params(
            protocol=provider.protocol,
            thinking=thinking if thinking is not None else provider.thinking,
            reasoning_effort=(
                reasoning_effort
                if reasoning_effort is not None
                else provider.reasoning_effort
            ),
        )
        return ModelEntry(
            id=self._new_entry_id(reserved),
            provider_id=provider.id,
            model=model,
            name=(name or "").strip() or model,
            source="manual",
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )

    def set_default(self, model_id: str) -> None:
        if model_id not in self.model_entries:
            raise KeyError(model_id)
        self.default_id = model_id
        self._save()

    # ── providers (v2 API) ──
    def get_provider(self, provider_id: str) -> Optional[Provider]:
        return self.providers.get(provider_id)

    def _provider_public(
        self, provider: Provider, pasted: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        key, source = self._provider_key_state(provider.id, pasted=pasted)
        data = asdict(provider)
        data["key_set"] = bool(key)
        data["key_source"] = source
        return data

    def provider_public(self, provider_id: str) -> Dict[str, Any]:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        return self._provider_public(provider)

    def list_providers(self) -> List[Dict[str, Any]]:
        pasted = self._credentials()
        return [
            self._provider_public(provider, pasted=pasted)
            for provider in self.providers.values()
        ]

    def add_provider(
        self,
        *,
        id: str,
        name: str,
        base_url: str,
        protocol: str = PROTOCOL_OPENAI,
        api_key_env: str = "LLM_API_KEY",
        key: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        reasoning_effort: Optional[str] = None,
        models: Optional[List[Dict[str, Any]]] = None,
    ) -> Provider:
        provider_id = id.strip()
        if provider_id in self.providers:
            raise ValueError(f"provider {provider_id!r} already exists")
        protocol = protocol.strip() or PROTOCOL_OPENAI
        base_url = base_url.strip()
        api_key_env = (api_key_env or "LLM_API_KEY").strip()
        self._validate_provider_fields(
            base_url=base_url,
            api_key_env=api_key_env,
            protocol=protocol,
            provider_id=provider_id,
        )
        self._validate_model_params(
            protocol=protocol,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
        self._validate_protocol_params(
            protocol=protocol,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
        provider = Provider(
            id=provider_id,
            name=(name.strip() or provider_id),
            protocol=protocol,
            base_url=base_url,
            api_key_env=api_key_env,
            preset=False,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
        # #352: validate every draft entry before touching memory or disk, so
        # the provider and its catalog are all-or-nothing.
        entries = self._prepare_new_entries(provider, models or [])
        # Persist the provider (and its drafts) first, then write the key: a
        # credentials failure must not leave an orphaned secret keyed by an id
        # the store never accepted. The other half of that trade-off is
        # compensation — a failed key write rolls the card and its entries back
        # so the user is not stuck with a half-created provider whose retry
        # answers "already exists" (and the 409 reason is not replaced by that
        # 400 on the next attempt).
        self.providers[provider.id] = provider
        for entry in entries:
            self.model_entries[entry.id] = entry
        try:
            self._save()
        except Exception:
            self._rollback_provider_create(provider.id, entries, persist=False)
            raise
        if key.strip():
            try:
                self.set_provider_key(provider.id, key)
            except CredentialsError:
                self._rollback_provider_create(provider.id, entries)
                raise
        return provider

    def _prepare_new_entries(
        self, provider: Provider, drafts: List[Dict[str, Any]]
    ) -> List[ModelEntry]:
        """Validate provider model drafts; build entries only, no mutation."""
        entries: List[ModelEntry] = []
        seen_models: set[str] = set()
        reserved_ids: set[str] = set()
        for draft in drafts:
            if not isinstance(draft, dict):
                raise ValueError("model entries must be objects")
            model = self._require_model_name(draft.get("model"))
            if model in seen_models:
                raise ValueError(
                    f"model {model!r} already exists for provider {provider.id!r}"
                )
            seen_models.add(model)
            entry = self._build_entry(
                provider,
                model=model,
                name=draft.get("name"),
                temperature=draft.get("temperature"),
                max_tokens=draft.get("max_tokens"),
                thinking=draft.get("thinking"),
                reasoning_effort=draft.get("reasoning_effort"),
                reserved=reserved_ids,
            )
            reserved_ids.add(entry.id)
            entries.append(entry)
        return entries

    def _rollback_provider_create(
        self,
        provider_id: str,
        entries: List[ModelEntry],
        *,
        persist: bool = True,
    ) -> None:
        """Undo an in-memory provider create; optionally rewrite the file."""
        self.providers.pop(provider_id, None)
        for entry in entries:
            self.model_entries.pop(entry.id, None)
        if not persist:
            return
        try:
            self._save()
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning(
                "rollback after a failed key write could not persist %s: %s",
                self.data_file,
                exc,
            )

    def update_provider(
        self,
        provider_id: str,
        *,
        name: Optional[str] = None,
        base_url: Optional[str] = None,
        protocol: Optional[str] = None,
        api_key_env: Optional[str] = None,
        key: Optional[str] = None,
        # D-36 params: omitted (_UNSET) = unchanged, explicit None = clear the
        # override so the value falls back to the provider / client default.
        temperature: Any = _UNSET,
        max_tokens: Any = _UNSET,
        thinking: Any = _UNSET,
        reasoning_effort: Any = _UNSET,
    ) -> Provider:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        candidate = replace(
            provider,
            name=provider.name if name is None else (name.strip() or provider.name),
            base_url=provider.base_url if base_url is None else base_url.strip(),
            protocol=provider.protocol if protocol is None else protocol.strip(),
            api_key_env=(
                provider.api_key_env if api_key_env is None else api_key_env.strip()
            ),
            preset=(
                provider.preset
                if base_url is None or base_url.strip() == provider.base_url
                else False
            ),
            temperature=_pick(temperature, provider.temperature),
            max_tokens=_pick(max_tokens, provider.max_tokens),
            thinking=_pick(thinking, provider.thinking),
            reasoning_effort=_pick(reasoning_effort, provider.reasoning_effort),
        )
        self._validate_provider_fields(
            base_url=candidate.base_url,
            api_key_env=candidate.api_key_env,
            protocol=candidate.protocol,
        )
        self._validate_model_params(
            protocol=candidate.protocol,
            temperature=candidate.temperature,
            max_tokens=candidate.max_tokens,
            thinking=candidate.thinking,
            reasoning_effort=candidate.reasoning_effort,
        )
        self._validate_protocol_params(
            protocol=candidate.protocol,
            thinking=candidate.thinking,
            reasoning_effort=candidate.reasoning_effort,
        )
        self._validate_provider_entries_runnable(candidate)
        # Validate the key write before touching the provider record: a refused
        # credentials write leaves the card exactly as it was.
        if key is not None:
            self.set_provider_key(provider_id, key)
        self.providers[provider_id] = candidate
        self._save()
        return candidate

    def delete_provider(self, provider_id: str) -> None:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        entries = [
            entry
            for entry in self.model_entries.values()
            if entry.provider_id == provider_id
        ]
        if len(self.model_entries) - len(entries) < 1:
            raise ValueError("cannot delete the last model profile")
        self._revoke_provider_key(provider.id)
        for entry in entries:
            del self.model_entries[entry.id]
        del self.providers[provider.id]
        if self.default_id not in self.model_entries:
            self.default_id = next(iter(self.model_entries))
        self._save()

    # ── model catalog (v2 API) ──
    def list_models(self, provider_id: str) -> List[Dict[str, Any]]:
        if provider_id not in self.providers:
            raise KeyError(provider_id)
        return [
            asdict(entry)
            for entry in self.model_entries.values()
            if entry.provider_id == provider_id
        ]

    def add_model(
        self,
        provider_id: str,
        *,
        model: str,
        name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        reasoning_effort: Optional[str] = None,
    ) -> ModelEntry:
        if provider_id not in self.providers:
            raise KeyError(provider_id)
        provider = self.providers[provider_id]
        model = self._require_model_name(model)
        if any(
            entry.provider_id == provider_id and entry.model == model
            for entry in self.model_entries.values()
        ):
            raise ValueError(f"model {model!r} already exists for provider {provider_id!r}")
        entry = self._build_entry(
            provider,
            model=model,
            name=name,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
        self.model_entries[entry.id] = entry
        self._save()
        return entry

    def update_model(
        self,
        provider_id: str,
        model_id: str,
        *,
        model: Optional[str] = None,
        name: Optional[str] = None,
        # D-36 params: omitted (_UNSET) = unchanged, explicit None = clear the
        # override (falls back to the provider default / client default).
        temperature: Any = _UNSET,
        max_tokens: Any = _UNSET,
        thinking: Any = _UNSET,
        reasoning_effort: Any = _UNSET,
    ) -> ModelEntry:
        """Edit one catalog entry (D-36 write path).

        ``temperature`` / ``max_tokens`` / ``thinking`` / ``reasoning_effort``
        set on the entry override the provider-level defaults. Passing one of
        them as an explicit ``None`` clears the override (omitting it keeps the
        stored value) — that is the only way back to "inherit from provider".
        """
        if provider_id not in self.providers:
            raise KeyError(provider_id)
        provider = self.providers[provider_id]
        entry = self.model_entries.get(model_id)
        if entry is None or entry.provider_id != provider_id:
            raise KeyError(model_id)
        candidate_model = self._require_model_name(
            entry.model if model is None else model
        )
        if any(
            other.id != entry.id
            and other.provider_id == provider_id
            and other.model == candidate_model
            for other in self.model_entries.values()
        ):
            raise ValueError(
                f"model {candidate_model!r} already exists for provider {provider_id!r}"
            )
        candidate = replace(
            entry,
            model=candidate_model,
            name=entry.name if name is None else ((name or "").strip() or entry.name),
            temperature=_pick(temperature, entry.temperature),
            max_tokens=_pick(max_tokens, entry.max_tokens),
            thinking=_pick(thinking, entry.thinking),
            reasoning_effort=_pick(reasoning_effort, entry.reasoning_effort),
        )
        self._validate_model_params(
            protocol=provider.protocol,
            temperature=candidate.temperature,
            max_tokens=candidate.max_tokens,
            thinking=candidate.thinking,
            reasoning_effort=candidate.reasoning_effort,
        )
        self._validate_protocol_params(
            protocol=provider.protocol,
            thinking=(
                candidate.thinking
                if candidate.thinking is not None
                else provider.thinking
            ),
            reasoning_effort=(
                candidate.reasoning_effort
                if candidate.reasoning_effort is not None
                else provider.reasoning_effort
            ),
        )
        self.model_entries[entry.id] = candidate
        self._save()
        return candidate

    def delete_model(self, provider_id: str, model_id: str) -> None:
        """Delete one catalog entry and keep ``default`` pointing at a live one.

        The last model entry of the whole catalog cannot be deleted (there must
        always be a default that resolves). A provider with no entries is
        unreachable through the API while that invariant holds, so deleting
        from it is simply "model not found" (``KeyError``) — the same answer
        legacy ``delete()`` gives for an unknown id; no separate empty-provider
        branch exists on purpose.
        """
        if provider_id not in self.providers:
            raise KeyError(provider_id)
        entry = self.model_entries.get(model_id)
        if entry is None or entry.provider_id != provider_id:
            raise KeyError(model_id)
        if len(self.model_entries) == 1:
            raise ValueError("cannot delete the last model profile")
        del self.model_entries[model_id]
        if self.default_id == model_id:
            self.default_id = next(iter(self.model_entries))
        self._save()

    def ensure_model_entry(self, provider_id: str, model: str) -> ModelEntry:
        """Find the (provider, model) entry, creating a `source: manual` one.

        Used by `PUT /api/tasks/{id}/model` with `{provider, model}`: a raw
        model id outside the catalog stays usable (D-34), and switching to the
        same pair twice reuses one entry instead of growing the catalog.
        """
        if provider_id not in self.providers:
            raise KeyError(provider_id)
        model = self._require_model_name(model)
        for entry in self.model_entries.values():
            if entry.provider_id == provider_id and entry.model == model:
                return entry
        return self.add_model(provider_id, model=model)

    # ── discovery (D-34) ──
    def discover_models(
        self, provider_id: str, *, transport: Any = None
    ) -> List[Dict[str, Any]]:
        """Candidate models for a provider; read-only, never persisted.

        `added` tells the page whether the candidate is already in the
        catalog. Tests inject an `httpx` transport so no real network is used.
        """
        provider = self.providers.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        existing = {
            entry.model
            for entry in self.model_entries.values()
            if entry.provider_id == provider_id
        }
        candidates = discover_provider_models(
            provider, self.provider_key_for(provider_id), transport=transport
        )
        return [
            {**candidate, "added": candidate["model"] in existing}
            for candidate in candidates
        ]

    # ── credentials ──
    def _credentials(self) -> Dict[str, str]:
        try:
            text = self.credentials_file.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            raw = json.loads(text)
        except ValueError:
            self._quarantine(self.credentials_file)
            return {}
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    def _save_credentials(self, data: Dict[str, str]) -> None:
        tmp = self.credentials_file.with_suffix(".json.tmp")
        try:
            self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
            # Create the file with 0600 from the start on POSIX; Windows ignores
            # the mode, where the file lives under the user profile ACL instead.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.credentials_file)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensive
                pass
            # AC (#244): the reason must also reach the log, not only the HTTP
            # detail — a 4xx without a server-side trace is still hard to debug.
            logger.warning(
                "credentials write failed for %s: %s",
                self.credentials_file,
                exc,
                exc_info=True,
            )
            raise CredentialsError(
                f"cannot write credentials file {self.credentials_file}: "
                f"{exc.strerror or exc}. Grant this process write access to the "
                "user profile directory, start qianfan outside a restricted "
                "sandbox, or set the environment variable this profile uses "
                "instead of pasting the key."
            ) from exc

    def _revoke_provider_key(self, provider_id: str) -> None:
        credentials = self._credentials()
        if credentials.pop(provider_id, None) is not None:
            self._save_credentials(credentials)

    def set_provider_key(self, provider_id: str, key: str) -> None:
        if provider_id not in self.providers:
            raise KeyError(provider_id)
        data = self._credentials()
        if key.strip():
            data[provider_id] = key.strip()
        else:
            data.pop(provider_id, None)
        self._save_credentials(data)

    def provider_key_for(self, provider_id: str) -> str:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        return self._provider_key_state(provider_id)[0]

    def _provider_key_state(
        self, provider_id: str, *, pasted: Optional[Dict[str, str]] = None
    ) -> tuple[str, str]:
        """(key, source) where source is `pasted` / `env` / `none`."""
        provider = self.providers.get(provider_id)
        if provider is None:
            return "", "none"
        pasted = self._credentials() if pasted is None else pasted
        if pasted.get(provider_id):
            return pasted[provider_id], "pasted"
        env_key = os.environ.get(provider.api_key_env, "")
        return env_key, ("env" if env_key else "none")

    def key_for(self, model_id: str) -> str:
        """The key an entry would call with: its provider's pasted/env key."""
        entry = self.model_entries.get(model_id)
        if entry is None:
            return ""
        return self._provider_key_state(entry.provider_id)[0]

    def secret_values(self) -> set[str]:
        """Every live key, for redacting text before it is logged or stored."""
        values = {v for v in self._credentials().values() if v}
        for provider in self.providers.values():
            env_key = os.environ.get(provider.api_key_env, "")
            if env_key:
                values.add(env_key)
        return values


def resolve_discovery_key(api_key_env: str, key: str) -> str:
    """Draft discovery key precedence: form value, then the named env var."""
    form_key = (key or "").strip()
    if form_key:
        return form_key
    return os.environ.get((api_key_env or "LLM_API_KEY").strip(), "")


def discover_draft_models(
    *,
    protocol: str,
    base_url: str,
    api_key_env: str = "LLM_API_KEY",
    key: str = "",
    transport: Any = None,
) -> List[Dict[str, str]]:
    """Read-only discovery for an unsaved provider draft (#352).

    Validation happens before the network call; the actual probe is the one
    :func:`discover_provider_models` (same URL, timeout, size cap, redirect
    refusal and error text). Nothing is persisted and a key echoed by an
    upstream error is redacted before it leaves this function.
    """
    protocol = (protocol or PROTOCOL_OPENAI).strip() or PROTOCOL_OPENAI
    base_url = (base_url or "").strip()
    api_key_env = (api_key_env or "LLM_API_KEY").strip()
    ModelStore._validate_provider_fields(
        base_url=base_url,
        api_key_env=api_key_env,
        protocol=protocol,
    )
    if protocol != PROTOCOL_OPENAI:
        raise ProviderDiscoveryError(_DISCOVERY_UNSUPPORTED_PROTOCOL)
    provider = Provider(
        id="draft",
        name="draft",
        protocol=protocol,
        base_url=base_url,
        api_key_env=api_key_env,
    )
    resolved_key = resolve_discovery_key(api_key_env, key)
    try:
        return discover_provider_models(provider, resolved_key, transport=transport)
    except ProviderDiscoveryError as exc:
        message = str(exc)
        if resolved_key:
            message = message.replace(resolved_key, "[redacted]")
        if message == str(exc):
            raise
        raise ProviderDiscoveryError(message) from exc


def discover_provider_models(
    provider: Provider, key: str, *, transport: Any = None
) -> List[Dict[str, str]]:
    """Fetch `GET <base_url>/models` and return `[{model, name}, ...]`.

    One request to the provider's own address only: bounded time (httpx's
    per-operation timeout plus a body-phase wall-clock deadline — not a strict
    total, see the deadline comment below), bounded response size, redirects
    refused (a redirect would silently move the request off the configured
    host), and no persistence. Error text is built here and never interpolates
    the key. `transport` exists for tests (`httpx.MockTransport`) — production
    callers pass nothing.
    """
    if provider.protocol != PROTOCOL_OPENAI:
        raise ProviderDiscoveryError(_DISCOVERY_UNSUPPORTED_PROTOCOL)
    import httpx

    url = f"{provider.base_url.rstrip('/')}/models"
    # #351: discovery is not a conversation — identity only, no session header.
    headers = {"User-Agent": QIANFAN_USER_AGENT}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    timeout_message = f"请求超时（{DISCOVER_TIMEOUT_SEC:.0f}s）：检查 API 地址是否可达。"
    deadline = _monotonic() + DISCOVER_TIMEOUT_SEC
    try:
        with httpx.Client(
            timeout=DISCOVER_TIMEOUT_SEC,
            follow_redirects=False,
            transport=transport,
        ) as client:
            # Cheap guard before the stream starts: it only rules out a body
            # read that begins after the body-phase deadline; connect/write and
            # header waits remain bounded by httpx's per-operation timeout.
            if _monotonic() > deadline:
                raise ProviderDiscoveryError(timeout_message)
            with client.stream("GET", url, headers=headers) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise ProviderDiscoveryError(
                        f"API 地址返回重定向（HTTP {status}），请直接填写最终地址。"
                    )
                if status in (401, 403):
                    raise ProviderDiscoveryError(
                        f"认证失败（HTTP {status}）：检查该 Provider 的 Key 是否有效。"
                    )
                if status >= 400:
                    raise ProviderDiscoveryError(f"获取模型目录失败（HTTP {status}）。")
                chunks: List[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    # Body-phase wall clock: a slow-drip body must not hold the
                    # request thread forever. Connect/header phases are not
                    # covered here (httpx's per-operation timeout bounds them).
                    if _monotonic() > deadline:
                        raise ProviderDiscoveryError(timeout_message)
                    size += len(chunk)
                    if size > DISCOVER_MAX_BYTES:
                        raise ProviderDiscoveryError(
                            "模型目录响应超过大小上限（1 MB），已放弃读取。"
                        )
                    chunks.append(chunk)
                if _monotonic() > deadline:
                    raise ProviderDiscoveryError(timeout_message)
                body = b"".join(chunks)
    except ProviderDiscoveryError:
        raise
    except httpx.TimeoutException as exc:
        raise ProviderDiscoveryError(timeout_message) from exc
    except httpx.HTTPError as exc:
        raise ProviderDiscoveryError(
            f"无法连接 API 地址：{type(exc).__name__}"
        ) from exc
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ProviderDiscoveryError("模型目录返回的不是合法 JSON。") from exc
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ProviderDiscoveryError("模型目录响应格式不符：缺少 data 数组。")
    candidates: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        candidates.append(
            {
                "model": model_id,
                "name": str(item.get("name") or model_id).strip() or model_id,
            }
        )
    return candidates
