"""Static-file path guard: refuse UNC / absolute paths before resolution.

Why this exists (2026-10-24 security pass)
------------------------------------------
``starlette<1.1.0`` resolves a ``StaticFiles`` request path with
``os.path.realpath`` **before** the "is it still inside the served directory"
check (``starlette/staticfiles.py::lookup_path``). On Windows a UNC path that
reaches ``realpath`` opens an SMB connection to the named host, leaking the
process account's NTLMv2 hash (CVE-2026-48818: SSRF + credential theft).

We cannot bump starlette on its own: ``fastapi==0.112.4`` pins
``starlette<0.39``, and the FastAPI/starlette bump is a separate dependency
chore (see ``docs/security.md``). Until that chore lands, the frontend mount
uses :class:`SafeStaticFiles`, which rejects the forms that would reach outside
the served directory (UNC, absolute / drive-qualified, NUL) **before** starlette
touches the filesystem. Traversal that survives ``normpath`` (``..``) still
relies on starlette's own ``commonpath`` check; this guard is not a complete
path validator, it removes the network-capable forms from the ``realpath`` call.

The check runs on the already-normalised path that ``StaticFiles.get_path``
returns (that is exactly the argument ``lookup_path`` receives), so drive
letters and leading UNC / root-relative separators are caught after
``normpath`` has converted them — checking the raw request path would miss
``/C:/Windows/...``.

Scope: the frontend mount only. API routes never go through ``StaticFiles``.
Remove this module (and go back to plain ``StaticFiles``) once the framework
bump to ``starlette>=1.1`` lands.
"""

from __future__ import annotations

import os

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


def is_unsafe_static_path(path: str) -> bool:
    """True when the normalised static path must not reach ``lookup_path``.

    Rejected forms (all escape the served directory or are invalid paths):
    * a leading separator — ``\\\\host\\share`` (UNC), ``\\name``
      (drive-root-relative) and, on POSIX, ``//host/share``. A backslash
      *inside* the path is not rejected: ``StaticFiles.get_path`` produces
      ``assets\\app.js`` for every served asset on Windows.
    * an absolute or drive-qualified path (``C:\\...``, ``C:foo``).
    * NUL bytes — invalid in paths and a classic truncation trick.
    """
    if not path:
        return True
    if path.startswith(("\\", "/")):
        return True
    if os.path.isabs(path):
        return True
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        return True
    return "\x00" in path


class SafeStaticFiles(StaticFiles):
    """``StaticFiles`` that rejects unsafe paths before any filesystem call."""

    def get_path(self, scope: Scope) -> str:
        path = super().get_path(scope)
        if is_unsafe_static_path(path):
            # Same status a missing file gets; do not echo the rejected path.
            raise HTTPException(status_code=404)
        return path
