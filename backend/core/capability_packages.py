"""Static capability-package manifest (#67 / spec #287 D-53…D-56).

Single source of truth for what a preset turns on. Packages are **this repo's
own code**, declared statically — there is no third-party plugin loading here
and none is promised (see ADR 0012).

Terminology (D-55):

* a **capability package** declares a tool-name set, the injection fragments it
  contributes, whether it connects MCP servers, and whether it can affect the
  sub-agent face;
* a **preset** is a named global combination of packages (`minimal` /
  `research` / `full`).

"Off" is a real close (D-55): a disabled package is not instantiated, does not
enter the tool schema, cannot be dispatched, contributes no injection fragment,
and does not connect MCP. "Modules are not imported" is explicitly **not**
promised — only instantiation / wiring is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Mapping, Optional, Tuple

#: Injection fragment ids a package may contribute to the system prompt.
INJECTION_SKILLS = "skills"


@dataclass(frozen=True)
class CapabilityPackage:
    """One statically declared capability package."""

    id: str
    #: Tool names owned by this package (must exist in the tool registry).
    tools: Tuple[str, ...] = ()
    #: Injection fragment ids contributed when the package is enabled.
    injection: Tuple[str, ...] = ()
    #: True when enabling the package connects MCP servers (dynamic tools).
    connects_mcp: bool = False
    #: True when the package's tools can appear on a sub-agent face (the actual
    #: face is still the fixed tier intersected with what the parent mounted).
    affects_subagent_face: bool = False


PACKAGES: Tuple[CapabilityPackage, ...] = (
    CapabilityPackage(
        id="files",
        tools=("read", "write", "edit", "ls", "glob", "grep"),
        affects_subagent_face=True,
    ),
    CapabilityPackage(id="bash", tools=("bash",)),
    CapabilityPackage(
        id="skills",
        tools=("load_skill",),
        injection=(INJECTION_SKILLS,),
        affects_subagent_face=True,
    ),
    CapabilityPackage(
        id="web-search",
        tools=("web_search",),
        affects_subagent_face=True,
    ),
    CapabilityPackage(id="subagent", tools=("spawn_subagent",)),
    CapabilityPackage(id="mcp", connects_mcp=True),
)

PACKAGE_BY_ID: Dict[str, CapabilityPackage] = {package.id: package for package in PACKAGES}
PACKAGE_IDS: FrozenSet[str] = frozenset(PACKAGE_BY_ID)

#: All packages are on by default, matching the pre-#67 behaviour exactly.
PRESETS: Dict[str, Tuple[str, ...]] = {
    "minimal": ("files", "bash"),
    "research": ("files", "bash", "skills", "web-search", "subagent", "mcp"),
    "full": tuple(package.id for package in PACKAGES),
}

DEFAULT_PRESET = "full"


def preset_names() -> Tuple[str, ...]:
    """Known preset ids in declaration order (for errors / docs)."""
    return tuple(PRESETS)


def resolve_packages(
    preset: str, overrides: Optional[Mapping[str, bool]] = None
) -> FrozenSet[str]:
    """Resolve a preset (+ optional per-package overrides) to enabled package ids.

    Unknown presets / package ids raise a readable `ValueError`: this is the
    startup fail-fast required by D-55/D-56. `overrides` is already the shape
    P4-2's `data/plugins.json` will feed in; P4-1 exposes no file/API path.
    """
    name = str(preset).strip() if preset is not None else ""
    if not name:
        raise ValueError(
            f"empty plugins preset; expected one of {sorted(PRESETS)}. Set "
            "plugins_preset (env PLUGINS_PRESET, see .env.example) to a known "
            "preset; data/plugins.json + the settings page arrive in P4-2."
        )
    if name not in PRESETS:
        raise ValueError(
            f"unknown plugins preset {name!r}; expected one of "
            f"{sorted(PRESETS)}. Set plugins_preset (env PLUGINS_PRESET, see "
            ".env.example) to a known preset; data/plugins.json + the settings "
            "page arrive in the next slice (P4-2)."
        )
    enabled = set(PRESETS[name])
    for package_id, flag in dict(overrides or {}).items():
        if package_id not in PACKAGE_BY_ID:
            raise ValueError(
                f"unknown capability package {package_id!r}; expected one of "
                f"{sorted(PACKAGE_IDS)}."
            )
        if flag:
            enabled.add(package_id)
        else:
            enabled.discard(package_id)
    return frozenset(enabled)


def validate_packages(packages: FrozenSet[str]) -> FrozenSet[str]:
    """Validate an explicit package set (the embedding/test seam)."""
    unknown = sorted(set(packages) - PACKAGE_IDS)
    if unknown:
        raise ValueError(
            f"unknown capability package(s) {unknown}; expected one of "
            f"{sorted(PACKAGE_IDS)}."
        )
    return frozenset(packages)


def enabled_tool_names(packages: FrozenSet[str]) -> FrozenSet[str]:
    """The exact tool names the enabled packages mount."""
    names: set[str] = set()
    for package_id in packages:
        names.update(PACKAGE_BY_ID[package_id].tools)
    return frozenset(names)


def package_for_tool(tool_name: str) -> Optional[str]:
    """The package that owns `tool_name`, or `None` for dynamic/test tools."""
    for package in PACKAGES:
        if tool_name in package.tools:
            return package.id
    return None


def injection_fragments(packages: FrozenSet[str]) -> FrozenSet[str]:
    """Injection fragment ids contributed by the enabled packages."""
    fragments: set[str] = set()
    for package_id in packages:
        fragments.update(PACKAGE_BY_ID[package_id].injection)
    return frozenset(fragments)


def mcp_package_enabled(packages: FrozenSet[str]) -> bool:
    """True when an enabled package asks for MCP connections."""
    return any(PACKAGE_BY_ID[package_id].connects_mcp for package_id in packages)


def subagent_package_enabled(packages: FrozenSet[str]) -> bool:
    """True when the sub-agent tool belongs to the enabled face."""
    return "subagent" in packages
