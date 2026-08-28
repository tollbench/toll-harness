import json
import urllib.request
from types import SimpleNamespace

from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import (
    AutonomyMode,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    RunStatus,
    ToolCall,
)
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.storage.secrets import FileSecretStore
from toll_harness.tools import registry as registry_module
from toll_harness.tools.registry import ToolContext, build_standard_registry

SECRET_VALUE = "resolved-secret-value-2f8a"


class _FakeResponse:
    def __init__(self, body=b"ok", status=200, content_type="text/plain"):
        self._body = body
        self.status = status
        self.headers = SimpleNamespace(
            get_content_type=lambda: content_type,
            get_content_charset=lambda: "utf-8",
        )

    def read(self, limit=None):
        return self._body if limit is None else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _fake_transport(monkeypatch, response=None):
    captured = {}

    class _Opener:
        def open(self, request, timeout=None):
            captured["request"] = request
            captured["timeout"] = timeout
            return response or _FakeResponse()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: _Opener())
    return captured


def _secret_store(tmp_path):
    store = FileSecretStore(tmp_path / "secrets")
    store.set("API_KEY", SECRET_VALUE)
    return store


def _context(tmp_path, **overrides):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "model")
    fields = dict(
        run_id=run.id,
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        event_cursor=0,
        secret_store=_secret_store(tmp_path),
    )
    fields.update(overrides)
    return ToolContext(**fields)


def test_http_request_resolves_secrets_and_never_returns_them(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_module, "_validate_public_url", lambda _url: None)
    captured = _fake_transport(monkeypatch)
    registry = build_standard_registry()

    result = registry.execute(
        _context(tmp_path),
        "call-1",
        "http.request",
        {
            "method": "POST",
            "url": "https://api.example.com/v1/things?key={{secret:API_KEY}}",
            "headers": {"Authorization": "Bearer {{secret:API_KEY}}"},
            "body": "token={{secret:API_KEY}}",
            "timeout_seconds": 12,
        },
    )

    assert not result.is_error
    request = captured["request"]
    assert request.full_url == f"https://api.example.com/v1/things?key={SECRET_VALUE}"
    assert request.get_header("Authorization") == f"Bearer {SECRET_VALUE}"
    assert request.data == f"token={SECRET_VALUE}".encode()
    assert request.get_method() == "POST"
    assert captured["timeout"] == 12
    # The result carries the caller's own unresolved url and no secret value.
    assert result.output["status"] == 200
    assert result.output["url"] == "https://api.example.com/v1/things?key={{secret:API_KEY}}"
    assert SECRET_VALUE not in json.dumps(result.output)


def test_http_request_scrubs_an_echoed_secret_from_the_body(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_module, "_validate_public_url", lambda _url: None)
    _fake_transport(
        monkeypatch, response=_FakeResponse(body=f"you sent {SECRET_VALUE}!".encode())
    )
    registry = build_standard_registry()

    result = registry.execute(
        _context(tmp_path),
        "call-1",
        "http.request",
        {
            "method": "GET",
            "url": "https://echo.example.com/",
            "headers": {"X-Api-Key": "{{secret:API_KEY}}"},
        },
    )

    assert not result.is_error
    assert result.output["body"] == "you sent [secret:API_KEY]!"


def test_http_request_reports_an_unknown_secret_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_module, "_validate_public_url", lambda _url: None)
    _fake_transport(monkeypatch)
    registry = build_standard_registry()

    result = registry.execute(
        _context(tmp_path),
        "call-1",
        "http.request",
        {"method": "GET", "url": "https://api.example.com/{{secret:NOPE}}"},
    )

    assert result.is_error
    assert result.output["error"] == "unknown secret: NOPE"


def test_http_request_placeholders_require_a_secret_store(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_module, "_validate_public_url", lambda _url: None)
    _fake_transport(monkeypatch)
    registry = build_standard_registry()

    result = registry.execute(
        _context(tmp_path, secret_store=None),
        "call-1",
        "http.request",
        {"method": "GET", "url": "https://api.example.com/{{secret:API_KEY}}"},
    )

    assert result.is_error
    assert "secret store" in result.output["error"]


def test_http_request_refuses_private_addresses(tmp_path, monkeypatch):
    # The real SSRF guard from web.py: loopback must be refused before any I/O.
    _fake_transport(monkeypatch)
    registry = build_standard_registry()

    result = registry.execute(
        _context(tmp_path),
        "call-1",
        "http.request",
        {"method": "GET", "url": "http://127.0.0.1/admin"},
    )

    assert result.is_error
    assert "Private, loopback" in result.output["error"]


def test_http_request_refuses_the_toll_bench_host(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_module, "_validate_public_url", lambda _url: None)
    _fake_transport(monkeypatch)
    registry = build_standard_registry()
    provider = SimpleNamespace(api=SimpleNamespace(base_url="https://bookofhouses.com"))

    result = registry.execute(
        _context(tmp_path, toll_bench_provider=provider),
        "call-1",
        "http.request",
        {"method": "GET", "url": "https://bookofhouses.com/api/bench/targets/open"},
    )

    assert result.is_error
    assert result.output["error"] == "use the toll_bench tools for the bench"


def test_http_request_rejects_methods_outside_the_enum(tmp_path, monkeypatch):
    _fake_transport(monkeypatch)
    registry = build_standard_registry()

    result = registry.execute(
        _context(tmp_path),
        "call-1",
        "http.request",
        {"method": "TRACE", "url": "https://api.example.com/"},
    )

    assert result.is_error
    assert "must be one of" in result.output["error"]


def _response(*calls):
    tool_calls = [
        ToolCall(f"call-{index}", name, arguments) for index, (name, arguments) in enumerate(calls)
    ]
    return ModelResponse(
        message=ModelMessage(
            "assistant",
            [
                {"type": "tool_call", "id": call.id, "name": call.name, "arguments": call.arguments}
                for call in tool_calls
            ],
        ),
        text="",
        tool_calls=tool_calls,
        usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        stop_reason="tool_use",
    )


def test_runtime_events_log_http_request_shape_never_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_module, "_validate_public_url", lambda _url: None)
    _fake_transport(monkeypatch)
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    runtime = HarnessRuntime(
        model=ScriptedModelAdapter(
            [
                _response(
                    (
                        "http.request",
                        {
                            "method": "POST",
                            "url": "https://api.example.com/private/path?key={{secret:API_KEY}}",
                            "headers": {"Authorization": "Bearer {{secret:API_KEY}}"},
                            "body": "grant={{secret:API_KEY}}",
                        },
                    )
                ),
                _response(("result.complete", {"summary": "Called the API."})),
            ]
        ),
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=build_standard_registry(),
        enabled_tools=["http.request", "result.complete", "result.fail"],
        secret_store=_secret_store(tmp_path),
    )

    result = runtime.start("Use the API", AutonomyMode.AUTONOMOUS)

    assert result.status is RunStatus.COMPLETED
    events = store.list_events(result.run_id)
    # The resolved value reaches no event of any kind.
    assert SECRET_VALUE not in json.dumps([event.payload for event in events])
    called = next(event for event in events if event.kind == "tool.called")
    assert called.payload["arguments"] == {
        "method": "POST",
        "domain": "api.example.com",
        "header_names": ["Authorization"],
        "body_bytes": len(b"grant={{secret:API_KEY}}"),
    }
    # The model.response audit uses the same shape: no url, header values, or body.
    model_event = next(event for event in events if event.kind == "model.response")
    assert model_event.payload["tool_calls"][0]["arguments"] == called.payload["arguments"]
    for event in (called, model_event):
        dumped = json.dumps(event.payload)
        assert "/private/path" not in dumped
        assert "Bearer" not in dumped
