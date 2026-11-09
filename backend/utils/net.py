"""Process-wide NO_PROXY sanitation for the outbound HTTP clients.

httpx expands every ``NO_PROXY`` entry into a proxy mount key
(``all://*<host>``, occasionally ``all://[<ipv6>]``). A bracketed IPv6 entry
such as ``[::1]`` is not the host shape httpx expects, so constructing any
client that reads the environment (the OpenAI SDK does) raises
``InvalidURL: Invalid port: ':1]'`` and the backend dies at startup. NO_PROXY
is a host list, not a URL: a bare ``::1`` is what httpx's own IPv6 detection
wants, so the brackets are dropped once before clients exist.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

_PROXY_KEYS = ("NO_PROXY", "no_proxy")


def normalize_no_proxy(env: MutableMapping[str, str] | None = None) -> bool:
    """Rewrite bracketed IPv6 NO_PROXY entries in place; True when changed.

    Only values carrying ``[`` are rebuilt, so a clean entry list is left
    byte-for-byte. Brackets become the bare host httpx detects as IPv6, and a
    ``[host]:port`` suffix drops its port: httpx's environment matcher only
    compares hosts, and keeping the digits would turn the entry into a
    different address. Both env spellings are handled because ``os.environ`` on
    Windows is case-insensitive while the child-process contract and httpx's
    own lookup are not guaranteed to be.
    """
    target = os.environ if env is None else env
    changed = False
    for name in _PROXY_KEYS:
        raw = target.get(name)
        if not raw or "[" not in raw:
            continue
        entries: list[str] = []
        for piece in raw.split(","):
            item = piece.strip()
            if not item:
                continue
            if item.startswith("[") and "]" in item[1:]:
                close = item.index("]")
                host = item[1:close]
                if not host:
                    continue
                tail = item[close + 1 :]
                if tail.startswith(":") and tail[1:].isdigit():
                    tail = ""
                item = host + tail
            entries.append(item)
        cleaned = ",".join(entries)
        if cleaned != raw:
            target[name] = cleaned
            changed = True
    return changed
