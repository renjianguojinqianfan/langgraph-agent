"""Git tool set (P2 item 2) — safe, local git workflow for the Agent.

Security model (orthogonal to the ``code_exec`` sandbox — Git tools NEVER go
through ``sandbox.run_code``; they call the ``git`` binary directly through a
parametrised subprocess):

* **Whitelist verbs** — only ``status/diff/commit/log/branch/checkout/init`` are
  accepted; the verb is hard-coded by each tool class and cannot be influenced
  by tool arguments.
* **Blacklist defense-in-depth** — remote / destructive verbs
  (``push/pull/fetch/clone/remote/reset/clean/rebase/merge/...``) are rejected
  at the runner entry even if a future tool ever passes them.
* **Argument-level option injection protection** — dangerous flags
  (``--force/-f/--hard/-D/-fdx/...``) and any unknown ``-``-prefixed argument
  are rejected; branch names starting with ``-`` are rejected; the commit
  message travels as a single ``-m`` argv (literal, no shell).
* **Path confinement** — every ``path`` argument resolves inside
  ``git_repo_path`` (``is_relative_to``); ``../`` escapes are rejected.
* **Non-git detection** — every verb except ``git_init`` probes
  ``git rev-parse --is-inside-work-tree`` first and returns a clear error.
* **Wall-clock timeout** — ``subprocess.run(timeout=git_timeout_sec)`` kills a
  hung ``git`` child.

``requires_confirm=True`` on ``git_commit`` / ``git_checkout`` / ``git_init``
routes those calls through the existing P0 ``human_confirm`` flow. Blacklisted
commands are *rejected outright* (never offered for confirmation).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult

logger = get_logger("tool.git")

#: Verbs the 7 Git tools may issue (each tool hard-codes its verb).
#: ``rev-parse`` is additionally allowed as a read-only probe used internally
#: by GitCommitTool (HEAD hash) and GitToolRunner (_ensure_repo).
_ALLOWED_VERBS = {"status", "diff", "commit", "log", "branch", "checkout", "init", "rev-parse"}

#: Dangerous verbs rejected even though they are outside the whitelist — this
#: is a second line of defence for future extensions (PRD Q3 / §4.3).
_BLOCKED_VERBS = {
    "push", "pull", "fetch", "clone", "remote", "reset", "clean", "rebase",
    "merge", "cherry-pick", "cherry_pick", "revert", "rm", "tag", "stash",
    "submodule", "gc", "prune", "fsck", "repack", "filter-branch",
    "update-ref", "symbolic-ref", "config", "mv", "apply", "am",
    "format-patch", "notes", "replace", "worktree", "archive", "bundle",
    "daemon", "send-email", "svn", "p4", "difftool", "mergetool",
}

#: Blocked option flags — rejected wherever they appear in the argv.
_BLOCKED_ARGS = {
    "--force", "-f", "--hard", "-D", "-fdx", "-fd", "-df", "-x", "--all",
    "--prune", "--tags", "--mirror", "--delete", "--cleanup",
    "--force-with-lease", "--no-verify", "--amend", "--reset", "--abort",
    "--continue", "--soft", "--mixed", "--keep", "--autosquash",
}

#: Internal flags the 7 tools legitimately emit (safe, fixed literals).
_ALLOWED_INTERNAL_ARGS = {
    "--cached", "--oneline", "--short", "--porcelain=v1", "-a", "-n", "-m", "--",
    "--show-current",
}


class GitCommandError(Exception):
    """Raised when a git invocation is rejected or fails."""


class GitToolRunner:
    """Parametrised ``git`` subprocess runner confined to one repo root."""

    def __init__(self, repo: Path, timeout: float = 30.0) -> None:
        self.repo = Path(repo)
        try:
            self.repo.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("could not create git repo root %s: %s", self.repo, exc)
        self.timeout = float(timeout)

    # ── validation ──
    def resolve_path(self, path: Optional[str]) -> Path:
        """Resolve a user-supplied repo-relative path; reject escapes."""
        if path is None or str(path).strip() in ("", ".", "./"):
            return self.repo
        raw = str(path).strip()
        if raw.startswith("-"):
            raise GitCommandError(f"path {raw!r} must not start with '-'")
        p = Path(raw)
        if not p.is_absolute():
            p = self.repo / p
        try:
            resolved = p.resolve()
        except Exception as exc:
            raise GitCommandError(f"invalid path {raw!r}: {exc}") from exc
        root = self.repo.resolve()
        try:
            inside = resolved.is_relative_to(root)
        except Exception as exc:  # pragma: no cover - defensive
            raise GitCommandError(f"cannot check path {raw!r}: {exc}") from exc
        if not inside:
            raise GitCommandError(f"path {raw!r} is outside the git repo root")
        return resolved

    def _ensure_repo(self, target: Path) -> None:
        """Probe that ``target`` is inside a git work tree (non-init verbs)."""
        try:
            proc = subprocess.run(
                ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise GitCommandError(f"git probe timed out after {self.timeout}s") from None
        except FileNotFoundError:
            raise GitCommandError("git executable not found on PATH") from None
        except Exception as exc:  # pragma: no cover - defensive
            raise GitCommandError(f"failed to probe git repository: {exc}") from exc
        if proc.returncode != 0 or proc.stdout.strip() != "true":
            raise GitCommandError("not a git repository (run git_init first)")

    def _validate(self, verb: str, args: List[str]) -> None:
        if verb not in _ALLOWED_VERBS:
            raise GitCommandError(f"git command {verb!r} is not allowed")
        if verb in _BLOCKED_VERBS:
            raise GitCommandError(f"git command {verb!r} is blocked")
        for a in args:
            if a in _BLOCKED_ARGS:
                raise GitCommandError(f"git argument {a!r} is blocked")
            if a.startswith("-") and a not in _ALLOWED_INTERNAL_ARGS:
                raise GitCommandError(f"git argument {a!r} is not allowed")

    # ── execute ──
    def run(
        self,
        verb: str,
        args: List[str],
        path: Optional[str] = None,
        *,
        check_repo: bool = True,
    ) -> Dict[str, Any]:
        """Run ``git -C <target> <verb> <args>`` and return a result dict.

        Raises :class:`GitCommandError` for validation / timeout / binary
        errors; the callers convert that into ``ToolResult(success=False)``.
        """
        target = self.resolve_path(path)
        if verb == "init":
            # `git init` requires the working directory to exist when invoked
            # with `-C <dir>`; create it up front (also covers nested subpaths).
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # pragma: no cover - defensive
                raise GitCommandError(f"cannot create directory {target}: {exc}") from exc
        if check_repo:
            self._ensure_repo(target)
        self._validate(verb, list(args))

        argv = ["git", "-C", str(target), verb, *args]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                shell=False,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise GitCommandError(f"git {verb} timed out after {self.timeout}s") from None
        except FileNotFoundError:
            raise GitCommandError("git executable not found on PATH") from None
        except Exception as exc:  # pragma: no cover - defensive
            raise GitCommandError(f"git {verb} failed: {exc}") from exc
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }


class GitTool(BaseTool):
    """Base for the seven Git tools (never registered via ``@register``)."""

    #: The git verb this tool runs (used as ``name = f"git_{verb}"``).
    verb: str = ""
    requires_confirm: bool = False
    # Local, deterministic commands — no retry / no circuit breaker.
    retryable: bool = False
    max_retries: int = 0
    circuit_breaker: bool = False

    def __init__(self, runner: GitToolRunner, settings: Optional[Settings] = None) -> None:
        super().__init__(settings)
        self._runner = runner
        self.name = f"git_{self.verb}"

    def _ok(self, data: Dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data=data)

    def _fail(self, exc: GitCommandError) -> ToolResult:
        return ToolResult(success=False, error=str(exc))

    def run(self, **kwargs: Any) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError


class GitStatusTool(GitTool):
    verb = "status"
    description = (
        "Show the working-tree status of the configured git repository "
        "(porcelain short format + current branch). Read-only."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional repo-relative subpath to scope the status.",
            }
        },
        "required": [],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            path = kwargs.get("path")
            res = self._runner.run("status", ["--short"], path=path)
            if res["exit_code"] != 0:
                return ToolResult(success=False, error=(res["stderr"] or res["stdout"]).strip())
            # Current branch via a second read-only probe.
            branch = ""
            try:
                bres = self._runner.run("branch", ["--show-current"], path=path)
                branch = (bres["stdout"] or "").strip()
            except GitCommandError:
                branch = ""
            changes = [ln for ln in res["stdout"].splitlines() if ln.strip()]
            return self._ok({"branch": branch, "changes": changes})
        except GitCommandError as exc:
            return self._fail(exc)


class GitDiffTool(GitTool):
    verb = "diff"
    description = (
        "Show unstaged differences (or staged differences when `staged` is "
        "true) of the configured git repository. Read-only."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "staged": {
                "type": "boolean",
                "description": "When true, show only the staged (--cached) diff.",
            },
            "path": {
                "type": "string",
                "description": "Optional repo-relative subpath to scope the diff.",
            },
        },
        "required": [],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            staged = bool(kwargs.get("staged", False))
            path = kwargs.get("path")
            args = ["--cached"] if staged else []
            res = self._runner.run("diff", args, path=path)
            if res["exit_code"] != 0:
                return ToolResult(success=False, error=(res["stderr"] or res["stdout"]).strip())
            return self._ok({"staged": staged, "diff": res["stdout"]})
        except GitCommandError as exc:
            return self._fail(exc)


class GitCommitTool(GitTool):
    verb = "commit"
    requires_confirm = True
    description = (
        "Create a commit with the given message (and optional repo-relative "
        "path scope). Requires human confirmation. Returns the commit hash."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message."},
            "path": {
                "type": "string",
                "description": "Optional repo-relative path to commit only that path.",
            },
        },
        "required": ["message"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            message = str(kwargs.get("message", "")).strip()
            if not message:
                return ToolResult(success=False, error="`message` is required and must not be empty")
            path = kwargs.get("path")
            args: List[str] = ["-m", message]
            if path is not None and str(path).strip() not in ("", ".", "./"):
                args += ["--", str(self._runner.resolve_path(path))]
            res = self._runner.run("commit", args, path=None)
            if res["exit_code"] != 0:
                return ToolResult(success=False, error=(res["stderr"] or res["stdout"]).strip())
            # Fetch the new HEAD hash.
            head = self._runner.run("rev-parse", ["HEAD"], path=path)
            commit_hash = (head.get("stdout") or "").strip()
            return self._ok({"commit": commit_hash, "output": (res["stdout"] or "").strip()})
        except GitCommandError as exc:
            return self._fail(exc)


class GitLogTool(GitTool):
    verb = "log"
    description = (
        "Show the most recent commits (oneline format) of the configured git "
        "repository. Read-only."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of commits to show (default 10).",
            },
            "path": {
                "type": "string",
                "description": "Optional repo-relative subpath to scope the log.",
            },
        },
        "required": [],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            limit = kwargs.get("limit", 10)
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                return ToolResult(success=False, error=f"`limit` must be an integer, got {limit!r}")
            if limit <= 0:
                return ToolResult(success=False, error="`limit` must be positive")
            path = kwargs.get("path")
            res = self._runner.run("log", ["--oneline", "-n", str(limit)], path=path)
            if res["exit_code"] != 0:
                stderr = res["stderr"].lower()
                if "does not have any commits" in stderr or "does not have any commits yet" in stderr:
                    return self._ok({"commits": [], "note": "empty repository"})
                return ToolResult(success=False, error=(res["stderr"] or res["stdout"]).strip())
            commits: List[Dict[str, str]] = []
            for line in res["stdout"].splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(" ", 1)
                commits.append(
                    {
                        "hash": parts[0],
                        "message": parts[1] if len(parts) > 1 else "",
                    }
                )
            return self._ok({"commits": commits})
        except GitCommandError as exc:
            return self._fail(exc)


class GitBranchTool(GitTool):
    verb = "branch"
    description = (
        "List the branches of the configured git repository and mark the "
        "current one. Read-only. `path` is accepted for API compatibility but "
        "not used by git branch."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Accepted for API compatibility (unused).",
            }
        },
        "required": [],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            res = self._runner.run("branch", ["-a"], path=None)
            if res["exit_code"] != 0:
                return ToolResult(success=False, error=(res["stderr"] or res["stdout"]).strip())
            branches: List[str] = []
            current = ""
            for line in res["stdout"].splitlines():
                name = line.strip()
                if not name:
                    continue
                if name.startswith("* "):
                    current = name[2:].strip()
                    name = name[2:].strip()
                branches.append(name)
            return self._ok({"branches": branches, "current": current})
        except GitCommandError as exc:
            return self._fail(exc)


class GitCheckoutTool(GitTool):
    verb = "checkout"
    requires_confirm = True
    description = (
        "Switch to the given branch in the configured git repository. "
        "Requires human confirmation."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "branch": {"type": "string", "description": "Branch name to switch to."},
            "path": {
                "type": "string",
                "description": "Accepted for API compatibility (unused).",
            },
        },
        "required": ["branch"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            branch = str(kwargs.get("branch", "")).strip()
            if not branch:
                return ToolResult(success=False, error="`branch` is required")
            if branch.startswith("-"):
                return ToolResult(success=False, error=f"branch {branch!r} must not start with '-'")
            res = self._runner.run("checkout", [branch], path=None)
            if res["exit_code"] != 0:
                return ToolResult(success=False, error=(res["stderr"] or res["stdout"]).strip())
            return self._ok({"branch": branch, "output": (res["stdout"] or res["stderr"]).strip()})
        except GitCommandError as exc:
            return self._fail(exc)


class GitInitTool(GitTool):
    verb = "init"
    requires_confirm = True
    description = (
        "Initialise a git repository in the configured repo root (or an "
        "optional subdirectory). Requires human confirmation."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional repo-relative directory to initialise (default: repo root).",
            }
        },
        "required": [],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            path = kwargs.get("path")
            res = self._runner.run("init", [], path=path, check_repo=False)
            if res["exit_code"] != 0:
                return ToolResult(success=False, error=(res["stderr"] or res["stdout"]).strip())
            out = (res["stdout"] or res["stderr"] or "").strip()
            target = self._runner.resolve_path(path)
            return self._ok(
                {
                    "ok": True,
                    "path": str(target),
                    "reinitialized": "Reinitialized" in out or "reinitialized" in out,
                    "output": out,
                }
            )
        except GitCommandError as exc:
            return self._fail(exc)


def build_git_tools(settings: Settings) -> List[GitTool]:
    """Instantiate the seven Git tools bound to ``settings.git_repo_path``."""
    runner = GitToolRunner(settings.git_repo_path, timeout=settings.git_timeout_sec)
    return [
        GitStatusTool(runner, settings),
        GitDiffTool(runner, settings),
        GitCommitTool(runner, settings),
        GitLogTool(runner, settings),
        GitBranchTool(runner, settings),
        GitCheckoutTool(runner, settings),
        GitInitTool(runner, settings),
    ]
