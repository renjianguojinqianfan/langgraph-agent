"""Session-wide Windows console hiding for test child processes (#356).

``install_hidden_popen`` swaps ``subprocess.Popen`` for a thin subclass that
merges ``CREATE_NO_WINDOW`` into every call's ``creationflags``. ``run`` /
``call`` / ``check_output`` / ``check_call`` look ``Popen`` up through the
``subprocess`` module at call time, so one patch covers every test helper —
including the ones that spawn ``sys.executable`` directly.

Only Windows installs the patch; on other platforms ``install_hidden_popen``
returns an inert object and ``subprocess`` is untouched. ``restore`` puts the
exact original class back, so an in-process rerun of the suite cannot inherit a
patched spawner.
"""

from __future__ import annotations

import subprocess
from typing import Any, Optional

from backend.utils import hidden_process


def apply_hidden_flags(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Merge the hidden-console flag into a Popen kwargs dict, in place.

    Caller-supplied ``creationflags`` are preserved; non-Windows returns the
    dict unchanged (the key is not added), so behavior is identical there.
    """
    flags = int(kwargs.get("creationflags") or 0)
    hidden = hidden_process.hidden_creationflags(flags)
    if hidden != flags:
        kwargs["creationflags"] = hidden
    return kwargs


class HiddenPopenPatch:
    """One restorable ``subprocess.Popen`` substitution."""

    def __init__(self) -> None:
        self._original: Optional[type[subprocess.Popen[Any]]] = None

    @property
    def installed(self) -> bool:
        return self._original is not None

    def install(self) -> "HiddenPopenPatch":
        if self._original is not None:
            return self
        original = subprocess.Popen
        self._original = original

        def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
            original.__init__(self, *args, **apply_hidden_flags(kwargs))

        hidden = type("HiddenPopen", (original,), {"__init__": __init__})
        setattr(subprocess, "Popen", hidden)
        return self

    def restore(self) -> None:
        if self._original is None:
            return
        setattr(subprocess, "Popen", self._original)
        self._original = None


def install_hidden_popen() -> HiddenPopenPatch:
    """Install the session patch on Windows; inert elsewhere."""
    patch = HiddenPopenPatch()
    if hidden_process._IS_WINDOWS:
        patch.install()
    return patch
