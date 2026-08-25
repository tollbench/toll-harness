import json

from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models.openai import OpenAIModelAdapter


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message, finish_reason="tool_calls"):
        self.message = message
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, prompt_tokens, completion_tokens, total_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _Response:
    def __init__(self, choices, usage, model="gpt-example"):
        self.choices = choices
        self.usage = usage
        self.model = model


class _Completions:
    def __init__(self, response):
        self._response = response
        self.request = None

    def create(self, **request):
        self.request = request
        return self._response


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class FakeOpenAIClient:
    def __init__(self, response):
        self.chat = _Chat(_Completions(response))


def _client():
    response = _Response(
        choices=[
            _Choice(
                _Message(
                    content="Saving state.",
                    tool_calls=[
                        _ToolCall(
                            "t1", "state__save", json.dumps({"checkpoint": {"status": "ready"}})
                        )
                    ],
                )
            )
        ],
        usage=_Usage(12, 8, 20),
    )
    return FakeOpenAIClient(response)


def test_openai_response_is_normalized():
    client = _client()
    adapter = OpenAIModelAdapter("gpt-example", client=client)
    tool = ToolDefinition("state.save", "Save state", {"type": "object", "properties": {}})

    response = adapter.invoke(
        system="system",
        messages=[ModelMessage.text("user", "goal")],
        tools=[tool],
    )

    request = client.chat.completions.request
    assert request["tools"][0]["function"]["name"] == "state__save"
    assert request["messages"][0] == {"role": "system", "content": "system"}
    assert response.tool_calls[0].name == "state.save"
    assert response.tool_calls[0].arguments == {"checkpoint": {"status": "ready"}}
    assert response.text == "Saving state."
    assert response.usage.total_tokens == 20
    assert response.stop_reason == "tool_calls"


def test_openai_expands_tool_results_into_tool_messages():
    client = _client()
    adapter = OpenAIModelAdapter("gpt-example", client=client)
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
            content=[{"type": "tool_result", "call_id": "t1", "output": {"ok": True}}],
        ),
    ]
    adapter.invoke(system="s", messages=messages, tools=[])
    wire = client.chat.completions.request["messages"]
    assert wire[0]["role"] == "system"
    assert wire[2]["role"] == "assistant"
    assert wire[2]["tool_calls"][0]["function"]["name"] == "state__save"
    assert wire[3]["role"] == "tool"
    assert wire[3]["tool_call_id"] == "t1"
    assert json.loads(wire[3]["content"]) == {"ok": True}
