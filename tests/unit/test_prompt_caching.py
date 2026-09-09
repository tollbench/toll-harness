"""PROMPT CACHING — the stable prefix is marked where the provider takes a mark.

WHAT FORCED THESE. Steven measured ~25,000 input tokens to write one plan and
asked for ~8,000. The draft loop sends the same front door, tools index and
block index in front of every round of a want; paying full price for that block
on every round is most of the difference. Each provider takes the mark
differently, and one of them (Bedrock) REFUSES the whole call if the model does
not support it -- so each is held to its own rule here.
"""
from __future__ import annotations

from types import SimpleNamespace

from toll_harness.core.types import ModelMessage
from toll_harness.models.anthropic import AnthropicModelAdapter
from toll_harness.models.bedrock import BedrockModelAdapter
from toll_harness.models.openai import OpenAIModelAdapter

LONG = "x" * 5_000
SHORT = "a short system"


class _AnthropicClient:
    def __init__(self):
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=5,
                cache_read_input_tokens=4_000,
                cache_creation_input_tokens=0,
            ),
            stop_reason="end_turn",
            model="claude-test",
        )


def _ask(adapter):
    return adapter.invoke(
        system=LONG, messages=[ModelMessage.text("user", "hi")], tools=[]
    )


def test_anthropic_marks_a_long_system_cacheable_and_reports_what_it_saved():
    client = _AnthropicClient()
    adapter = AnthropicModelAdapter("claude-test", client=client)

    response = _ask(adapter)

    system = client.requests[0]["system"]
    assert system == [
        {"type": "text", "text": LONG, "cache_control": {"type": "ephemeral"}}
    ]
    assert response.usage.raw["cache_read_input_tokens"] == 4_000


def test_anthropic_leaves_a_short_system_exactly_as_it_was():
    client = _AnthropicClient()
    adapter = AnthropicModelAdapter("claude-test", client=client)

    adapter.invoke(system=SHORT, messages=[ModelMessage.text("user", "hi")], tools=[])

    assert client.requests[0]["system"] == SHORT


def test_anthropic_caching_can_be_turned_off_by_the_operator():
    client = _AnthropicClient()
    adapter = AnthropicModelAdapter("claude-test", client=client, prompt_caching=False)

    _ask(adapter)

    assert client.requests[0]["system"] == LONG


class _BedrockClient:
    def __init__(self):
        self.requests = []

    def converse(self, **request):
        self.requests.append(request)
        return {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadInputTokens": 8},
            "stopReason": "end_turn",
        }


def test_bedrock_sends_a_cache_point_only_where_the_model_takes_one():
    client = _BedrockClient()
    _ask(BedrockModelAdapter("us.anthropic.claude-test", client=client))

    assert client.requests[0]["system"] == [
        {"text": LONG},
        {"cachePoint": {"type": "default"}},
    ]


def test_bedrock_sends_no_cache_point_to_a_model_that_would_refuse_it():
    # A model that does not take a cachePoint answers the WHOLE call with a
    # ValidationException, so this is decided by the model id and not by hope.
    client = _BedrockClient()
    _ask(BedrockModelAdapter("deepseek.v3-2", client=client))

    assert client.requests[0]["system"] == [{"text": LONG}]


class _OpenAIClient:
    def __init__(self, base_url=""):
        self.base_url = base_url
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=[]),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=2,
                total_tokens=12,
                prompt_tokens_details=SimpleNamespace(cached_tokens=6),
            ),
            model="m",
        )


def test_openrouter_gets_the_cache_control_an_anthropic_model_needs():
    client = _OpenAIClient("https://openrouter.ai/api/v1")
    _ask(OpenAIModelAdapter("anthropic/claude-sonnet-5", client=client))

    system = client.requests[0]["messages"][0]
    assert system["content"] == [
        {"type": "text", "text": LONG, "cache_control": {"type": "ephemeral"}}
    ]


def test_openai_itself_is_left_alone_because_it_caches_by_itself():
    client = _OpenAIClient("https://api.openai.com/v1")
    response = _ask(OpenAIModelAdapter("gpt-5", client=client))

    assert client.requests[0]["messages"][0]["content"] == LONG
    # ...and what it cached is still reported.
    assert response.usage.raw["cached_tokens"] == 6
