"""Plugin tools package (P0 item 3).

Drop a ``BaseTool`` subclass decorated with ``@register`` anywhere under
``backend/plugins/`` (single files or subpackages). When ``plugins_autoload``
is enabled, :func:`backend.core.tools.registry.discover_plugins` imports every
``*.py`` file recursively and the tool becomes available to the LLM.

Note: this ``__init__.py`` is intentionally empty — plugin modules are loaded
standalone via ``importlib`` and must use absolute imports (``from backend...``)
instead of relative imports.
"""
