"""Persisted capability-package configuration (#67 P4-2, ADR 0012).

Single read/write seam for data/plugins.json:

    {"preset": "minimal|research|full", "overrides": {"<package-id>": true|false}}

The file is global (service level), read at startup and after that only by the
settings API. Missing file = default full + no overrides. Any unreadable /
malformed content is a **startup error**: silently falling back to full would
turn a typo into "half the tools disappeared and nobody knows why".

Effective configuration priority (D-54):

    CLI --preset (one-shot, never written) > data/plugins.json >
    Settings.plugins_preset (env PLUGINS_PRESET, P4-1 compatibility) > full

Overrides always ride on top of the chosen preset (true forces a package on,
false forces it off). Changing the file requires a restart to take effect; the
API reports restart_required by comparing the saved file with the running
configuration.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..config import Settings
from ..core.capability_packages import (
    DEFAULT_PRESET,
    PACKAGE_BY_ID,
    PACKAGE_IDS,
    PACKAGES,
    PRESETS,
    enabled_tool_names,
    preset_names,
    resolve_packages,
)

#: Env var through which the launcher passes a one-shot CLI preset to the web
#: child process (the child resolves it as a CLI override, beating the file).
PLUGIN_PRESET_OVERRIDE_ENV = "QIANFAN_PLUGIN_PRESET_OVERRIDE"

#: File name under Settings.data_path.
PLUGINS_FILE_NAME = "plugins.json"


class PluginsConfigError(ValueError):
    """Unreadable / invalid plugins configuration (readable startup error)."""


@dataclass(frozen=True)
class SavedPluginsConfig:
    """What data/plugins.json currently holds."""

    preset: str
    overrides: Dict[str, bool]
    #: False when the file does not exist (default full + no overrides).
    exists: bool


@dataclass(frozen=True)
class EffectivePluginsConfig:
    """The configuration this process actually assembled at startup."""

    preset: str
    overrides: Dict[str, bool]
    packages: frozenset[str]
    tools: frozenset[str]
    #: Where the preset came from: cli | file | env | default.
    source: str
    saved: SavedPluginsConfig


def plugins_path(settings: Settings) -> Path:
    """<data_path>/plugins.json (the repository-external runtime state)."""
    return settings.data_path / PLUGINS_FILE_NAME


def _fix_hint(settings: Settings) -> str:
    path = plugins_path(settings)
    return (
        f"修法：删除 {path} 回到默认 full，或在设置页/PUT /api/plugins 重新保存；"
        "然后重启后台生效。"
    )


def _read_raw(settings: Settings) -> Optional[dict]:
    """Read + shallow-validate the JSON object; None when the file is absent."""
    path = plugins_path(settings)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginsConfigError(
            f"无法读取能力包配置 {path}：{exc}. {_fix_hint(settings)}"
        ) from None
    try:
        raw = json.loads(text)
    except ValueError:
        raise PluginsConfigError(
            f"能力包配置 {path} 不是合法 JSON。{_fix_hint(settings)}"
        ) from None
    if not isinstance(raw, dict):
        raise PluginsConfigError(
            f"能力包配置 {path} 必须是 JSON 对象。{_fix_hint(settings)}"
        )
    return raw


def _validate_overrides(settings: Settings, value: object) -> Dict[str, bool]:
    if not isinstance(value, dict):
        raise PluginsConfigError(
            f"能力包配置 {plugins_path(settings)} 的 overrides 必须是 JSON 对象。"
            f"{_fix_hint(settings)}"
        )
    overrides: Dict[str, bool] = {}
    for package_id, flag in value.items():
        if package_id not in PACKAGE_BY_ID:
            raise PluginsConfigError(
                f"能力包配置 {plugins_path(settings)} 含未知能力包 {package_id!r}；"
                f"可选值：{sorted(PACKAGE_IDS)}。{_fix_hint(settings)}"
            )
        if not isinstance(flag, bool):
            raise PluginsConfigError(
                f"能力包配置 {plugins_path(settings)} 的 overrides[{package_id!r}] "
                f"必须是 true / false，实际是 {type(flag).__name__}。"
                f"{_fix_hint(settings)}"
            )
        overrides[package_id] = flag
    return overrides


def _validate_preset_and_overrides(
    settings: Settings, preset: object, overrides: object
) -> tuple[str, Dict[str, bool]]:
    if not isinstance(preset, str):
        raise PluginsConfigError(
            f"能力包配置 {plugins_path(settings)} 的 preset 必须是字符串。"
            f"{_fix_hint(settings)}"
        )
    normalized = preset.strip()
    clean_overrides = _validate_overrides(settings, overrides)
    try:
        resolve_packages(normalized, clean_overrides)
    except ValueError as exc:
        raise PluginsConfigError(
            f"能力包配置 {plugins_path(settings)} 无效：{exc} {_fix_hint(settings)}"
        ) from None
    return normalized, clean_overrides


def load_plugins_config(settings: Settings) -> SavedPluginsConfig:
    """Load data/plugins.json; missing file = default full + no overrides.

    Invalid content raises PluginsConfigError with the file path and a fix hint
    (fail-fast, never a silent full fallback).
    """
    raw = _read_raw(settings)
    if raw is None:
        return SavedPluginsConfig(preset=DEFAULT_PRESET, overrides={}, exists=False)
    if "preset" not in raw:
        raise PluginsConfigError(
            f"能力包配置 {plugins_path(settings)} 缺少 preset 字段。"
            f"{_fix_hint(settings)}"
        )
    preset, overrides = _validate_preset_and_overrides(
        settings, raw.get("preset"), raw.get("overrides", {})
    )
    return SavedPluginsConfig(preset=preset, overrides=overrides, exists=True)


def save_plugins_config(
    settings: Settings, preset: str, overrides: Optional[Dict[str, bool]] = None
) -> SavedPluginsConfig:
    """Validate + atomically write data/plugins.json.

    Validation happens before any write, so an invalid request never touches the
    file. The write is tmp + os.replace (same pattern as tasks.json /
    models.json), so a crash cannot leave a torn file.
    """
    clean_overrides = dict(overrides or {})
    preset, clean_overrides = _validate_preset_and_overrides(
        settings, preset, clean_overrides
    )
    path = plugins_path(settings)
    payload = {"preset": preset, "overrides": clean_overrides}
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise PluginsConfigError(
            f"无法写入能力包配置 {path}：{exc}. 修法：确认 data/ 可写后重试；"
            "正在运行的组合不受本次写入影响，重启后台才生效。"
        ) from None
    return SavedPluginsConfig(preset=preset, overrides=clean_overrides, exists=True)


def resolve_effective_plugins(
    settings: Settings, *, preset_override: Optional[str] = None
) -> EffectivePluginsConfig:
    """Resolve the effective configuration (CLI > file > env > default)."""
    saved = load_plugins_config(settings)
    override = (
        preset_override
        if preset_override is not None
        else os.environ.get(PLUGIN_PRESET_OVERRIDE_ENV)
    )
    if override is not None:
        preset = str(override)
        source = "cli"
    elif saved.exists:
        preset = saved.preset
        source = "file"
    elif str(settings.plugins_preset).strip():
        preset = settings.plugins_preset
        source = "env"
    else:
        preset = DEFAULT_PRESET
        source = "default"
    overrides = dict(saved.overrides) if saved.exists else {}
    try:
        packages = resolve_packages(preset, overrides)
    except ValueError as exc:
        if source == "cli":
            context = "CLI --preset"
        elif source == "file":
            context = f"配置文件 {plugins_path(settings)}"
        else:
            context = "PLUGINS_PRESET"
        raise PluginsConfigError(
            f"能力包配置无效（来源：{context}）：{exc} {_fix_hint(settings)}"
        ) from None
    return EffectivePluginsConfig(
        preset=preset.strip(),
        overrides=overrides,
        packages=packages,
        tools=enabled_tool_names(packages),
        source=source,
        saved=saved,
    )


def restart_required(active: EffectivePluginsConfig, saved: SavedPluginsConfig) -> bool:
    """True when the saved file differs from what this process assembled."""
    return (active.preset, dict(active.overrides)) != (
        saved.preset,
        dict(saved.overrides),
    )


#: API catalogues (manifest order; kept here so the route stays presentation-only).
def presets_catalog() -> List[dict]:
    return [
        {"id": preset_id, "packages": list(PRESETS[preset_id])}
        for preset_id in preset_names()
    ]


def packages_catalog() -> List[dict]:
    return [
        {
            "id": package.id,
            "tools": list(package.tools),
            "connects_mcp": package.connects_mcp,
            "affects_subagent_face": package.affects_subagent_face,
        }
        for package in PACKAGES
    ]
