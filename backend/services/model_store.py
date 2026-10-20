"""Model profiles and credentials (M1 / ADR 0010 decisions 7–8).

Profiles live in ``data/models.json`` (no keys); pasted keys live in
``~/.qianfan/credentials.json`` (outside the repo). The key for a profile is
the pasted one first, then the environment variable named by the profile.
Neither file is in ``Settings`` (the settings object is cached at startup).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import Settings
from ..utils.logging import get_logger

logger = get_logger("models")

DEFAULT_MODEL_ID = "default"
_DEEPSEEK_FALLBACK = "https://api.deepseek.com/v1"


@dataclass
class ModelProfile:
    id: str
    name: str
    base_url: str
    model: str
    api_key_env: str = "LLM_API_KEY"
    preset: bool = False

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
        self.models: Dict[str, ModelProfile] = {}
        self.default_id = DEFAULT_MODEL_ID
        self._load()

    # ── seeding / persistence ──
    def _seed(self) -> None:
        # DeepSeek is the only built-in preset (M1): an explicit base_url is a
        # custom host, anything else falls back to the DeepSeek endpoint.
        base_url = self.settings.llm_base_url or _DEEPSEEK_FALLBACK
        profile = ModelProfile(
            id=DEFAULT_MODEL_ID,
            name="默认配置",
            base_url=base_url,
            model=self.settings.llm_model,
            api_key_env="LLM_API_KEY",
            preset=not self.settings.llm_base_url
            and (self.settings.llm_provider or "deepseek").lower() == "deepseek",
        )
        self.models = {profile.id: profile}
        self.default_id = profile.id
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
        profiles: Dict[str, ModelProfile] = {}
        for item in raw.get("models", []) if isinstance(raw, dict) else []:
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
            if profile.id and profile.base_url and profile.model:
                profiles[profile.id] = profile
        if not profiles:
            self._seed()
            return
        self.models = profiles
        default = str(raw.get("default") or "")
        self.default_id = default if default in profiles else next(iter(profiles))

    def _save(self) -> None:
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "default": self.default_id,
            "models": [asdict(profile) for profile in self.models.values()],
        }
        tmp = self.data_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.data_file)

    # ── profiles ──
    def get(self, model_id: str) -> Optional[ModelProfile]:
        return self.models.get(model_id)

    def resolve(self, model_id: Optional[str]) -> Optional[ModelProfile]:
        """An explicit id must exist; only an empty id means "the default".

        Falling back to the default for a deleted id would silently send the
        task's content to another provider, so that case stays ``None`` and the
        caller fails loudly.
        """
        if model_id:
            return self.models.get(model_id)
        return self.models.get(self.default_id)

    def public_profile(self, model_id: str) -> Dict[str, Any]:
        profile = self.models.get(model_id)
        if profile is None:
            raise KeyError(model_id)
        key, source = self._key_state(model_id)
        return profile.public(bool(key), source)

    def list_public(self) -> Dict[str, Any]:
        pasted = self._credentials()
        entries = []
        for profile in self.models.values():
            key, source = self._key_state(profile.id, pasted=pasted)
            entries.append(profile.public(bool(key), source))
        return {"default": self.default_id, "models": entries}

    def add(
        self, *, name: str, base_url: str, model: str, api_key_env: str = "LLM_API_KEY"
    ) -> ModelProfile:
        profile = ModelProfile(
            id=uuid.uuid4().hex[:12],
            name=name.strip() or "自定义",
            base_url=base_url.strip(),
            model=model.strip(),
            api_key_env=(api_key_env or "LLM_API_KEY").strip(),
        )
        if not profile.base_url or not profile.model:
            raise ValueError("base_url and model are required")
        self.models[profile.id] = profile
        self._save()
        return profile

    def update(self, model_id: str, **fields: Any) -> ModelProfile:
        profile = self.models.get(model_id)
        if profile is None:
            raise KeyError(model_id)
        cleaned = {
            key: str(fields[key]).strip()
            for key in ("name", "base_url", "model", "api_key_env")
            if fields.get(key) is not None
        }
        candidate = ModelProfile(
            id=profile.id,
            name=cleaned.get("name", profile.name),
            base_url=cleaned.get("base_url", profile.base_url),
            model=cleaned.get("model", profile.model),
            api_key_env=cleaned.get("api_key_env", profile.api_key_env),
            preset=profile.preset,
        )
        if not candidate.base_url or not candidate.model:
            raise ValueError("base_url and model are required")
        self.models[model_id] = candidate
        self._save()
        return candidate

    def delete(self, model_id: str) -> None:
        if model_id not in self.models:
            raise KeyError(model_id)
        if len(self.models) == 1:
            raise ValueError("cannot delete the last model profile")
        del self.models[model_id]
        if self.default_id == model_id:
            self.default_id = next(iter(self.models))
        self._save()

    def set_default(self, model_id: str) -> None:
        if model_id not in self.models:
            raise KeyError(model_id)
        self.default_id = model_id
        self._save()

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
        self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.credentials_file.with_suffix(".json.tmp")
        # Create the file with 0600 from the start on POSIX; Windows ignores the
        # mode, where the file lives under the user profile ACL instead.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.credentials_file)

    def set_key(self, model_id: str, key: str) -> None:
        if model_id not in self.models:
            raise KeyError(model_id)
        data = self._credentials()
        if key.strip():
            data[model_id] = key.strip()
        else:
            data.pop(model_id, None)
        self._save_credentials(data)

    def _key_state(
        self, model_id: str, *, pasted: Optional[Dict[str, str]] = None
    ) -> tuple[str, str]:
        """(key, source) where source is ``pasted`` / ``env`` / ``none``."""
        profile = self.models.get(model_id)
        if profile is None:
            return "", "none"
        pasted = self._credentials() if pasted is None else pasted
        if pasted.get(model_id):
            return pasted[model_id], "pasted"
        env_key = os.environ.get(profile.api_key_env, "")
        return env_key, ("env" if env_key else "none")

    def key_for(self, model_id: str) -> str:
        return self._key_state(model_id)[0]

    def key_source(self, model_id: str) -> str:
        return self._key_state(model_id)[1]

    def key_set(self, model_id: str) -> bool:
        return bool(self.key_for(model_id))

    def secret_values(self) -> set[str]:
        """Every live key, for redacting text before it is logged or stored."""
        values = {v for v in self._credentials().values() if v}
        for profile in self.models.values():
            env_key = os.environ.get(profile.api_key_env, "")
            if env_key:
                values.add(env_key)
        return values
