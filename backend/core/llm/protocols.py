"""Provider protocol vocabulary shared by the store, factory and adapters.

D-34 keeps ``protocol`` on the provider card; D-35 adds the second adapter.
The store persists these values, the factory dispatches on them, and the API
write face uses :data:`SUPPORTED_PROTOCOLS` for its readable 400. Keeping the
vocabulary in ``core/llm`` avoids the store (services) and the clients (core)
importing each other for a handful of strings.
"""

from __future__ import annotations

PROTOCOL_OPENAI = "openai-compatible"
PROTOCOL_ANTHROPIC = "anthropic"
SUPPORTED_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

#: Protocols with a working client adapter (D-35). Kept separate from
#: SUPPORTED_PROTOCOLS so a stored-but-not-yet-wired protocol has one explicit
#: place to be held back; as of PR B both supported protocols are implemented.
IMPLEMENTED_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

#: D-36: the documented default when neither the entry nor the provider sets one.
DEFAULT_TEMPERATURE = 0.2
