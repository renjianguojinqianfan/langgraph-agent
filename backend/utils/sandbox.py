"""Child-process environment helper.

Child processes (the bash tool and every MCP stdio server) run with this
process's account privileges — this is not a sandbox (README「这不是安全边界」).
The allowlists filter what a child *inherits*; they are not a secret boundary —
files, and on Linux ``/proc/$PPID/environ``, remain readable by the same account.
"""

from __future__ import annotations

import os
from typing import Dict

#: The only parent variables a child process may see. API keys, tokens and
#: provider endpoints stay in the parent process (#50).
ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
        "LD_LIBRARY_PATH",
    }
)


#: Extra parent variables an MCP stdio server may inherit on top of
#: :data:`ENV_ALLOWLIST`. npm / npx (and similar CLIs) resolve their config and
#: cache directories from these; they carry filesystem paths, not credentials.
#: Anything else a third-party server needs must come from its own explicit
#: ``env`` mapping in ``mcp_servers`` (issue #254).
MCP_ENV_ALLOWLIST = ENV_ALLOWLIST | frozenset(
    {
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "SYSTEMDRIVE",
    }
)


def build_sandbox_env(allowlist: frozenset[str] = ENV_ALLOWLIST) -> Dict[str, str]:
    """Parent environment filtered down to ``allowlist``.

    Matching is case-insensitive so Windows casing (``Path``) still passes.
    ``PYTHONUNBUFFERED`` is added for every child: both callers speak a
    line-oriented protocol over pipes and must not lose buffered output.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowlist and value is not None
    }
    env["PYTHONUNBUFFERED"] = "1"
    return env
