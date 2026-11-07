
"""Model providers, model catalog and credentials (M1 / ADR 0010, #243 P2-2).

`data/models.json` is at schema version 2: a list of **providers**
(id / name / protocol / base_url / api_key_env / preset) and a list of **model
entries** (id / provider_id / model / name / source); `default` names a model
entry id. A v1 file (a flat `models` list) is migrated in place on first load:
every profile becomes a same-id provider plus a same-id model entry, so
`Task.model`, `default` and the credentials keys do not change.

Pasted keys live in `~/.qianfan/credentials.json` (outside the repo) keyed by
**provider id**; the environment variable named by the provider is the fallback.
Neither file is in `Settings` (the settings object is cached at startup).
No API response ever returns a key: only `key_set` / `key_source`.

The legacy `/api/models` surface keeps working as a flat projection of one
model entry + its provider (`ModelProfile`), so the pre-P2-3 frontend does not
change in this PR.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from ..config import Settings
from ..utils.logging import get_logger

logger = get_logger("models")

DEFAULT_MODEL_ID = "default"
_DEEPSEEK_FALLBACK = "https://api.deepseek.com/v1"

#: Current on-disk schema version (D-33). A v1 file omits this key.
SCHEMA_VERSION = 2

PROTOCOL_OPENAI = "openai-compatible"
PROTOCOL_ANTHROPIC = "anthropic"
SUPPORTED_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

#: Provider ids are used to derive the credentials key, so they are constrained
#: for new providers (D-34). Migrated v1 ids are opaque hex strings and stay
#: readable as-is; only creation validates the pattern.
PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Model discovery (D-34): one GET, bounded time and response size; the body is
#: never stored and error text never contains the key.
DISCOVER_TIMEOUT_SEC = 10.0
DISCOVER_MAX_BYTES = 1_000_000


class CredentialsError(RuntimeError):
    """The credential file outside the repo could not be written (#244).

    Written as a domain error so the API can answer 4xx with the path and a
    next step, instead of leaking an `OSError` as a 500 with no reason.
    """


class ProviderDiscoveryError(RuntimeError):
    """A readable "why the model catalog could not be fetched" (D-34)."""


@dataclass
class Provider:
    """One API host: several model entries share its address, key and protocol."""

    id: str
    name: str
    protocol: str = PROTOCOL_OPENAI
    base_url: str = ""
    api_key_env: str = "LLM_API_KEY"
    preset: bool = False


@dataclass
class ModelEntry:
    """One selectable model under a provider (`Task.model` stores its id)."""

    id: str
    provider_id: str
    model: str
    name: str
    source: str = "manual"  # manual | migrated


@dataclass
class ModelProfile:
    """v1-compatible flat view of one model entry + its provider.

    Legacy `/api/models` consumers receive this shape (old fields plus
    `provider_id` / `protocol`), so the pre-P2-3 frontend keeps working.
    """

    id: str
    name: str
    base_url: str
    model: str
    api_key_env: str = "LLM_API_KEY"
    preset: bool = False
    provider_id: str = ""
    protocol: str = PROTOCOL_OPENAI

    def public(self, key_set: bool, key_source: str = "none") -> Dict[str, Any]:
        data = asdict(self)
        data["key_set"] = key_set
        data["key_source"] = key_source
        return data


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
        elif version == 0:
            # v1 had no version stamp at all: a `models` list of flat profiles.
            self._migrate_v1(raw)
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
                )
            except (KeyError, TypeError):
                continue
            if provider.id and provider.base_url:
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

    def _migrate_v1(self, raw: Dict[str, Any]) -> None:
        """v1 flat profiles -> same-id provider + same-id model entry (D-33).

        `Task.model` / `default` / credentials keys keep working because the
        ids are reused verbatim. The original file is copied to
        `models.json.bak-v1` once (an existing backup is never overwritten);
        the second load sees `schema_version: 2` and writes nothing.
        """
        providers: Dict[str, Provider] = {}
        entries: Dict[str, ModelEntry] = {}
        for item in raw.get("models", []):
            if not isinstance(item, dict):
                continue
            try:
                profile = ModelProfile(
                    id=str(item["id"]),
                    name=str(item.get("name") or item["id"]),
                    base_url=str(item.get("base_url") or ""),
                    model=str(item.get("model") or ""),
                    api_key_env=str(item.get("api_key_env") or "LLM_API_KEY"),
                    preset=bool(item.get("preset")),
                )
            except (KeyError, TypeError):
                continue
            if not (profile.id and profile.base_url and profile.model):
                continue
            providers[profile.id] = Provider(
                id=profile.id,
                name=profile.name,
                protocol=PROTOCOL_OPENAI,
                base_url=profile.base_url,
                api_key_env=profile.api_key_env,
                preset=profile.preset,
            )
            entries[profile.id] = ModelEntry(
                id=profile.id,
                provider_id=profile.id,
                model=profile.model,
                name=profile.name,
                source="migrated",
            )
        if not entries:
            self._quarantine(self.data_file)
            self._seed()
            return
        self.providers = providers
        self.model_entries = entries
        default = str(raw.get("default") or "")
        self.default_id = default if default in entries else next(iter(entries))
        try:
            original = self.data_file.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - defensive (we just read it)
            original = ""
        self._backup_v1(original)
        self._save()
        logger.info(
            "migrated %d model profile(s) to schema v%d (backup: %s)",
            len(entries),
            SCHEMA_VERSION,
            self.data_file.with_name(f"{self.data_file.name}.bak-v1").name,
        )

    def _backup_v1(self, text: str) -> None:
        backup = self.data_file.with_name(f"{self.data_file.name}.bak-v1")
        if backup.exists():
            # Only the first migration writes a backup: a later restore + reload
            # must not clobber the snapshot taken before the first rewrite.
            return
        try:
            backup.write_text(text, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning(
                "could not back up %s to %s before migrating: %s",
                self.data_file.name,
                backup.name,
                exc,
            )

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

    # ── projections (legacy /api/models) ──
    def _profile(self, entry: ModelEntry) -> ModelProfile:
        provider = self.providers[entry.provider_id]
        return ModelProfile(
            id=entry.id,
            name=entry.name,
            base_url=provider.base_url,
            model=entry.model,
            api_key_env=provider.api_key_env,
            preset=provider.preset,
            provider_id=provider.id,
            protocol=provider.protocol,
        )

    # ── model entries (legacy accessors) ──
    def get(self, model_id: str) -> Optional[ModelProfile]:
        entry = self.model_entries.get(model_id)
        return self._profile(entry) if entry is not None else None

    def get_model(self, model_id: str) -> Optional[ModelEntry]:
        return self.model_entries.get(model_id)

    def resolve(self, model_id: Optional[str]) -> Optional[ModelProfile]:
        """An explicit id must exist; only an empty id means "the default".

        Falling back to the default for a deleted id would silently send the
        task's content to another provider, so that case stays `None` and the
        caller fails loudly.
        """
        if model_id:
            return self.get(model_id)
        return self.get(self.default_id)

    def public_profile(self, model_id: str) -> Dict[str, Any]:
        entry = self.model_entries.get(model_id)
        if entry is None:
            raise KeyError(model_id)
        key, source = self._key_state(model_id)
        return self._profile(entry).public(bool(key), source)

    def model_public(self, model_id: str) -> Dict[str, Any]:
        """One model entry as stored (id / provider_id / model / name / source)."""
        entry = self.model_entries.get(model_id)
        if entry is None:
            raise KeyError(model_id)
        return asdict(entry)

    def list_public(self) -> Dict[str, Any]:
        pasted = self._credentials()
        entries = []
        for entry in self.model_entries.values():
            key, source = self._key_state(entry.id, pasted=pasted)
            entries.append(self._profile(entry).public(bool(key), source))
        return {"default": self.default_id, "models": entries}

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
    def _slugify_provider_id(name: str, taken: Iterable[str]) -> str:
        """A valid provider id derived from a display name (legacy POST path)."""
        slug = re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower()).strip("-_")
        if not slug:
            slug = "provider"
        elif not slug[0].isalpha():
            slug = f"p-{slug}"
        slug = slug[:48].rstrip("-_") or "provider"
        taken_set = set(taken)
        candidate = slug
        suffix = 2
        while candidate in taken_set:
            candidate = f"{slug}-{suffix}"
            suffix += 1
        return candidate

    def _new_entry_id(self) -> str:
        candidate = uuid.uuid4().hex[:12]
        while candidate in self.model_entries:
            candidate = uuid.uuid4().hex[:12]
        return candidate

    # ── legacy profile CRUD (POST/PUT/DELETE /api/models) ──
    def add(
        self, *, name: str, base_url: str, model: str, api_key_env: str = "LLM_API_KEY"
    ) -> ModelProfile:
        """Create a v1-shaped profile as a 1:1 provider + model entry.

        The provider id is derived from the display name (valid per D-34) and
        reused as the model entry id, which keeps legacy PUT/DELETE/key paths
        keyed by the same id.
        """
        display = name.strip() or "自定义"
        base_url = base_url.strip()
        model = model.strip()
        api_key_env = (api_key_env or "LLM_API_KEY").strip()
        self._validate_provider_fields(
            base_url=base_url, api_key_env=api_key_env, protocol=PROTOCOL_OPENAI
        )
        provider_id = self._slugify_provider_id(
            display, set(self.providers) | set(self.model_entries)
        )
        provider = Provider(
            id=provider_id,
            name=display,
            protocol=PROTOCOL_OPENAI,
            base_url=base_url,
            api_key_env=api_key_env,
            preset=False,
        )
        entry = ModelEntry(
            id=provider_id,
            provider_id=provider_id,
            model=model,
            name=display,
            source="manual",
        )
        self.providers[provider.id] = provider
        self.model_entries[entry.id] = entry
        self._save()
        return self._profile(entry)

    def update(self, model_id: str, **fields: Any) -> ModelProfile:
        entry = self.model_entries.get(model_id)
        if entry is None:
            raise KeyError(model_id)
        provider = self.providers[entry.provider_id]
        cleaned = {
            key: str(fields[key]).strip()
            for key in ("name", "base_url", "model", "api_key_env")
            if fields.get(key) is not None
        }
        base_url = cleaned.get("base_url", provider.base_url)
        candidate_provider = replace(
            provider,
            name=cleaned.get("name", provider.name),
            base_url=base_url,
            api_key_env=cleaned.get("api_key_env", provider.api_key_env),
            # Editing the address turns a built-in preset into a custom provider.
            preset=provider.preset if base_url == provider.base_url else False,
        )
        self._validate_provider_fields(
            base_url=candidate_provider.base_url,
            api_key_env=candidate_provider.api_key_env,
            protocol=candidate_provider.protocol,
        )
        candidate_entry = replace(
            entry,
            name=cleaned.get("name", entry.name),
            model=cleaned.get("model", entry.model),
        )
        self.providers[provider.id] = candidate_provider
        self.model_entries[entry.id] = candidate_entry
        self._save()
        return self._profile(candidate_entry)

    def delete(self, model_id: str) -> None:
        entry = self.model_entries.get(model_id)
        if entry is None:
            raise KeyError(model_id)
        if len(self.model_entries) == 1:
            raise ValueError("cannot delete the last model profile")
        provider = self.providers[entry.provider_id]
        siblings = [
            other
            for other in self.model_entries.values()
            if other.provider_id == provider.id and other.id != entry.id
        ]
        if not siblings:
            # Revoke the pasted key BEFORE dropping the provider: if the
            # credential file is not writable, raising here keeps the provider
            # (and its key reference) intact instead of leaving an orphaned
            # secret on disk.
            self._revoke_provider_key(provider.id)
            del self.providers[provider.id]
        del self.model_entries[entry.id]
        if self.default_id == entry.id:
            self.default_id = next(iter(self.model_entries))
        self._save()

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
        provider = Provider(
            id=provider_id,
            name=(name.strip() or provider_id),
            protocol=protocol,
            base_url=base_url,
            api_key_env=api_key_env,
            preset=False,
        )
        self.providers[provider.id] = provider
        self._save()
        # The key is written after the provider is durable: a credentials write
        # failure must not leave an orphaned secret keyed by an id that was
        # never accepted.
        if key.strip():
            self.set_provider_key(provider.id, key)
        return provider

    def update_provider(
        self,
        provider_id: str,
        *,
        name: Optional[str] = None,
        base_url: Optional[str] = None,
        protocol: Optional[str] = None,
        api_key_env: Optional[str] = None,
        key: Optional[str] = None,
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
        )
        self._validate_provider_fields(
            base_url=candidate.base_url,
            api_key_env=candidate.api_key_env,
            protocol=candidate.protocol,
        )
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
        self, provider_id: str, *, model: str, name: Optional[str] = None
    ) -> ModelEntry:
        if provider_id not in self.providers:
            raise KeyError(provider_id)
        model = model.strip()
        if not model:
            raise ValueError("model is required")
        if any(
            entry.provider_id == provider_id and entry.model == model
            for entry in self.model_entries.values()
        ):
            raise ValueError(f"model {model!r} already exists for provider {provider_id!r}")
        entry = ModelEntry(
            id=self._new_entry_id(),
            provider_id=provider_id,
            model=model,
            name=(name or "").strip() or model,
            source="manual",
        )
        self.model_entries[entry.id] = entry
        self._save()
        return entry

    def delete_model(self, provider_id: str, model_id: str) -> None:
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
        model = model.strip()
        if not model:
            raise ValueError("model is required")
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

    def set_key(self, model_id: str, key: str) -> None:
        """Legacy write: the key belongs to the entry's provider (same id after
        v1 migration, shared by every entry under a v2 provider)."""
        entry = self.model_entries.get(model_id)
        if entry is None:
            raise KeyError(model_id)
        self.set_provider_key(entry.provider_id, key)

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

    def _key_state(
        self, model_id: str, *, pasted: Optional[Dict[str, str]] = None
    ) -> tuple[str, str]:
        entry = self.model_entries.get(model_id)
        if entry is None:
            return "", "none"
        return self._provider_key_state(entry.provider_id, pasted=pasted)

    def key_for(self, model_id: str) -> str:
        return self._key_state(model_id)[0]

    def key_source(self, model_id: str) -> str:
        return self._key_state(model_id)[1]

    def key_set(self, model_id: str) -> bool:
        return bool(self.key_for(model_id))

    def secret_values(self) -> set[str]:
        """Every live key, for redacting text before it is logged or stored."""
        values = {v for v in self._credentials().values() if v}
        for provider in self.providers.values():
            env_key = os.environ.get(provider.api_key_env, "")
            if env_key:
                values.add(env_key)
        return values


def discover_provider_models(
    provider: Provider, key: str, *, transport: Any = None
) -> List[Dict[str, str]]:
    """Fetch `GET <base_url>/models` and return `[{model, name}, ...]`.

    One request to the provider's own address only: bounded wall clock, bounded
    response size, redirects refused (a redirect would silently move the
    request off the configured host), and no persistence. Error text is built
    here and never interpolates the key. `transport` exists for tests
    (`httpx.MockTransport`) — production callers pass nothing.
    """
    if provider.protocol != PROTOCOL_OPENAI:
        raise ProviderDiscoveryError(
            "协议暂不支持发现：当前只有 OpenAI 兼容协议支持自动获取模型目录，"
            "请手动添加模型。"
        )
    import httpx

    url = f"{provider.base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        with httpx.Client(
            timeout=DISCOVER_TIMEOUT_SEC,
            follow_redirects=False,
            transport=transport,
        ) as client:
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
                    size += len(chunk)
                    if size > DISCOVER_MAX_BYTES:
                        raise ProviderDiscoveryError(
                            "模型目录响应超过大小上限（1 MB），已放弃读取。"
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
    except ProviderDiscoveryError:
        raise
    except httpx.TimeoutException as exc:
        raise ProviderDiscoveryError(
            f"请求超时（{DISCOVER_TIMEOUT_SEC:.0f}s）：检查 API 地址是否可达。"
        ) from exc
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
