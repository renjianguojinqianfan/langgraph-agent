"""Keep child consoles hidden on Windows (#356).

A console child started with ``CREATE_NO_WINDOW`` gets no console window of its
own; without the flag, every ``python.exe`` / ``pwsh.exe`` / ``cmd.exe`` spawn
can flash a black window. The flag only exists on Windows, so callers keep
using the helper on every platform: elsewhere it returns the caller's flags
untouched, which keeps the POSIX code paths byte-for-byte the same.

``_IS_WINDOWS`` is a module-level seam: the platform is fixed for a process,
and tests patch it instead of relying on the OS they happen to run on.
"""

from __future__ import annotations

import os
import subprocess

#: The platform seam tests patch; production reads it once (``os.name`` never changes).
_IS_WINDOWS = os.name == "nt"

#: ``subprocess.CREATE_NO_WINDOW`` (0x08000000); the attribute is Windows-only.
CREATE_NO_WINDOW = 0x0800_0000


def hidden_creationflags(existing: int = 0) -> int:
    """OR ``CREATE_NO_WINDOW`` into ``existing``; no-op outside Windows."""
    if not _IS_WINDOWS:
        return existing
    return existing | int(getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW))
