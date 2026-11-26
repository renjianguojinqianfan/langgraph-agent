"""#67 P4-2: data/plugins.json store, effective resolution and entry fail-fast."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import pytest

from backend import cli, repl, server_lock
from backend.core.capability_packages import (
    DEFAULT_PRESET,
    enabled_tool_names,
    mounted_tool_names,
)
from backend.core.llm.client import MockLLMClient
from backend.services.event_bus import EventBus
from backend.services.model_store import ModelStore
from backend.services.persistence import Persistence
from backend.services.plugins_store import (
    PLUGIN_PRESET_OVERRIDE_ENV,
    PluginsConfigError,
    PluginsWriteError,
    load_plugins_config,
    packages_catalog,
    plugins_path,
    presets_catalog,
    resolve_effective_plugins,
    restart_required,
    save_plugins_config,
)
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_settings

ALL_PACKAGES = frozenset(
    {"files", "shell", "skills", "web-search", "subagent", "mcp"}
)


def _write_raw(settings, text: str) -> Path:
    path = plugins_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_config(settings, payload: Any) -> Path:
    return _write_raw(settings, json.dumps(payload, ensure_ascii=False))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return dist


def _cli_settings(tmp_path, monkeypatch, **overrides):
    settings = make_settings(
        tmp_path, frontend_dist=str(_dist(tmp_path)), **overrides
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.delenv(PLUGIN_PRESET_OVERRIDE_ENV, raising=False)
    return settings


class _FakeChild:
    pid = 4242

    def poll(self):
        return None

    def terminate(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None


def _capture_spawn(seen: dict):
    def spawn(cmd, env, log_path):
        seen["cmd"] = cmd
        seen["env"] = dict(env)
        seen["log"] = log_path
        return _FakeChild()

    return spawn


def _healthy(port, timeout):
    return {"status": "ok"}


# ── store: missing / valid / invalid / atomic ──
def test_missing_file_with_default_settings_is_full(tmp_path):
    # The field default is full; with no file it is the env fallback that wins.
    settings = make_settings(tmp_path)
    saved = load_plugins_config(settings)
    assert saved.preset == DEFAULT_PRESET
    assert saved.overrides == {}
    assert saved.exists is False

    effective = resolve_effective_plugins(settings)
    assert effective.source == "env"
    assert effective.packages == ALL_PACKAGES
    assert effective.tools == enabled_tool_names(ALL_PACKAGES)


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_env_preset_is_an_error_not_a_silent_full(tmp_path, blank):
    settings = make_settings(tmp_path, plugins_preset=blank)
    with pytest.raises(PluginsConfigError, match="PLUGINS_PRESET"):
        resolve_effective_plugins(settings)


def test_missing_env_preset_value_is_an_error_not_a_silent_full(tmp_path):
    settings = SimpleNamespace(
        data_path=tmp_path, plugins_preset=None, subagent_enabled=True
    )
    with pytest.raises(PluginsConfigError, match="PLUGINS_PRESET"):
        resolve_effective_plugins(settings)


def test_missing_file_falls_back_to_the_env_field(tmp_path):
    settings = make_settings(tmp_path, plugins_preset="minimal")
    effective = resolve_effective_plugins(settings)
    assert effective.source == "env"
    assert effective.preset == "minimal"
    assert effective.packages == frozenset({"files", "shell"})


def test_valid_file_overrides_the_env_field(tmp_path):
    settings = make_settings(tmp_path, plugins_preset="research")
    _write_config(
        settings, {"preset": "minimal", "overrides": {"mcp": True}}
    )
    saved = load_plugins_config(settings)
    assert saved.exists is True
    assert saved.preset == "minimal"
    assert saved.overrides == {"mcp": True}

    effective = resolve_effective_plugins(settings)
    # File exists -> authoritative; the env value is only the file-missing fallback.
    assert effective.source == "file"
    assert effective.preset == "minimal"
    assert effective.packages == frozenset({"files", "shell", "mcp"})


def test_cli_override_beats_the_file_but_keeps_its_overrides(tmp_path):
    settings = make_settings(tmp_path)
    _write_config(
        settings, {"preset": "research", "overrides": {"mcp": False}}
    )
    effective = resolve_effective_plugins(settings, preset_override="full")
    assert effective.source == "cli"
    assert effective.preset == "full"
    assert effective.overrides == {"mcp": False}
    assert "mcp" not in effective.packages
    assert "skills" in effective.packages


def test_internal_override_env_is_a_cli_override(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, plugins_preset="full")
    monkeypatch.setenv(PLUGIN_PRESET_OVERRIDE_ENV, "minimal")
    effective = resolve_effective_plugins(settings)
    assert effective.source == "cli"
    assert effective.preset == "minimal"
    assert effective.packages == frozenset({"files", "shell"})


def test_invalid_file_json_is_a_readable_error(tmp_path):
    settings = make_settings(tmp_path)
    path = _write_raw(settings, "{not json")
    with pytest.raises(PluginsConfigError) as excinfo:
        load_plugins_config(settings)
    message = str(excinfo.value)
    assert str(path) in message
    assert "JSON" in message
    assert "修法" in message


def test_non_utf8_file_is_a_readable_error(tmp_path):
    settings = make_settings(tmp_path)
    path = plugins_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"preset": "full", "overrides": {}}\xff')

    with pytest.raises(PluginsConfigError) as excinfo:
        load_plugins_config(settings)

    message = str(excinfo.value)
    assert str(path) in message
    assert "UTF-8" in message
    assert "修法" in message


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        {"overrides": {}},
        {"preset": "ultra", "overrides": {}},
        {"preset": "minimal", "overrides": {"nope": True}},
        {"preset": "minimal", "overrides": {"files": "yes"}},
        {"preset": "minimal", "overrides": []},
    ],
)
def test_invalid_file_contents_are_readable_errors(tmp_path, payload):
    settings = make_settings(tmp_path)
    path = _write_raw(
        settings,
        payload if isinstance(payload, str) else json.dumps(payload),
    )
    with pytest.raises(PluginsConfigError) as excinfo:
        load_plugins_config(settings)
    message = str(excinfo.value)
    assert str(path) in message
    assert "修法" in message


def test_legacy_bash_package_id_fails_fast_and_keeps_the_file(tmp_path):
    """ADR 0013: the retired `bash` id is unsupported — no alias, no rewrite."""
    settings = make_settings(tmp_path)
    path = _write_config(settings, {"preset": "full", "overrides": {"bash": True}})
    before = path.read_bytes()

    with pytest.raises(PluginsConfigError) as excinfo:
        load_plugins_config(settings)
    message = str(excinfo.value)
    assert "bash" in message  # the offending id is named
    assert "shell" in message  # and the valid set is shown
    assert str(path) in message
    assert path.read_bytes() == before, "the old file must not be rewritten"

    # Startup resolution refuses the same file instead of falling back to full.
    with pytest.raises(PluginsConfigError):
        resolve_effective_plugins(settings)


def test_save_is_atomic_and_round_trips(tmp_path):
    settings = make_settings(tmp_path)
    saved = save_plugins_config(settings, "minimal", {"mcp": True})
    assert saved.preset == "minimal"
    assert saved.overrides == {"mcp": True}
    path = plugins_path(settings)
    assert path.is_file()
    assert not path.with_name(path.name + ".tmp").exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == {"preset": "minimal", "overrides": {"mcp": True}}
    assert load_plugins_config(settings) == saved


def test_save_creates_the_data_directory(tmp_path):
    settings = make_settings(tmp_path / "nested" / "data")
    save_plugins_config(settings, "research", {})
    assert plugins_path(settings).is_file()


def test_invalid_save_never_touches_the_existing_file(tmp_path):
    settings = make_settings(tmp_path)
    save_plugins_config(settings, "minimal", {})
    before = plugins_path(settings).read_text(encoding="utf-8")
    with pytest.raises(PluginsConfigError):
        save_plugins_config(settings, "ultra", {})
    with pytest.raises(PluginsConfigError):
        save_plugins_config(settings, "minimal", {"nope": True})
    assert plugins_path(settings).read_text(encoding="utf-8") == before


def test_restart_required_compares_saved_with_active(tmp_path):
    settings = make_settings(tmp_path)
    saved = load_plugins_config(settings)
    active = resolve_effective_plugins(settings)
    assert restart_required(active, saved) is False
    changed = save_plugins_config(settings, "minimal", {})
    assert restart_required(active, changed) is True
    same = save_plugins_config(settings, active.preset, dict(active.overrides))
    assert restart_required(active, same) is False


def test_restart_required_three_cases(tmp_path):
    settings = make_settings(tmp_path)
    active = resolve_effective_plugins(settings)

    # 1) No file: nothing to restart (env/default is what is running).
    assert restart_required(active, load_plugins_config(settings)) is False
    # 2) File exists and matches the running configuration.
    same = save_plugins_config(settings, active.preset, dict(active.overrides))
    assert restart_required(active, same) is False
    # 3) File exists and differs from the running configuration.
    changed = save_plugins_config(settings, "minimal", {})
    assert restart_required(active, changed) is True


def test_invalid_cli_preset_is_readable(tmp_path):
    settings = make_settings(tmp_path)
    with pytest.raises(PluginsConfigError) as excinfo:
        resolve_effective_plugins(settings, preset_override="ultra")
    assert "CLI --preset" in str(excinfo.value)


def test_api_catalogues_cover_the_manifest():
    assert [item["id"] for item in presets_catalog()] == [
        "minimal",
        "research",
        "full",
    ]
    packages = packages_catalog()
    assert [item["id"] for item in packages] == [
        "files",
        "shell",
        "skills",
        "web-search",
        "subagent",
        "mcp",
    ]
    files = next(item for item in packages if item["id"] == "files")
    assert files["tools"] == ["read", "write", "edit", "ls", "glob", "grep"]
    assert files["connects_mcp"] is False
    assert files["affects_subagent_face"] is True
    mcp = next(item for item in packages if item["id"] == "mcp")
    assert mcp["connects_mcp"] is True


# ── TaskManager explicit injection precedence (P4-2 note 5) ──
def test_task_manager_plugins_param_beats_settings(tmp_path):
    settings = make_settings(tmp_path, plugins_preset="full")
    _write_config(settings, {"preset": "minimal", "overrides": {}})
    plugins = resolve_effective_plugins(settings)
    assert plugins.packages == frozenset({"files", "shell"})

    tm = TaskManager(
        settings,
        EventBus(),
        Persistence(settings),
        llm_client=MockLLMClient(),
        default_workspace=settings.artifacts_path,
        model_store=ModelStore(settings, home_dir=settings.data_path / "home"),
        packages=frozenset({"files"}),
        plugins=plugins,
    )
    try:
        assert tm.packages == frozenset({"files"})
        assert {tool.name for tool in tm._tools} == {
            "read",
            "write",
            "edit",
            "ls",
            "glob",
            "grep",
        }
    finally:
        tm.shutdown()


def test_task_manager_resolves_the_file_when_not_injected(tmp_path):
    settings = make_settings(tmp_path, plugins_preset="full")
    _write_config(settings, {"preset": "minimal", "overrides": {}})
    tm = TaskManager(
        settings,
        EventBus(),
        Persistence(settings),
        llm_client=MockLLMClient(),
        default_workspace=settings.artifacts_path,
        model_store=ModelStore(settings, home_dir=settings.data_path / "home"),
    )
    try:
        assert tm.packages == frozenset({"files", "shell"})
    finally:
        tm.shutdown()


# ── entry fail-fast: CLI web / repl / start / runtime ──
def test_build_runtime_rejects_an_invalid_file(tmp_path):
    settings = make_settings(tmp_path)
    path = _write_raw(settings, "{broken")
    from backend.runtime import build_runtime

    with pytest.raises(PluginsConfigError) as excinfo:
        build_runtime(settings)
    assert str(path) in str(excinfo.value)


def test_cli_web_valid_preset_reaches_the_serving_process_env(
    tmp_path, monkeypatch
):
    settings = _cli_settings(tmp_path, monkeypatch)
    port = _free_port()
    previous = os.environ.pop(PLUGIN_PRESET_OVERRIDE_ENV, None)
    seen: dict = {}
    try:
        code = cli.main(
            ["--preset", "minimal", "--port", str(port)],
            spawn=_capture_spawn(seen),
            probe=_healthy,
            open_browser=lambda url: None,
        )
    finally:
        os.environ.pop(PLUGIN_PRESET_OVERRIDE_ENV, None)
        if previous is not None:
            os.environ[PLUGIN_PRESET_OVERRIDE_ENV] = previous

    assert code == 0
    # The preset travels in the detached child's environment, before it imports
    # settings (the parent process env is no longer mutated by the launcher).
    assert seen["env"][PLUGIN_PRESET_OVERRIDE_ENV] == "minimal"
    assert "backend.serve" in seen["cmd"]
    assert (
        resolve_effective_plugins(settings, preset_override="minimal").packages
        == frozenset({"files", "shell"})
    )


def test_cli_web_invalid_preset_fails_before_claim_or_spawn(
    tmp_path, monkeypatch, capsys
):
    settings = _cli_settings(tmp_path, monkeypatch)
    spawned: List[Any] = []
    code = cli.main(
        ["--preset", "ultra"],
        spawn=lambda *args: spawned.append(args),
        probe=_healthy,
    )
    assert code == 1
    assert spawned == []
    assert not server_lock.lock_path(settings).exists()
    assert "ultra" in capsys.readouterr().out


def test_cli_web_invalid_file_fails_before_claim_or_spawn(
    tmp_path, monkeypatch, capsys
):
    settings = _cli_settings(tmp_path, monkeypatch)
    path = _write_raw(settings, "{broken")
    spawned: List[Any] = []
    code = cli.main(
        [],
        spawn=lambda *args: spawned.append(args),
        probe=_healthy,
    )
    assert code == 1
    assert spawned == []
    assert not server_lock.lock_path(settings).exists()
    assert str(path) in capsys.readouterr().out


def test_cli_dispatch_passes_repl_preset_to_the_subparser(tmp_path, monkeypatch):
    _cli_settings(tmp_path, monkeypatch)
    entered: List[List[str]] = []
    code = cli.main(
        ["repl", "--preset", "minimal"],
        repl=lambda argv: entered.append(argv) or 0,
    )
    assert code == 0
    assert entered == [["--preset", "minimal"]]


def test_cli_dispatch_rejects_an_invalid_repl_preset_before_the_seam(
    tmp_path, monkeypatch, capsys
):
    settings = _cli_settings(tmp_path, monkeypatch)
    entered: List[List[str]] = []
    code = cli.main(
        ["repl", "--preset", "ultra"],
        repl=lambda argv: entered.append(argv) or 0,
    )
    assert code == 1
    assert entered == []
    assert not server_lock.lock_path(settings).exists()
    assert "ultra" in capsys.readouterr().out


def test_repl_subparser_receives_preset_and_passes_it_through(
    tmp_path, monkeypatch
):
    """#332 S6: the terminal forwards ``--preset`` to the shared-backend ensure.

    It no longer builds a runtime itself (ADR 0013 decision 2); the preset is
    what the *backend this run might spawn* has to start with.
    """
    settings = make_settings(tmp_path)
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    monkeypatch.setenv(PLUGIN_PRESET_OVERRIDE_ENV, "")
    captured: dict = {}

    def ensure(_settings, **kwargs):
        captured.update(kwargs)
        return 1, 0  # stop before connecting: only the preset plumbing matters

    monkeypatch.setattr(cli, "ensure_backend", ensure)
    code = repl.repl_main(
        ["--preset", "minimal"],
        input_fn=lambda prompt: "exit",
        output=lambda line: None,
        configure_stdio=False,
    )

    assert code == 1
    assert captured["preset"] == "minimal"


def test_repl_invalid_preset_fails_before_ensure(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    ensured: List[int] = []
    monkeypatch.setattr(
        cli, "ensure_backend", lambda *args, **kwargs: ensured.append(1) or (0, 0)
    )
    out: List[str] = []
    code = repl.repl_main(
        ["--preset", "ultra"],
        input_fn=lambda prompt: "exit",
        output=out.append,
        configure_stdio=False,
    )
    assert code == 1
    assert ensured == [], "a bad preset must fail before anything is spawned"
    assert any("ultra" in line for line in out)


def test_repl_invalid_file_fails_before_ensure(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    path = _write_raw(settings, "{broken")
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    ensured: List[int] = []
    monkeypatch.setattr(
        cli, "ensure_backend", lambda *args, **kwargs: ensured.append(1) or (0, 0)
    )
    out: List[str] = []
    code = repl.repl_main(
        [],
        input_fn=lambda prompt: "exit",
        output=out.append,
        configure_stdio=False,
    )
    assert code == 1
    assert ensured == []
    assert any(str(path) in line for line in out)


def test_start_rejects_an_invalid_file_before_claim_or_spawn(
    tmp_path, monkeypatch, capsys
):
    import sys

    import start
    from backend import config as backend_config

    settings = make_settings(tmp_path)
    path = _write_raw(settings, "{broken")
    monkeypatch.setattr(backend_config, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["start.py", "--backend-only"])
    actions: List[str] = []
    monkeypatch.setattr(start, "_run", lambda *args, **kwargs: actions.append("spawn"))
    monkeypatch.setattr(
        server_lock,
        "claim_lock",
        lambda *args, **kwargs: actions.append("claim") or True,
    )
    code = start.main()
    assert code == 2
    assert actions == []
    out = capsys.readouterr().out
    assert str(path) in out
    assert "修法" in out


# ── #308 M1: the effective/assembled face follows the legacy gates ──
def test_mounted_tool_names_helper_covers_gates_and_dynamic_names():
    full = mounted_tool_names(ALL_PACKAGES)
    assert "spawn_subagent" in full
    gated = mounted_tool_names(ALL_PACKAGES, subagent_enabled=False)
    assert "spawn_subagent" not in gated
    dynamic = mounted_tool_names(
        ALL_PACKAGES, extra_names={"mcp_fetch"}
    )
    assert "mcp_fetch" in dynamic


def test_effective_tools_respect_the_legacy_subagent_switch(tmp_path):
    settings = make_settings(tmp_path, plugins_preset="full", subagent_enabled=False)
    effective = resolve_effective_plugins(settings)
    assert effective.packages == ALL_PACKAGES
    assert "spawn_subagent" not in effective.tools
    assert "read" in effective.tools


def test_runtime_plugins_expose_the_actually_assembled_face(tmp_path):
    from backend.runtime import build_runtime

    settings = make_settings(tmp_path, subagent_enabled=False)
    runtime = build_runtime(settings)
    try:
        # Legacy gate removed the tool from the manager; the API-facing effective
        # config must not claim it either.
        assert "spawn_subagent" not in runtime.plugins.tools
        assert runtime.plugins.tools == runtime.task_manager.assembled_tool_names
        assert "spawn_subagent" not in runtime.task_manager.assembled_tool_names
    finally:
        runtime.task_manager.shutdown()


# ── #308 M3/L2: write IO errors vs validation errors, unique temp files ──
def test_write_io_error_preserves_the_old_file_and_cleans_tmp(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    save_plugins_config(settings, "minimal", {})
    path = plugins_path(settings)
    before = path.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(PluginsWriteError, match="保存失败"):
        save_plugins_config(settings, "research", {})

    assert path.read_text(encoding="utf-8") == before
    assert list(path.parent.glob("plugins.json.*.tmp")) == []


def test_concurrent_saves_use_unique_temp_files(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    settings = make_settings(tmp_path)
    presets = ["minimal", "research", "full"] * 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda preset: save_plugins_config(settings, preset, {}), presets))

    path = plugins_path(settings)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["preset"] in {"minimal", "research", "full"}
    assert list(path.parent.glob("plugins.json.*.tmp")) == []


def test_cli_preset_does_not_exempt_a_broken_file(tmp_path, monkeypatch, capsys):
    settings = _cli_settings(tmp_path, monkeypatch)
    path = _write_raw(settings, "{broken")
    spawned: List[Any] = []
    code = cli.main(
        ["--preset", "minimal"],
        spawn=lambda *args: spawned.append(args),
        probe=_healthy,
    )
    assert code == 1
    assert spawned == []
    assert not server_lock.lock_path(settings).exists()
    out = capsys.readouterr().out
    assert str(path) in out
    assert "修法" in out
