from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models.anthropic import AnthropicModelAdapter


class _Block:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, content, usage, stop_reason="tool_use", model="claude-opus-4-8"):
        self.content = content
        self.usage = usage
        self.stop_reason = stop_reason
        self.model = model


class _Messages:
    def __init__(self, response):
        self._response = response
        self.request = None

    def create(self, **request):
        self.request = request
        return self._response


class FakeAnthropicClient:
    def __init__(self, response):
        self.messages = _Messages(response)


def _client():
    response = _Response(
        content=[
            _Block(type="text", text="Saving state."),
            _Block(
                type="tool_use",
                id="t1",
                name="state__save",
                input={"checkpoint": {"status": "ready"}},
            ),
        ],
        usage=_Usage(12, 8),
    )
    return FakeAnthropicClient(response)


def test_anthropic_response_is_normalized():
    client = _client()
    adapter = AnthropicModelAdapter("claude-opus-4-8", client=client)
    tool = ToolDefinition(
        "state.save",
        "Save state",
        {"type": "object", "properties": {"checkpoint": {"type": "object"}}},
    )

    response = adapter.invoke(
        system="system",
        messages=[ModelMessage.text("user", "goal")],
        tools=[tool],
    )

    assert client.messages.request["tools"][0]["name"] == "state__save"
    assert response.tool_calls[0].name == "state.save"
    assert response.tool_calls[0].arguments == {"checkpoint": {"status": "ready"}}
    assert response.text == "Saving state."
    assert response.usage.total_tokens == 20
    assert response.stop_reason == "tool_use"


def test_anthropic_default_model_and_tool_result_round_trip():
    client = _client()
    adapter = AnthropicModelAdapter(client=client)
    assert adapter.model_id == "claude-opus-4-8"

    messages = [
        ModelMessage.text("user", "goal"),
        ModelMessage(
            role="assistant",
            content=[
                {"type": "tool_call", "id": "t1", "name": "state.save", "arguments": {"x": 1}}
            ],
        ),
        ModelMessage(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "call_id": "t1",
                    "output": {"ok": True},
                    "is_error": False,
                }
            ],
        ),
    ]
    adapter.invoke(system="s", messages=messages, tools=[])
    wire = client.messages.request["messages"]
    assert wire[1]["content"][0] == {
        "type": "tool_use",
        "id": "t1",
        "name": "state__save",
        "input": {"x": 1},
    }
    assert wire[2]["content"][0]["type"] == "tool_result"
    assert wire[2]["content"][0]["tool_use_id"] == "t1"
    assert wire[2]["content"][0]["is_error"] is False
