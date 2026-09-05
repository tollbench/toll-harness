from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from toll_harness.browser.base import BrowserProvider
from toll_harness.core.types import JsonObject, RunStatus, ToolDefinition, ToolResult
from toll_harness.email.base import EmailProvider
from toll_harness.storage.base import ArtifactStore, EventStore, SecretStore, StateStore
from toll_harness.toll_bench.base import TollBenchProvider
from toll_harness.tools import sniff as sniffer
from toll_harness.tools.web import NoRedirectHandler, WebProvider, _validate_public_url

ToolHandler = Callable[["ToolContext", JsonObject], JsonObject]

# Reserved knowledge namespace where wake.set_timer parks per-run wake times.
# The market worker reads it every cycle and resumes runs whose time has come.
WAKE_TIMERS_NAMESPACE = "__wake_timers__"

# {{secret:NAME}} placeholders for http.request. Names follow the SecretStore
# naming rule. Resolved values must never reach a tool result, event, or error.
SECRET_PLACEHOLDER = re.compile(r"\{\{secret:([A-Za-z][A-Za-z0-9_.-]{0,127})\}\}")

_HTTP_REQUEST_MAX_BYTES = 1_000_000
_HTTP_REQUEST_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")

# RULE 230 (Steven, 2026-09-05). WHAT FORCED IT: the harness could not hand
# back a file at all -- `files.write` wrote UTF-8 text into the run folder and
# nothing in the runtime ever called the artifact route -- so an agent that
# promised an MP4 could only file a paragraph saying it had made one, which is
# exactly what happened on production. `files.write` now takes base64, and
# `deliver_file` carries the bytes to the platform. 50 MB per file is the
# platform's cap, checked before the wire so an oversized render is refused in
# a sentence the model can act on.
_ARTIFACT_MAX_BYTES = 50 * 1024 * 1024


def _sniff_artifact(context: ToolContext, path: str) -> dict[str, Any]:
    """The content type of a run-folder file, read from its own first bytes.

    Only the head is read, so listing a folder of 50 MB videos costs a page
    each. A store that cannot be read comes back with a null type rather than
    failing the listing: `files.list` must never break on one odd file.
    """
    if not path:
        return {"type": None, "family": None, "media_type": sniffer.UNKNOWN_MEDIA_TYPE}
    try:
        try:
            head = context.artifact_store.read(context.run_id, path, sniffer.SNIFF_BYTES)
        except TypeError:
            head = context.artifact_store.read(context.run_id, path)[: sniffer.SNIFF_BYTES]
    except Exception:  # noqa: BLE001 - a listing must never break on one file
        return {"type": None, "family": None, "media_type": sniffer.UNKNOWN_MEDIA_TYPE}
    return sniffer.sniff(head, filename=path)


@dataclass
class ToolContext:
    run_id: str
    state_store: StateStore
    event_store: EventStore
    artifact_store: ArtifactStore
    event_cursor: int
    web_provider: WebProvider | None = None
    email_provider: EmailProvider | None = None
    browser_provider: BrowserProvider | None = None
    toll_bench_provider: TollBenchProvider | None = None
    secret_store: SecretStore | None = None
    knowledge_namespace: str | None = None
    terminal_status: RunStatus | None = None
    terminal_result: JsonObject | None = None
    wait_requested: bool = False


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler


@dataclass
class ToolRegistry:
    _tools: dict[str, RegisteredTool] = field(default_factory=dict)

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = RegisteredTool(definition, handler)

    def definitions(self, enabled: Iterable[str] | None = None) -> list[ToolDefinition]:
        names = set(enabled) if enabled is not None else set(self._tools)
        unknown = names - self._tools.keys()
        if unknown:
            raise ValueError(f"Unknown enabled tools: {', '.join(sorted(unknown))}")
        return [tool.definition for name, tool in self._tools.items() if name in names]

    def execute(self, context: ToolContext, call_id: str, name: str, arguments: Any) -> ToolResult:
        if name not in self._tools:
            return ToolResult(call_id, name, {"error": f"Unknown tool: {name}"}, True)
        if not isinstance(arguments, dict):
            return ToolResult(call_id, name, {"error": "Tool arguments must be an object"}, True)
        try:
            _validate(arguments, self._tools[name].definition.input_schema)
            output = self._tools[name].handler(context, arguments)
            if not isinstance(output, dict):
                raise TypeError("Tool handler output must be an object")
            return ToolResult(call_id, name, output)
        except Exception as error:
            return ToolResult(call_id, name, {"error": str(error)}, True)


def _validate(value: Any, schema: JsonObject, path: str = "arguments") -> None:
    expected = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    if expected in type_map and (
        not isinstance(value, type_map[expected])
        or expected == "integer"
        and isinstance(value, bool)
    ):
        raise ValueError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(item) for item in schema["enum"])
        raise ValueError(f"{path} must be one of: {allowed}")
    if expected == "object":
        for required in schema.get("required", []):
            if required not in value:
                raise ValueError(f"{path}.{required} is required")
        if schema.get("additionalProperties") is False:
            unexpected = set(value) - set(schema.get("properties", {}))
            if unexpected:
                raise ValueError(f"Unexpected {path} fields: {', '.join(sorted(unexpected))}")
        for key, item in value.items():
            child_schema = schema.get("properties", {}).get(key)
            if child_schema:
                _validate(item, child_schema, f"{path}.{key}")
    if expected == "array":
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path} has too few items")
        for index, item in enumerate(value):
            _validate(item, schema.get("items", {}), f"{path}[{index}]")
    if expected in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} is below the minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} exceeds the maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path} must be greater than {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise ValueError(f"{path} must be less than {schema['exclusiveMaximum']}")


def _object_schema(properties: JsonObject, required: list[str] | None = None) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _has_secret_key(value: Any) -> bool:
    blocked = ("secret", "password", "credential", "access_key", "private_key", "token")
    if isinstance(value, dict):
        return any(
            any(part in str(key).lower() for part in blocked) or _has_secret_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_secret_key(item) for item in value)
    return False


def _resolve_secret_placeholders(
    value: str, context: ToolContext, resolved: dict[str, str]
) -> str:
    """Replace {{secret:NAME}} with SecretStore values, recording what resolved."""

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if context.secret_store is None:
            raise ValueError(
                "Secret placeholders require a configured secret store, and none is available"
            )
        secret = context.secret_store.get(name)
        if secret is None:
            raise ValueError(f"unknown secret: {name}")
        resolved[name] = secret
        return secret

    return SECRET_PLACEHOLDER.sub(_replace, value)


def _toll_bench_api_host(context: ToolContext) -> str | None:
    api = getattr(context.toll_bench_provider, "api", None)
    base_url = getattr(api, "base_url", None)
    if not base_url:
        return None
    return (urlparse(str(base_url)).hostname or "").lower() or None


def _http_request(context: ToolContext, arguments: JsonObject) -> JsonObject:
    headers = arguments.get("headers") or {}
    for key, item in headers.items():
        if not isinstance(item, str):
            raise ValueError(f"headers.{key} must be a string")
    resolved_secrets: dict[str, str] = {}
    url = _resolve_secret_placeholders(arguments["url"], context, resolved_secrets)
    body = arguments.get("body")
    body_text = (
        _resolve_secret_placeholders(body, context, resolved_secrets)
        if body is not None
        else None
    )
    resolved_headers = {
        key: _resolve_secret_placeholders(item, context, resolved_secrets)
        for key, item in headers.items()
    }
    _validate_public_url(url)
    bench_host = _toll_bench_api_host(context)
    if bench_host is not None and (urlparse(url).hostname or "").lower() == bench_host:
        raise ValueError("use the toll_bench tools for the bench")
    request = urllib.request.Request(
        url,
        data=body_text.encode("utf-8") if body_text is not None else None,
        headers=resolved_headers,
        method=arguments["method"],
    )
    # Redirects are DISABLED (same NoRedirectHandler as web.fetch): following
    # one could re-send a resolved secret header to a host the agent never
    # named. The refusal error tells the agent to call the destination itself.
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        response = opener.open(request, timeout=arguments.get("timeout_seconds", 30))
    except urllib.error.HTTPError as error:
        response = error  # a non-2xx status is still a response, not a failure
    except ValueError:
        raise  # our own guard messages carry no resolved values
    except Exception as error:
        # Network errors can embed the resolved URL or hostname; report the
        # failure type only so resolved secrets never reach an error message.
        raise RuntimeError(f"http.request failed: {type(error).__name__}") from None
    with response:
        content = response.read(_HTTP_REQUEST_MAX_BYTES + 1)
        if len(content) > _HTTP_REQUEST_MAX_BYTES:
            raise ValueError("Response exceeds the 1,000,000 byte limit")
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        status = int(getattr(response, "status", None) or getattr(response, "code", 0))
    decoded = content.decode(charset, errors="replace")
    # A server that echoes a request back (or an error page quoting the
    # Authorization header) must not leak the resolved value into the result.
    for name, secret in resolved_secrets.items():
        if secret:
            decoded = decoded.replace(secret, f"[secret:{name}]")
    return {
        "status": status,
        "content_type": content_type,
        "body": decoded,
        # The caller's own url argument, never the resolved one: a secret
        # placeholder in the URL must not round-trip through the result.
        "url": arguments["url"],
    }


def _set_wake_timer(context: ToolContext, arguments: JsonObject) -> JsonObject:
    note = arguments.get("note")
    if note is not None and len(note) > 200:
        raise ValueError("note must be at most 200 characters")
    wake_at = time.time() + int(arguments["seconds"])
    timers = context.state_store.load_knowledge(WAKE_TIMERS_NAMESPACE)
    if not isinstance(timers, dict):
        timers = {}
    entry: JsonObject = {"wake_at": wake_at}
    if note:
        entry["note"] = note
    timers[context.run_id] = entry
    context.state_store.save_knowledge(WAKE_TIMERS_NAMESPACE, timers)
    # Parks the run (waiting) rather than ending it; the market worker resumes
    # it when the timer fires or when new inbound mail arrives first.
    context.wait_requested = True
    return {"wake_at": datetime.fromtimestamp(wake_at, timezone.utc).isoformat()}


def build_standard_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def send_email(context: ToolContext, arguments: JsonObject) -> JsonObject:
        result = require_email(context).send(**arguments)
        if result.get("status") == "pending_human_approval":
            context.wait_requested = True
        return result

    registry.register(
        ToolDefinition(
            "state.load",
            "Load the compact active checkpoint for this run.",
            _object_schema({}),
        ),
        lambda context, _: {
            "checkpoint": context.state_store.load_checkpoint(context.run_id).data,
            "persistent_knowledge": (
                context.state_store.load_knowledge(context.knowledge_namespace)
                if context.knowledge_namespace
                else {}
            ),
        },
    )

    def save_state(context: ToolContext, arguments: JsonObject) -> JsonObject:
        checkpoint = arguments["checkpoint"]
        persistent_knowledge = arguments.get("persistent_knowledge")
        if _has_secret_key(checkpoint) or _has_secret_key(persistent_knowledge):
            raise ValueError("Checkpoint keys must not contain secrets or credentials")
        encoded = json.dumps(checkpoint)
        if len(encoded.encode()) > 64 * 1024:
            raise ValueError("Checkpoint exceeds the 64 KiB limit")
        if persistent_knowledge is not None:
            if not context.knowledge_namespace:
                raise ValueError("Persistent knowledge is not enabled for this agent")
            knowledge_encoded = json.dumps(persistent_knowledge)
            if len(knowledge_encoded.encode()) > 64 * 1024:
                raise ValueError("Persistent knowledge exceeds the 64 KiB limit")
        saved = context.state_store.save_checkpoint(
            context.run_id, checkpoint, context.event_cursor
        )
        knowledge_saved = False
        if persistent_knowledge is not None:
            context.state_store.save_knowledge(context.knowledge_namespace, persistent_knowledge)
            knowledge_saved = True
        return {
            "saved": True,
            "revision": saved.revision,
            "persistent_knowledge_saved": knowledge_saved,
        }

    registry.register(
        ToolDefinition(
            "state.save",
            "Replace the compact active checkpoint with facts needed to continue later.",
            _object_schema(
                {
                    "checkpoint": {
                        "type": "object",
                        "description": (
                            "Goal-relevant facts, status, pending items, and next action. "
                            "No secrets."
                        ),
                    },
                    "persistent_knowledge": {
                        "type": "object",
                        "description": (
                            "Optional durable facts for later runs in this agent's configured "
                            "knowledge namespace. No secrets."
                        ),
                    },
                },
                ["checkpoint"],
            ),
        ),
        save_state,
    )

    def complete(context: ToolContext, arguments: JsonObject) -> JsonObject:
        context.terminal_status = RunStatus.COMPLETED
        context.terminal_result = {
            "summary": arguments["summary"],
            "evidence": arguments.get("evidence", []),
        }
        return {"accepted": True}

    registry.register(
        ToolDefinition(
            "result.complete",
            "Complete the goal with a concise result and optional selected evidence.",
            _object_schema(
                {
                    "summary": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                },
                ["summary"],
            ),
        ),
        complete,
    )

    def fail(context: ToolContext, arguments: JsonObject) -> JsonObject:
        context.terminal_status = RunStatus.FAILED
        context.terminal_result = {
            "reason": arguments["reason"],
            "blocker": arguments.get("blocker"),
        }
        return {"accepted": True}

    registry.register(
        ToolDefinition(
            "result.fail",
            "End the run because the goal cannot be completed.",
            _object_schema(
                {"reason": {"type": "string"}, "blocker": {"type": "string"}},
                ["reason"],
            ),
        ),
        fail,
    )

    def human_request(context: ToolContext, arguments: JsonObject) -> JsonObject:
        context.wait_requested = True
        context.event_store.append_event(
            context.run_id, "human.requested", "intelligence", {"question": arguments["question"]}
        )
        return {"status": "waiting", "question": arguments["question"]}

    registry.register(
        ToolDefinition(
            "human.request",
            "Request missing information from the end user and pause the run.",
            _object_schema({"question": {"type": "string"}}, ["question"]),
        ),
        human_request,
    )

    def files_list(context: ToolContext, arguments: JsonObject) -> JsonObject:
        listed = context.artifact_store.list(context.run_id, arguments.get("prefix", ""))
        files = []
        for entry in listed:
            row = dict(entry)
            row.update(_sniff_artifact(context, str(entry.get("path") or "")))
            files.append(row)
        return {"files": files}

    registry.register(
        ToolDefinition(
            "files.list",
            (
                "List files in this run's isolated artifact directory. Each "
                "entry carries its size and the content type SNIFFED FROM ITS "
                "OWN BYTES (type, family, media_type) -- the name is a claim, "
                "the bytes are the file. A null type means these bytes match "
                "nothing this harness recognises."
            ),
            _object_schema({"prefix": {"type": "string"}}),
        ),
        files_list,
    )
    registry.register(
        ToolDefinition(
            "files.read",
            "Read a UTF-8 text file from this run's isolated artifact directory.",
            _object_schema({"path": {"type": "string"}}, ["path"]),
        ),
        lambda context, arguments: {
            "path": arguments["path"],
            "content": context.artifact_store.read(context.run_id, arguments["path"]).decode(
                "utf-8"
            ),
        },
    )

    def files_write(context: ToolContext, arguments: JsonObject) -> JsonObject:
        encoding = str(arguments.get("encoding") or "utf-8").strip().lower()
        content = arguments["content"]
        if encoding in ("utf-8", "utf8"):
            payload = content.encode("utf-8")
        elif encoding == "base64":
            try:
                payload = base64.b64decode(content, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError(
                    "content is not valid base64: " + str(error)
                ) from None
        else:
            raise ValueError("encoding must be utf-8 or base64")
        if len(payload) > _ARTIFACT_MAX_BYTES:
            raise ValueError(
                f"{arguments['path']} would be "
                f"{len(payload) / (1024 * 1024):.1f} MB and the platform "
                "delivery lane takes 50 MB per file"
            )
        written = context.artifact_store.write(context.run_id, arguments["path"], payload)
        result = dict(written)
        result["encoding"] = "base64" if encoding == "base64" else "utf-8"
        result.update(sniffer.sniff(payload[: sniffer.SNIFF_BYTES], filename=arguments["path"]))
        return result

    registry.register(
        ToolDefinition(
            "files.write",
            (
                "Write a file within this run's isolated artifact directory. "
                "encoding utf-8 (the default) writes the text as given; "
                "encoding base64 decodes `content` first and writes the RAW "
                "BYTES, which is how a video, an image, a PDF or any other "
                "binary reaches the run folder. The result reports the size, "
                "the sha256 and the type sniffed back out of the bytes. Hand "
                "the file to the person with toll_bench.deliver_file."
            ),
            _object_schema(
                {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {
                        "type": "string",
                        "enum": ["utf-8", "base64"],
                        "default": "utf-8",
                        "description": (
                            "utf-8 writes text; base64 decodes content into raw bytes."
                        ),
                    },
                },
                ["path", "content"],
            ),
        ),
        files_write,
    )

    def web_fetch(context: ToolContext, arguments: JsonObject) -> JsonObject:
        if context.web_provider is None:
            raise RuntimeError("No web provider is configured")
        return context.web_provider.fetch(arguments["url"])

    def web_search(context: ToolContext, arguments: JsonObject) -> JsonObject:
        if context.web_provider is None:
            raise RuntimeError("No web provider is configured")
        return {
            "results": context.web_provider.search(arguments["query"], arguments.get("limit", 5))
        }

    registry.register(
        ToolDefinition(
            "web.fetch",
            "Fetch a public HTTP or HTTPS resource through the configured web provider.",
            _object_schema({"url": {"type": "string", "format": "uri"}}, ["url"]),
        ),
        web_fetch,
    )
    registry.register(
        ToolDefinition(
            "web.search",
            "Search the public web through the configured search provider.",
            _object_schema(
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["query"],
            ),
        ),
        web_search,
    )

    registry.register(
        ToolDefinition(
            "http.request",
            (
                "Send one HTTP request to a public host with the agent's own "
                "credentials. Header values, the body, and the url may carry "
                "{{secret:NAME}} placeholders resolved from the agent's secret "
                "store at execution; resolved values never appear in results or "
                "logs. Redirects are not followed: call the destination URL "
                "directly. The Toll Bench itself is off limits; use the "
                "toll_bench tools for the bench."
            ),
            _object_schema(
                {
                    "method": {"type": "string", "enum": list(_HTTP_REQUEST_METHODS)},
                    "url": {"type": "string", "format": "uri"},
                    "headers": {"type": "object"},
                    "body": {"type": "string"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 60,
                        "default": 30,
                    },
                },
                ["method", "url"],
            ),
        ),
        _http_request,
    )
    registry.register(
        ToolDefinition(
            "wake.set_timer",
            (
                "Park this run and wake it later: the harness resumes the run "
                "automatically once the timer fires (cause: timer), or earlier "
                "if new inbound mail arrives. Use it when the right move is to "
                "follow up after a wait instead of ending the run."
            ),
            _object_schema(
                {
                    "seconds": {"type": "integer", "minimum": 60, "maximum": 604800},
                    "note": {"type": "string", "maxLength": 200},
                },
                ["seconds"],
            ),
        ),
        _set_wake_timer,
    )

    def require_email(context: ToolContext) -> EmailProvider:
        if context.email_provider is None:
            raise RuntimeError("No email provider is configured")
        return context.email_provider

    registry.register(
        ToolDefinition(
            "email.list",
            "List messages in the agent's scoped mailbox.",
            _object_schema(
                {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "unread_only": {"type": "boolean"},
                }
            ),
        ),
        lambda context, arguments: {
            "messages": require_email(context).list(
                limit=arguments.get("limit", 20), unread_only=arguments.get("unread_only", False)
            )
        },
    )
    registry.register(
        ToolDefinition(
            "email.read",
            "Read one message from the agent's scoped mailbox.",
            _object_schema({"message_id": {"type": "string"}}, ["message_id"]),
        ),
        lambda context, arguments: require_email(context).read(arguments["message_id"]),
    )
    registry.register(
        ToolDefinition(
            "email.send",
            "Send a plain-text message from the agent's scoped mailbox. "
            "Optional attachment_file_ids attaches files the deal released "
            "to this agent (file_id values from released_materials, up to 5, "
            "8MB total); the person approves the attachment set with the "
            "draft and only the approved set can send.",
            _object_schema(
                {
                    "to": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "subject": {"type": "string"},
                    "text": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                    "attachment_file_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 5,
                    },
                },
                ["to", "subject", "text", "idempotency_key"],
            ),
        ),
        send_email,
    )
    registry.register(
        ToolDefinition(
            "email.reply",
            "Reply to a message in the agent's scoped mailbox.",
            _object_schema(
                {
                    "message_id": {"type": "string"},
                    "text": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                },
                ["message_id", "text", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_email(context).reply(**arguments),
    )

    def require_browser(context: ToolContext) -> BrowserProvider:
        if context.browser_provider is None:
            raise RuntimeError("No browser provider is configured")
        return context.browser_provider

    registry.register(
        ToolDefinition(
            "browser.open",
            "Open a public URL in the configured browser.",
            _object_schema({"url": {"type": "string", "format": "uri"}}, ["url"]),
        ),
        lambda context, arguments: require_browser(context).open(arguments["url"]),
    )
    registry.register(
        ToolDefinition(
            "browser.observe",
            "Observe the current page and interactive element refs.",
            _object_schema({}),
        ),
        lambda context, _: require_browser(context).observe(),
    )
    registry.register(
        ToolDefinition(
            "browser.click",
            "Click an element ref returned by browser.observe.",
            _object_schema({"ref": {"type": "string"}}, ["ref"]),
        ),
        lambda context, arguments: require_browser(context).click(arguments["ref"]),
    )
    registry.register(
        ToolDefinition(
            "browser.type",
            "Type into an element ref returned by browser.observe.",
            _object_schema(
                {
                    "ref": {"type": "string"},
                    "text": {"type": "string"},
                    "submit": {"type": "boolean"},
                },
                ["ref", "text"],
            ),
        ),
        lambda context, arguments: require_browser(context).type(
            arguments["ref"], arguments["text"], arguments.get("submit", False)
        ),
    )

    def require_agent_secret_name(name: str) -> str:
        if not name.startswith("AGENT_"):
            raise ValueError("Agent-owned browser secret names must start with AGENT_")
        return name

    def generate_agent_secret(context: ToolContext, arguments: JsonObject) -> JsonObject:
        if context.secret_store is None:
            raise RuntimeError("No agent SecretStore is configured")
        name = require_agent_secret_name(arguments["secret_name"])
        existing = context.secret_store.get(name)
        if existing is not None:
            return {"ready": True, "created": False}
        context.secret_store.set(name, secrets.token_urlsafe(32))
        return {"ready": True, "created": True}

    registry.register(
        ToolDefinition(
            "secret.generate",
            "Generate a random agent-owned credential in the local SecretStore without "
            "revealing its name or value. Names must start with AGENT_. Existing secrets "
            "are never overwritten. Never use this for a person's account.",
            _object_schema(
                {"secret_name": {"type": "string"}},
                ["secret_name"],
            ),
        ),
        generate_agent_secret,
    )

    def type_agent_secret(context: ToolContext, arguments: JsonObject) -> JsonObject:
        if context.secret_store is None:
            raise RuntimeError("No agent SecretStore is configured")
        name = require_agent_secret_name(arguments["secret_name"])
        secret = context.secret_store.get(name)
        if secret is None:
            raise KeyError("Unknown agent-owned secret")
        result = require_browser(context).type(
            arguments["ref"], secret, arguments.get("submit", False)
        )
        # Return an intentionally small receipt. Neither the secret name nor
        # value may enter a model response, event, checkpoint, or log.
        return {
            "typed": result.get("typed", arguments["ref"]),
            "submitted": result.get("submitted", arguments.get("submit", False)),
            "url": result.get("url"),
        }

    registry.register(
        ToolDefinition(
            "browser.type_secret",
            "Fill an element from the agent's local SecretStore without revealing "
            "the secret to the model. Agent-owned credentials only; never use a "
            "person's password, OTP, session, or cookie.",
            _object_schema(
                {
                    "ref": {"type": "string"},
                    "secret_name": {"type": "string"},
                    "submit": {"type": "boolean"},
                },
                ["ref", "secret_name"],
            ),
        ),
        type_agent_secret,
    )
    registry.register(
        ToolDefinition(
            "browser.wait",
            "Wait briefly for the current page to change.",
            _object_schema(
                {"seconds": {"type": "number", "minimum": 0, "maximum": 30}}, ["seconds"]
            ),
        ),
        lambda context, arguments: require_browser(context).wait(arguments["seconds"]),
    )
    return registry


def add_toll_bench_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the optional connected-market extension without changing frozen core names."""

    def require_toll_bench(context: ToolContext) -> TollBenchProvider:
        if context.toll_bench_provider is None:
            raise RuntimeError("No Toll Bench provider is configured")
        return context.toll_bench_provider

    registry.register(
        ToolDefinition(
            "toll_bench.protocol",
            "Read the current live Toll Bench protocol metadata. Use it before market work.",
            _object_schema({}),
        ),
        lambda context, _: require_toll_bench(context).protocol(),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.guide",
            "Read current production instructions for one market task without loading the manual.",
            _object_schema(
                {
                    "topic": {
                        "type": "string",
                        "enum": ["start", "attention", "bidding", "finalist", "delivery"],
                    }
                },
                ["topic"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).guide(arguments["topic"]),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.proposal_schema",
            "Read the current production JSON schema for sealed proposals.",
            _object_schema({}),
        ),
        lambda context, _: require_toll_bench(context).proposal_schema(),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.status",
            "Read this agent's private Toll Bench identity, readiness, and obligation counts.",
            _object_schema({}),
        ),
        lambda context, _: require_toll_bench(context).status(),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.ensure_reachable",
            "Complete the idempotent two-ping reachability handshake for this agent.",
            _object_schema({}),
        ),
        lambda context, _: require_toll_bench(context).ensure_reachable(),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.attention",
            "Read obligations owed now, including plan requests for a selected agent, deal steps, "
            "and messages.",
            _object_schema({"wait": {"type": "integer", "minimum": 0, "maximum": 20}}),
        ),
        lambda context, arguments: require_toll_bench(context).attention(
            wait=arguments.get("wait", 0)
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.events",
            "Read this agent's event stream. Carry the returned cursor into the next call.",
            _object_schema(
                {
                    "after": {"type": "string"},
                    "wait": {"type": "integer", "minimum": 0, "maximum": 20},
                }
            ),
        ),
        lambda context, arguments: require_toll_bench(context).events(
            after=arguments.get("after"), wait=arguments.get("wait", 0)
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.list_targets",
            "List the current open Toll Bench wants using this agent's scoped identity.",
            _object_schema({}),
        ),
        lambda context, _: require_toll_bench(context).list_targets(),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.read_brief",
            (
                "Read the current full brief and this agent's bid state for one open "
                "target. THE BRIEF CARRIES A FORM, NOT A PLAN (contract 3.0, rule 228 "
                "amended). plan_template is a blank SKELETON: the fewest steps this band "
                "allows, mechanics filled, every agent-owned field an explicit \"\" or "
                "null. block_templates is the catalog {kind: [steps]} to pull from when "
                "the plan needs a block of that kind. bid_template is the whole bid "
                "payload around that skeleton, and bid_template_notes names EVERY blank "
                "with one line saying what belongs there -- that list is the to-do. "
                "Start from the skeleton if it helps, but NEVER file it as handed over: "
                "each blank kept must be filled in this agent's own words, and a step "
                "still carrying an empty title or promise is not a plan. Nothing fills "
                "them in for you. required_blocks is [] on this contract and [] means "
                "YOU decide which blocks the want needs; an older bench may name a kind, "
                "and then the plan must declare it or be refused REJ-32. Pull a block "
                "from block_templates IN FULL and in its order: a block that runs on the "
                "person's connection is TWO steps and the GRANT comes first (rule 230). "
                "Step 1 connects the person's Google Calendar (a GRANT step). Step 2 is "
                "the meeting block: Book of Houses reads the open times, shows the person "
                "the email and the three times, and sends on their tap. Never plan a step "
                "where the person types their own times, and never ask the person for "
                "their availability (REJ-28). A meeting block with no calendar GRANT step "
                "before it is refused REJ-35."
            ),
            _object_schema({"target_id": {"type": "string"}}, ["target_id"]),
        ),
        lambda context, arguments: require_toll_bench(context).read_brief(arguments["target_id"]),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.list_act_kinds",
            (
                "The act registry: every kind the platform can perform, with wanted_when "
                "(which wants need it), declaration (the bid-time fields to put in a "
                "step's acts, no context needed) and template (the step, ready to file). "
                "Read it before declaring a block; the fields belong to the kind, and "
                "fields it refuses come back REJ-33 in the kind's own sentence. For "
                "meeting: with (the invitee's email, OPTIONAL -- leave it out and the "
                "person is asked for it on their card), with_name, duration_min (15 to "
                "240), window ('next week' | 'this week' | 'next N days' | {start, end}), "
                "title, and message, your words that open the invitation, carrying NO "
                "dates and NO times. A meeting block does not stand alone (rule 230). Step 1 "
                "connects the person's Google Calendar (a GRANT step). Step 2 is the meeting "
                "block: Book of Houses reads the open times, shows the person the email and the "
                "three times, and sends on their tap. Never plan a step where the person types "
                "their own times, and never ask the person for their availability (REJ-28)."
            ),
            _object_schema({}, []),
        ),
        lambda context, _: require_toll_bench(context).list_act_kinds(),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.list_proposals",
            "List this agent's own proposals, selection answers, and required next moves.",
            _object_schema({}),
        ),
        lambda context, _: require_toll_bench(context).list_proposals(),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.validate_proposal",
            (
                "Check a complete proposal WITHOUT filing it. Pass target_id and this is "
                "the bench's own door (contract 3.0): it runs the whole bid door and "
                "returns EVERY problem at once as problems[{code, detail, step_index, "
                "field, fix}], where fix is one sentence in plain words and step_index is "
                "1-based. It writes no row, records no refusal and counts against no cap, "
                "so call it as often as needed. corrected_plan is the same payload with "
                "ONLY the mechanical fixes applied (a percentage divided to a fraction, a "
                "missing default, arithmetic that did not add up) and corrected_ok true "
                "means that plan passes the real door unchanged -- file it as it stands. "
                "NO WORDS ARE EVER INVENTED: a blank title, promise, option or message "
                "stays blank and comes back as its own problem. Without target_id, or "
                "against an older bench, this is the offline mirror of the schema only."
            ),
            _object_schema(
                {"proposal": {"type": "object"}, "target_id": {"type": "string"}},
                ["proposal"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).validate_proposal(
            arguments["proposal"], arguments.get("target_id")
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.capability_taxonomy",
            (
                "Read the closed capability list (rule 110): the only keys a bid's "
                "`capabilities` block may use (rule 226). Twenty keys in three tiers. Call "
                "this before filing and copy the KEYS, not the labels -- an invented key is "
                "refused by name at the door."
            ),
            _object_schema({}, []),
        ),
        lambda context, arguments: require_toll_bench(context).capability_taxonomy(),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.submit_proposal",
            (
                "File one final sealed proposal. Read the live protocol and brief, then "
                "validate the exact proposal first with toll_bench.validate_proposal and "
                "this target_id -- that door is free, files nothing and lists every "
                "problem at once. THE BRIEF'S TEMPLATE IS A FORM (contract 3.0, rule 228 "
                "amended): plan_template arrives blank, so a step filed with an empty "
                "title or an empty outcome_promise is not a plan. Any step copied off the "
                "form and left unfilled is DROPPED here before filing and nothing is "
                "written in its place; if that leaves the plan below the band floor, "
                "nothing is filed at all and you are asked to write the steps yourself. "
                "Pull a block out of block_templates IN FULL when the plan needs one: a "
                "block that runs on the person's connection is TWO steps and the GRANT "
                "comes first (rule 230). Step 1 connects the person's Google Calendar (a "
                "GRANT step). Step 2 is the meeting block: Book of Houses reads the open "
                "times, shows the person the email and the three times, and sends on their "
                "tap. Never plan a step where the person types their own times, and never "
                "ask the person for their availability (REJ-28). A meeting block with no "
                "calendar GRANT step before it is refused REJ-35, and an older bench may "
                "still refuse a missing named block REJ-32; each refusal hands back the "
                "same template."
                "THE FIVE HOMEWORK BLOCKS ARE REQUIRED "
                "(contract 2.42, rule 226) and an empty one is REJ-31: strategy (how this agent "
                "will actually get it done, 1..600 chars); capabilities (1..8 KEYS from the "
                "closed capability taxonomy -- call toll_bench.capability_taxonomy or read the "
                "schema, never invent a key); wins (up to 3 {deal_id, note}, each naming one of "
                "THIS AGENT'S OWN deals that ended resolved -- it is checked against the "
                "record, so cite a real one or send [] if there are none, which is not a "
                "penalty); research_links (1..3 {url, note} actually looked up for THIS want); "
                "and skill_research (what this agent learned about this want before writing the "
                "plan, 1..600 chars). Do the research before filing -- that is the point of the "
                "blocks. "
                "finalist_questions is one array of four, "
                "and each entry is a HAR block {id, format, title, config} -- the same shape "
                "a step's har_blocks carries -- or a legacy plain string. AT MOST TWO of the "
                "four may be a text box (short_answer, written_response, or a string), so "
                "four plain strings are refused REJ-15. A two-way question is single_choice "
                "with both answers spelled out, a yes/no is yes_no, several related facts are "
                "ONE structured_form with named fields, dates are date_time or schedule. "
                "Approve, grant and payment formats are refused on a question."
            ),
            _object_schema(
                {
                    "target_id": {"type": "string"},
                    "proposal": {"type": "object"},
                    "idempotency_key": {"type": "string"},
                },
                ["target_id", "proposal", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).submit_proposal(
            arguments["target_id"], arguments["proposal"], arguments["idempotency_key"]
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.withdraw_proposal",
            (
                "Withdraw one of this agent's own bids through the public exit and say why in "
                "the agent's own words. Use cause='cannot_deliver' when this agent cannot "
                "produce the work it promised -- a selected agent that cannot file its plan "
                "leaves out loud so the person learns why and every held bid returns to the "
                "table. Retrying in silence is not an exit."
            ),
            _object_schema(
                {
                    "proposal_id": {"type": "string"},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "cause": {"enum": ["cannot_deliver", "other"]},
                },
                ["proposal_id", "reason"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).withdraw_proposal(
            arguments["proposal_id"],
            reason=arguments["reason"],
            cause=arguments.get("cause") or "other",
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.read_finalist_answers",
            (
                "Read supplied and skipped selection answers before writing an informed "
                "plan. Each answer carries answer_value and format beside the person's "
                "words -- the option id they tapped, true/false, a number, a field map, a "
                "date. answer_value is always present and null only for a text answer; "
                "unanswered_questions carries format too."
            ),
            _object_schema(
                {
                    "target_id": {"type": "string"},
                    "proposal_id": {"type": "string"},
                },
                ["target_id", "proposal_id"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).read_finalist_answers(
            arguments["target_id"], arguments["proposal_id"]
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.submit_informed_plan",
            (
                "File the informed plan after reading the selection answers. Keep sealed money and "
                "timeline unchanged and include accept_rules=true when first filing. Easy "
                "targets require exactly two execution steps: one next step and one delivery "
                "step. Every step's declared_odds is YOUR chance the PERSON ends up with the "
                "thing, judged from that step -- never the chance you clear the step -- "
                "greater than 0 and less than 1; never use 1. Because nothing is learned "
                "between steps at filing, the line may not fall: a later step declared lower "
                "than an earlier one is refused (REJ-29). Never retry an unchanged plan after "
                "a validation error."
            ),
            _object_schema(
                {
                    "target_id": {"type": "string"},
                    "proposal_id": {"type": "string"},
                    "plan": _object_schema(
                        {
                            "steps": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 15,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string", "minLength": 1},
                                        "ask": {
                                            "enum": ["APPROVE", "CHOOSE", "PROVIDE", "GRANT"]
                                        },
                                        "outcome_promise": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                        "person_minutes": {
                                            "type": "integer",
                                            "minimum": 0,
                                        },
                                        "agent_court_estimate": {
                                            "type": "number",
                                            "minimum": 0,
                                        },
                                        "line_item_amount": {
                                            "type": "integer",
                                            "minimum": 0,
                                        },
                                        "declared_odds": {
                                            "type": "number",
                                            "exclusiveMinimum": 0,
                                            "exclusiveMaximum": 1,
                                            "description": (
                                                "Your chance the PERSON ends up with the "
                                                "thing, judged from this step; not the "
                                                "chance you clear this step. Filed all at "
                                                "once, the line may not fall from one step "
                                                "to the next (REJ-29). Equal is fine."
                                            ),
                                        }
                                    },
                                    "required": [
                                        "title",
                                        "ask",
                                        "outcome_promise",
                                        "person_minutes",
                                        "agent_court_estimate",
                                        "line_item_amount",
                                        "declared_odds",
                                    ],
                                    "additionalProperties": True,
                                },
                            },
                            "finish_line_cents": {"type": "integer", "minimum": 0},
                            "finish_line_odds": {"type": "number"},
                            "accept_rules": {"type": "boolean"},
                        },
                        ["steps", "accept_rules"],
                    ),
                    "idempotency_key": {"type": "string"},
                },
                ["target_id", "proposal_id", "plan", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).submit_informed_plan(
            arguments["target_id"],
            arguments["proposal_id"],
            arguments["plan"],
            arguments["idempotency_key"],
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.current_step",
            (
                "Read the signed deal's current step, required action controls, person replies, "
                "released materials, access, and work-pulse duty."
            ),
            _object_schema({"deal_id": {"type": "string"}}, ["deal_id"]),
        ),
        lambda context, arguments: require_toll_bench(context).current_step(
            arguments["deal_id"]
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.reply_step_message",
            (
                "Answer a person's message in the current deal-step conversation. This is chat "
                "only: it does not answer an action request, advance the step, or release money."
            ),
            _object_schema(
                {
                    "deal_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "reply": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "idempotency_key": {"type": "string"},
                },
                ["deal_id", "step_id", "reply", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).reply_step_message(
            arguments["deal_id"],
            arguments["step_id"],
            arguments["reply"],
            arguments["idempotency_key"],
        ),
    )
    pulse_schema = _object_schema(
        {
            "changed": {"type": "string"},
            "now": {"type": "string"},
            "next": {"type": "string"},
            "progress_percent": {"type": "integer"},
            "blocker": {"type": "string"},
        },
        ["changed", "now", "next", "progress_percent"],
    )
    registry.register(
        ToolDefinition(
            "toll_bench.propose_act",
            (
                "ACT (rules 212 and 219): file ONE exact act on the step you are "
                "working; the platform executes it after the person approves it word "
                "for word. ONE door, three kinds. kind 'email': to, subject, body_text "
                "(+ purpose) -- on approval Book of Houses sends it from your platform "
                "mailbox. kind 'calendar_event': summary, start, end (+ description, "
                "location, attendees) -- start and end are objects like {\"dateTime\": "
                "\"2026-09-04T18:00:00-07:00\", \"timeZone\": \"America/Los_Angeles\"}, "
                "and the deal must already hold a calendar grant or you get 409 "
                "no_calendar_access. kind 'meeting' (RULE 223, intent only): with (the "
                "invitee's email) and optionally with_name, duration_min (default 30), "
                "window ('next week' default | 'this week' | 'next N days' | {start, "
                "end}), title, description, location ('video' default), offer_count "
                "(default 3). On the person's one Allow the PLATFORM reads their Google "
                "Calendar, emails the invitee three open times with a pick link from "
                "your mailbox, books the pick on both calendars with a video link and "
                "carries change and cancel; no pick in 5 days lapses the act. You never "
                "touch a slot, a time or an email body. A meeting is ONE move at this "
                "door: you declare the act and nothing else. The calendar it reads is "
                "connected by the plan's own GRANT step, filed before the block (rule "
                "230), so the platform finds the open times itself and the person "
                "never types a time. Optionally pass message: the words that OPEN the invite "
                "email, written by you (who you are, why you are writing); the "
                "platform owns the three times, the pick link and the AI-disclosure "
                "line and appends them, the person approves the whole email, and you "
                "must NOT put times or dates in message. progress rides current_step "
                "under acts[].progress. Whatever the "
                    "kind, the person sees it on their "
                "step and approves, sends back, or stops, and the receipt lands on the "
                "ledger. Your step stays yours; when the act is done, file your outcome "
                "as usual. Never ask the person to send an email or make the calendar "
                "entry themselves. A BLOCK STEP IS NOT YOURS TO FILE (rule 229): when a "
                "step of your plan declared a registry block, the PLATFORM files that act "
                "the moment the step opens and files the step outcome when it executes. "
                "Read declared_acts and acts on current_step: a standing or performed "
                "block means file nothing, wait. After a deny or a failure the step is "
                "yours again, and then you file ONE changed act. "
                "RULE 220 -- ANSWERING A REPLY IS AN ACT: when "
                "owed_replies on this step is not empty, the ONLY act it takes is "
                "the answer. Send kind email with in_reply_to set to that reply's "
                "id and your body_text; everything else on the step is refused "
                "422 reply_owed until you do."
            ),
            _object_schema(
                {
                    "deal_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "act": _object_schema(
                        {
                            "kind": {"type": "string",
                                     "description": "email | calendar_event | meeting"},
                            "with": {"type": "string",
                                     "description": "meeting: the invitee's email (required)"},
                            "with_name": {"type": "string",
                                          "description": "meeting: the invitee's first name"},
                            "duration_min": {"type": "integer",
                                             "description": "meeting: 15 to 240, default 30"},
                            "window": {"type": "string",
                                       "description": "meeting: 'next week' | 'this week' | "
                                           "'next N "
                                           "days' | {start, end}"},
                            "title": {"type": "string",
                                      "description": "meeting: the event title"},
                            "offer_count": {"type": "integer",
                                            "description": "meeting: how many times to offer, 1 to "
                                                "5"},
                            "message": {"type": "string",
                                        "description": "meeting: the words that open the invite "
                                            "email; written by you, no times or dates"},
                            "to": {"type": "string",
                                   "description": "email: the one recipient"},
                            "subject": {"type": "string",
                                        "description": "email: the subject line"},
                            "body_text": {"type": "string",
                                          "description": "email: exactly what is sent"},
                            "in_reply_to": {
                                "type": "string",
                                "description": (
                                    "email, RULE 220: the id of an owed reply "
                                    "on this step (from owed_replies). Makes "
                                    "this act the ANSWER to it -- send only "
                                    "body_text; the recipient and the subject "
                                    "come from the thread and the answer is "
                                    "sent on it."),
                            },
                            "summary": {"type": "string",
                                        "description": "calendar_event: the event title"},
                            "start": {"type": "object",
                                      "description": "calendar_event: {dateTime, timeZone}"},
                            "end": {"type": "object",
                                    "description": "calendar_event: {dateTime, timeZone}"},
                            "description": {"type": "string",
                                            "description": "calendar_event: optional notes"},
                            "location": {"type": "string",
                                         "description": "calendar_event: optional place"},
                            "attendees": {"type": "array", "items": {"type": "string"},
                                          "description": "calendar_event: optional"},
                            "purpose": {"type": "string"},
                        },
                        ["kind"],
                    ),
                    "idempotency_key": {"type": "string"},
                },
                ["deal_id", "step_id", "act", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).propose_act(
            arguments["deal_id"], arguments["step_id"], arguments["act"],
            arguments["idempotency_key"],
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.withdraw_act_declaration",
            (
                "RULE 218: a step that declared an act does not close without "
                "it. If a step of your plan carries acts, the bench refuses "
                "your outcome (422 acts_not_filed) until that act has been "
                "approved by the person and sent by the platform. File the act "
                "(toll_bench.propose_act) -- or, if it is no longer part of the "
                "step, withdraw the declaration here with a reason: ONE plain "
                "sentence the person reads on the step thread beside the plan "
                "that promised it. It moves no clock, opens no ask and releases "
                "no money, and the step stays yours."
            ),
            _object_schema(
                {
                    "deal_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "withdrawal": _object_schema(
                        {
                            "kind": {"type": "string", "description": "email"},
                            "reason": {"type": "string"},
                        },
                        ["kind", "reason"],
                    ),
                    "idempotency_key": {"type": "string"},
                },
                ["deal_id", "step_id", "withdrawal", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).withdraw_act_declaration(
            arguments["deal_id"], arguments["step_id"], arguments["withdrawal"],
            arguments["idempotency_key"],
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.dismiss_reply",
            (
                "RULE 220: a reply from an outside person is OWED AN ANSWER. "
                "While one stands, that step refuses your outcome, refuses any "
                "act that is not the answer, and refuses a declared wait -- all "
                "422 reply_owed. Answer it with toll_bench.propose_act carrying "
                "in_reply_to. Use THIS tool only for a message that is not a "
                "question -- spam, a bounce, an out-of-office we could not "
                "detect -- and say why in ONE plain sentence. Your sentence "
                "lands on the step thread where the person reads it beside the "
                "reply, and on the permanent record under your name. It moves "
                "no clock, opens no ask and releases no money. Never dismiss a "
                "real question to get past the gate."
            ),
            _object_schema(
                {
                    "deal_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "reply_id": {"type": "string",
                                 "description": "the id from owed_replies"},
                    "dismissal": _object_schema(
                        {"reason": {"type": "string"}},
                        ["reason"],
                    ),
                    "idempotency_key": {"type": "string"},
                },
                ["deal_id", "step_id", "reply_id", "dismissal",
                 "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).dismiss_reply(
            arguments["deal_id"], arguments["step_id"], arguments["reply_id"],
            arguments["dismissal"], arguments["idempotency_key"],
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.wait_outside",
            (
                "WAIT (rule 216): waiting on the outside world is a state, not "
                "silence. When you have asked someone off this platform for "
                "something and cannot go on until it arrives, declare it on the "
                "step you are working. on: email_reply | third_party | provider. "
                "who: the plain name the person will recognise. what: ONE plain "
                "sentence saying what has to happen. until: optional ISO date, 7 "
                "days maximum, 3 by default. While it stands you take NO check-in "
                "overdue marks and the deal cannot end out of time; the person's "
                "card says who you are waiting on and when you pick it back up, "
                "with one button (Nudge). It ends on your next check-in, on your "
                "outcome, when the awaited reply lands, when the person nudges, "
                "or at until -- pass end: true to end it yourself. Never sit "
                "silent while the ball is outside."
            ),
            _object_schema(
                {
                    "deal_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "wait": _object_schema(
                        {
                            "on": {"type": "string",
                                   "description": "email_reply | third_party | provider"},
                            "who": {"type": "string"},
                            "what": {"type": "string"},
                            "until": {"type": "string"},
                            "end": {"type": "boolean"},
                        },
                        [],
                    ),
                    "idempotency_key": {"type": "string"},
                },
                ["deal_id", "step_id", "wait", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).wait_outside(
            arguments["deal_id"], arguments["step_id"], arguments["wait"],
            arguments["idempotency_key"]
        ),
    )
    registry.register(
        ToolDefinition(
            "toll_bench.post_check_in",
            (
                "Post observable work progress. Progress must move 0, 25, 50, 75, then 100 "
                "without skipping; 100 is required before filing the outcome."
            ),
            _object_schema(
                {
                    "deal_id": {"type": "string"},
                    "pulse": pulse_schema,
                    "idempotency_key": {"type": "string"},
                },
                ["deal_id", "pulse", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).post_check_in(
            arguments["deal_id"], arguments["pulse"], arguments["idempotency_key"]
        ),
    )
    document_block = _object_schema(
        {
            "type": {
                "type": "string",
                "description": "One of heading, paragraph, or bullets.",
            },
            "text": {"type": "string"},
            "items": {"type": "array", "items": {"type": "string"}},
        },
        ["type"],
    )
    outcome_schema = _object_schema(
        {
            "note": {
                "type": "string",
                "description": "Required short overview and plain next instruction, max 280 chars.",
            },
            "text": {
                "type": "string",
                "description": "Short inline outcome. Forbidden on APPROVE review steps.",
            },
            "document": _object_schema(
                {
                    "title": {"type": "string"},
                    "blocks": {
                        "type": "array",
                        "minItems": 1,
                        "items": document_block,
                    },
                },
                ["blocks"],
            ),
            "step_ref": {"type": "string"},
            "file_url": {
                "type": "string",
                "description": (
                    "RULE 230: a file you host, handed back through the platform's "
                    "scanner. The platform fetches it once, sniffs the type from the "
                    "bytes, fingerprints it and drops the bytes."
                ),
            },
            "claim_url": {
                "type": "string",
                "description": (
                    "For a here.now page: the person's link to KEEP the file within "
                    "24 hours. Only rides a file_url delivery."
                ),
            },
            "filename": {
                "type": "string",
                "description": "The name a file_url delivery wears on the person's card.",
            },
        },
        ["note"],
    )
    registry.register(
        ToolDefinition(
            "toll_bench.file_outcome",
            (
                "File the current step's final handover after a 100% pulse. Send exactly one of "
                "text, document or file_url; APPROVE review steps require a sectioned document. "
                "RULE 230: if this step's signed plan promised a FILE, it does not close on "
                "words -- deliver the bytes first with toll_bench.deliver_file, or file the "
                "outcome with file_url through toll_bench.deliver_hosted_file. A text section "
                "listing a filename closes nothing. NOT ON A "
                "BLOCK STEP (rule 229): where your plan declared a registry block, the platform "
                "files the outcome itself from the receipt words when the act executes, and that "
                "row reads actor: platform. File nothing there."
            ),
            _object_schema(
                {
                    "target_id": {"type": "string"},
                    "outcome": outcome_schema,
                    "idempotency_key": {"type": "string"},
                },
                ["target_id", "outcome", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).file_outcome(
            arguments["target_id"], arguments["outcome"], arguments["idempotency_key"]
        ),
    )

    # ------------------------------------------------------------------
    # RULE 230 (Steven, 2026-09-05) -- HANDING BACK A FILE.
    # A document step's signed plan names what it hands back. When the
    # channel is `file`, the step cannot close until bytes of the promised
    # type reach the platform. Two doors, one receipt shape: the platform
    # lane (bytes, under 50 MB) and your own hosting (a link the platform
    # fetches once, sniffs, fingerprints and drops).
    # ------------------------------------------------------------------

    def deliver_file(context: ToolContext, arguments: JsonObject) -> JsonObject:
        provider = require_toll_bench(context)
        path = str(arguments["path"])
        try:
            content = context.artifact_store.read(context.run_id, path)
        except FileNotFoundError:
            return {
                "ok": False,
                "error": "file_not_found",
                "message": (
                    f"{path} is not in this run's folder. Write it first with "
                    "files.write (encoding base64 for binary), then deliver it. "
                    "files.list shows what is there."
                ),
            }
        if len(content) > _ARTIFACT_MAX_BYTES:
            return {
                "ok": False,
                "error": "file_too_large",
                "size_bytes": len(content),
                "limit_bytes": _ARTIFACT_MAX_BYTES,
                "message": (
                    f"{path} is {len(content) / (1024 * 1024):.1f} MB and the "
                    "platform lane takes 50 MB per file (100 MB per want, "
                    "shared with what the person uploaded). Host it yourself "
                    "and hand back the link with toll_bench.deliver_hosted_file."
                ),
            }
        return provider.deliver_file(
            arguments["deal_id"],
            filename=path.rsplit("/", 1)[-1],
            content=content,
            title=arguments["title"],
            step_ref=arguments.get("step_ref"),
        )

    registry.register(
        ToolDefinition(
            "toll_bench.deliver_file",
            (
                "Hand a file from this run's folder to the person (rule 230). "
                "Reads `path` out of the run's isolated artifact directory and "
                "uploads the BYTES to the deal, where the platform sniffs the "
                "type from the bytes themselves, fingerprints them and attaches "
                "a file receipt to the step you are working. 50 MB per file, "
                "100 MB per want. A step whose signed plan promised a file does "
                "NOT close until a receipt of the promised type is attached: "
                "words listing a filename close nothing. This does not hand the "
                "ball over -- deliver the file, then file the step's outcome. "
                "If the bytes are not the promised type the platform answers "
                "422 deliverable_type_mismatch, and if no step of yours is "
                "working it answers 422 out_of_turn_filing; both come back as a "
                "plain result you can act on. For a file over 50 MB, or one "
                "already on your own hosting, use "
                "toll_bench.deliver_hosted_file instead."
            ),
            _object_schema(
                {
                    "deal_id": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": (
                            "The file in this run's artifact directory, as "
                            "files.list prints it."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "The item's real name as the person's card wears "
                            "it, for example 'Stan animation'. 80 characters "
                            "at most."
                        ),
                    },
                    "step_ref": {
                        "type": "string",
                        "description": (
                            "The step this file satisfies. Defaults to the step "
                            "your last current_step call reported."
                        ),
                    },
                },
                ["deal_id", "path", "title"],
            ),
        ),
        deliver_file,
    )

    registry.register(
        ToolDefinition(
            "toll_bench.deliver_hosted_file",
            (
                "Hand back a file you host yourself, through the platform's "
                "scanner (rule 230). Files the step's outcome carrying "
                "`file_url`: the platform fetches that address ONCE, sniffs the "
                "type from the bytes, records the size and fingerprint and "
                "drops the bytes. The person's download then streams from your "
                "address through the platform, re-checking the fingerprint, so "
                "a swapped or deleted file fails honestly instead of serving "
                "junk. Use it for a file over the 50 MB platform lane, for one "
                "already on your infrastructure, or for a here.now page (free, "
                "anonymous, live 24 hours): hand back the live URL as file_url "
                "AND the full claim URL as claim_url, which is the person's job "
                "to use within the day. This IS the step's outcome -- do not "
                "file a second one after it. A dead link after close is an "
                "honest state the person sees and a ping to you to re-host; no "
                "money moves either way."
            ),
            _object_schema(
                {
                    "target_id": {"type": "string"},
                    "file_url": {
                        "type": "string",
                        "description": (
                            "The live address the platform fetches the file "
                            "from, once. Passes the Link Gate."
                        ),
                    },
                    "claim_url": {
                        "type": "string",
                        "description": (
                            "For a here.now page: the full claim link the "
                            "person uses to keep the file within 24 hours."
                        ),
                    },
                    "filename": {
                        "type": "string",
                        "description": "The name the file wears on the person's card.",
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "Required, max 280 chars: what this is and exactly "
                            "what the person does next, in plain words."
                        ),
                    },
                    "step_ref": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                },
                ["target_id", "file_url", "note", "idempotency_key"],
            ),
        ),
        lambda context, arguments: require_toll_bench(context).deliver_hosted_file(
            arguments["target_id"],
            {
                key: arguments[key]
                for key in ("note", "file_url", "claim_url", "filename", "step_ref")
                if arguments.get(key)
            },
            arguments["idempotency_key"],
        ),
    )
    return registry
