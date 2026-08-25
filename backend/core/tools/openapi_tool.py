"""OpenAPI-spec-driven tool generation (P1 item 6).

Turns an OpenAPI 3.0/3.1 document (local YAML/JSON path or HTTP(S) URL) into a
list of standard :class:`BaseTool` instances — one per operation:

* :func:`load_openapi_spec` — load + parse a spec, raising
  :class:`OpenAPISpecError` on any invalid input (the caller logs a warning
  and continues startup);
* :func:`build_tools_from_spec` — iterate ``paths`` and produce
  :class:`OpenAPITool` instances; a single bad operation is skipped without
  aborting the rest;
* :class:`OpenAPITool` — executes one HTTP operation (path-param injection,
  query/header/body mapping, apiKey security from settings).

Security: ``apiKey`` schemes (header/query) are supported; ``basic`` is a
documented placeholder. ``openapi_api_key`` / ``openapi_global_headers`` come
from :class:`~backend.config.Settings`. Calls never raise — they return a
:class:`ToolResult` with ``success=False`` and the HTTP status on 4xx/5xx.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult

logger = get_logger("tool.openapi")

#: Methods considered safe (not confirmed) — informational reads only.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

#: Security scheme types we can actually apply in P1.
_SUPPORTED_SECURITY = {"apiKey"}


class OpenAPISpecError(Exception):
    """Raised when an OpenAPI spec cannot be loaded / parsed / used."""


def _normalize_operation_id(operation_id: str, method: str, path: str) -> str:
    """Produce a unique, schema-safe tool name for an operation."""
    if operation_id:
        name = str(operation_id).strip()
        name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
        if name:
            return name
    raw = f"{method}_{path}"
    name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    return name.strip("_") or f"{method}_root"


def _load_local(path: Path) -> str:
    if not path.exists():
        raise OpenAPISpecError(f"spec file not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_url(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (admin-provided URL)
            return resp.read().decode("utf-8")
    except Exception as exc:
        raise OpenAPISpecError(f"failed to fetch spec URL {url}: {exc}") from exc


def _parse_spec_text(raw: str, source: str) -> Dict[str, Any]:
    text = (raw or "").lstrip()
    if not text:
        raise OpenAPISpecError(f"spec is empty: {source}")
    if text.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except Exception as exc:
            raise OpenAPISpecError(f"invalid JSON spec: {exc}") from exc
    else:
        try:
            import yaml

            data = yaml.safe_load(text)
        except Exception as exc:
            raise OpenAPISpecError(f"invalid YAML spec: {exc}") from exc
    if not isinstance(data, dict):
        raise OpenAPISpecError("spec root must be a mapping (object)")
    return data


def load_openapi_spec(source: str) -> Dict[str, Any]:
    """Load an OpenAPI spec from a local YAML/JSON path or an HTTP(S) URL."""
    source = (source or "").strip()
    if not source:
        raise OpenAPISpecError("empty spec source")
    if source.startswith(("http://", "https://")):
        raw = _load_url(source)
    else:
        raw = _load_local(Path(source))
    spec = _parse_spec_text(raw, source)
    if "paths" not in spec or not isinstance(spec.get("paths"), dict):
        raise OpenAPISpecError("spec missing a 'paths' mapping")
    return spec


def _server_url(spec: Dict[str, Any]) -> str:
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url", "")).rstrip("/")
        # Substitute simple server variables with defaults.
        variables = servers[0].get("variables") or {}
        for key, var in variables.items():
            if isinstance(var, dict) and "default" in var:
                url = url.replace("{" + key + "}", str(var["default"]))
        return url
    return ""


def _operation_parameters(path_item: Dict[str, Any], operation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Merge path-level and operation-level parameters (op wins by name/in)."""
    merged: Dict[str, Dict[str, Any]] = {}
    for p in list(path_item.get("parameters") or []) + list(operation.get("parameters") or []):
        if not isinstance(p, dict):
            continue
        key = f"{p.get('in', '')}:{p.get('name', '')}"
        merged[key] = p
    return list(merged.values())


def _schema_to_json_schema(schema: Any) -> Dict[str, Any]:
    """Best-effort conversion of an OpenAPI schema object to JSON Schema."""
    if not isinstance(schema, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in ("type", "enum", "items", "description", "default", "format", "minimum", "maximum"):
        if key in schema:
            out[key] = schema[key]
    return out


def _build_args_schema(
    parameters: List[Dict[str, Any]],
    body_schema: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for p in parameters:
        name = p.get("name")
        if not name:
            continue
        prop = _schema_to_json_schema(p.get("schema") or {})
        if not prop:
            prop = {"type": "string"}
        prop["description"] = p.get("description") or f"{p.get('in')} parameter {name}"
        properties[name] = prop
        if p.get("required"):
            required.append(name)
    if body_schema is not None:
        properties["body"] = body_schema
        # The request body is required when the operation marks it so; keep it
        # optional otherwise to avoid forcing callers to always pass one.
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _security_schemes(spec: Dict[str, Any]) -> Dict[str, Any]:
    components = spec.get("components") or {}
    return components.get("securitySchemes") or {}


class OpenAPITool(BaseTool):
    """A generated tool that executes one OpenAPI operation via httpx."""

    # Generated tools are network calls; retries are left to the caller's
    # judgement, so default to deterministic single-shot behaviour.
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def __init__(
        self,
        *,
        name: str,
        description: str,
        args_schema: Dict[str, Any],
        method: str,
        path: str,
        parameters: List[Dict[str, Any]],
        security: List[Dict[str, Any]],
        security_schemes: Dict[str, Any],
        server_url: str = "",
        api_key: str = "",
        global_headers: Optional[Dict[str, str]] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        super().__init__(settings)
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self._method = method.upper()
        self._path = path
        self._parameters = parameters
        self._security = security or []
        self._security_schemes = security_schemes or {}
        self._server_url = server_url
        self._api_key = api_key or ""
        self._global_headers = dict(global_headers or {})
        self._body_param = "body" if any(p.get("in") == "body" for p in parameters) else "body"

    # ── helpers ──
    def _url_for(self, kwargs: Dict[str, Any]) -> str:
        url = f"{self._server_url}{self._path}" if self._server_url else self._path
        for p in self._parameters:
            if p.get("in") != "path":
                continue
            name = p["name"]
            val = kwargs.get(name)
            if val is not None:
                url = url.replace("{" + name + "}", str(val))
        return url

    def _apply_security(self, headers: Dict[str, str], query: Dict[str, Any]) -> Optional[str]:
        """Apply apiKey security; returns an error string when a key is missing."""
        for sec in self._security:
            if not isinstance(sec, dict):
                continue
            for scheme_name in sec:
                scheme = self._security_schemes.get(scheme_name) or {}
                if scheme.get("type") not in _SUPPORTED_SECURITY:
                    continue  # basic / oauth2 placeholder: skip
                loc = scheme.get("in", "header")
                key_name = scheme.get("name", "X-API-Key")
                if self._api_key:
                    if loc == "query":
                        query[key_name] = self._api_key
                    else:
                        headers[key_name] = self._api_key
                else:
                    return f"apiKey '{scheme_name}' requires openapi_api_key but none is configured"
        return None

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            url = self._url_for(kwargs)
            query: Dict[str, Any] = {}
            headers: Dict[str, str] = dict(self._global_headers)
            for p in self._parameters:
                name = p["name"]
                loc = p.get("in")
                val = kwargs.get(name)
                if val is None:
                    if p.get("required"):
                        return ToolResult(success=False, error=f"missing required parameter: {name}")
                    continue
                if loc == "query":
                    query[name] = val
                elif loc == "header":
                    headers[name] = str(val)
            body = kwargs.get("body")
            sec_error = self._apply_security(headers, query)
            if sec_error:
                return ToolResult(success=False, error=sec_error)
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.request(
                    method=self._method,
                    url=url,
                    params=query or None,
                    headers=headers or None,
                    json=body if body is not None else None,
                )
                try:
                    payload: Any = resp.json()
                except Exception:
                    payload = resp.text
                return ToolResult(
                    success=resp.status_code < 400,
                    data={"status_code": resp.status_code, "body": payload},
                    error="" if resp.status_code < 400 else f"HTTP {resp.status_code}",
                )
        except Exception as exc:
            logger.exception("OpenAPITool %s failed", self.name)
            return ToolResult(success=False, error=str(exc))


def build_tools_from_spec(
    spec: Dict[str, Any],
    settings: Optional[Settings] = None,
) -> List[OpenAPITool]:
    """Generate one :class:`OpenAPITool` per operation in ``spec``.

    A single malformed operation is skipped with a warning — it never aborts
    the remaining operations.
    """
    if not isinstance(spec, dict) or "paths" not in spec:
        raise OpenAPISpecError("spec missing 'paths'")
    tools: List[OpenAPITool] = []
    server_url = _server_url(spec)
    security_schemes = _security_schemes(spec)
    api_key = settings.openapi_api_key if settings else ""
    try:
        global_headers = json.loads(settings.openapi_global_headers) if settings and settings.openapi_global_headers else {}
    except Exception:
        global_headers = {}
        logger.warning("openapi_global_headers is not valid JSON; ignoring.")

    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "delete", "patch", "head", "options"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            try:
                parameters = _operation_parameters(path_item, operation)
                body_schema: Optional[Dict[str, Any]] = None
                request_body = operation.get("requestBody")
                if isinstance(request_body, dict):
                    content = request_body.get("content") or {}
                    for media_type in ("application/json", "application/*+json", "*/*"):
                        if media_type in content and isinstance(content[media_type], dict):
                            body_schema = _schema_to_json_schema(
                                (content[media_type].get("schema") or {}).get("properties")
                                if isinstance(content[media_type].get("schema"), dict)
                                and isinstance(content[media_type]["schema"].get("properties"), dict)
                                else content[media_type].get("schema")
                            )
                            break
                    if body_schema:
                        parameters.append({"name": "body", "in": "body", "schema": body_schema, "required": bool(request_body.get("required"))})
                name = _normalize_operation_id(operation.get("operationId", ""), method, path)
                description = (
                    operation.get("summary")
                    or operation.get("description")
                    or f"{method.upper()} {path}"
                )
                args_schema = _build_args_schema(
                    parameters,
                    body_schema,
                )
                tool = OpenAPITool(
                    name=name,
                    description=str(description),
                    args_schema=args_schema,
                    method=method,
                    path=path,
                    parameters=parameters,
                    security=operation.get("security") or spec.get("security") or [],
                    security_schemes=security_schemes,
                    server_url=server_url,
                    api_key=api_key,
                    global_headers=global_headers,
                    settings=settings,
                )
                tools.append(tool)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("skipping OpenAPI operation %s %s: %s", method.upper(), path, exc)
    return tools
