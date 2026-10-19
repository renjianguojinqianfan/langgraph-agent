"""Child-process environment helper.

The bash tool runs commands with this process's account privileges — it is not
a sandbox (README「这不是安全边界」). What survives here is the one boundary the
tool does keep: the child sees only :data:`ENV_ALLOWLIST`, so API keys and
provider endpoints never leak into a command.
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


def build_sandbox_env() -> Dict[str, str]:
    """Parent environment filtered down to :data:`ENV_ALLOWLIST`.

    Matching is case-insensitive so Windows casing (``Path``) still passes.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in ENV_ALLOWLIST and value is not None
    }
    env["PYTHONUNBUFFERED"] = "1"
    return env
