"""Model profiles and credentials (M1 / ADR 0010 decisions 7–8).

Profiles live in ``data/models.json`` (no keys); pasted keys live in
``~/.qianfan/credentials.json`` (outside the repo). The key for a profile is
the pasted one first, then the environment variable named by the profile.
Neither file is in ``Settings`` (the settings object is cached at startup).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import PROJECT_ROOT, Settings
from ..utils.logging import get_logger

logger = get_logger("models")

DEFAULT_MODEL_ID = "default"
_OPENAI_FALLBACK = "https://api.openai.com/v1"
_DEEPSEEK_FALLBACK = "https://api.deepseek.com/v1"


@dataclass
class ModelProfile:
    id: str
    name: str
    base_url: str
    model: str
    api_key_env: str = "LLM_API_KEY"
    preset: bool = False

    def public(self, key_set: bool) -> Dict[str, Any]:
        data = asdict(self)
        data["key_set"] = key_set
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
        preset_base = (
            _DEEPSEEK_FALLBACK
            if (self.settings.llm_provider or "").lower() == "deepseek"
            else _OPENAI_FALLBACK
        )
        profile = ModelProfile(
            id=DEFAULT_MODEL_ID,
            name="默认配置",
            base_url=self.settings.llm_base_url or preset_base,
            model=self.settings.llm_model,
            api_key_env="LLM_API_KEY",
        )
        self.models = {profile.id: profile}
        self.default_id = profile.id
        self._save()

    def _load(self) -> None:
        try:
            raw = json.loads(self.data_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
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
        return self.models.get(model_id or "") or self.models.get(self.default_id)

    def list_public(self) -> Dict[str, Any]:
        return {
            "default": self.default_id,
            "models": [p.public(self.key_set(p.id)) for p in self.models.values()],
        }

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
        for key in ("name", "base_url", "model", "api_key_env"):
            if key in fields and fields[key] is not None:
                setattr(profile, key, str(fields[key]).strip())
        if not profile.base_url or not profile.model:
            raise ValueError("base_url and model are required")
        self._save()
        return profile

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
            raw = json.loads(self.credentials_file.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_credentials(self, data: Dict[str, str]) -> None:
        self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.credentials_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - Windows ignores chmod
            pass
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

    def key_for(self, model_id: str) -> str:
        profile = self.models.get(model_id)
        if profile is None:
            return ""
        pasted = self._credentials().get(model_id, "")
        if pasted:
            return pasted
        return os.environ.get(profile.api_key_env, "")

    def key_set(self, model_id: str) -> bool:
        return bool(self.key_for(model_id))

    @staticmethod
    def repo_root() -> Path:
        return PROJECT_ROOT
