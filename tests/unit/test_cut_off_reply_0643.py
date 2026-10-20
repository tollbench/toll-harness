"""A REPLY CUT OFF BEFORE IT SAID ANYTHING IS ASKED ONCE MORE (0.64.3).

WHAT FORCED THESE TESTS. Prod heat, 2026-10-20 02:57Z: DeepSeek V4.1 Flash,
GLM 5.3, Mistral Large 4 and Qwen 3.8 Max, all through OpenRouter, failed every
plan try with "empty reply". One plan-sized call each at max_tokens 5000 came
back finish_reason "length", 4,999-5,000 reasoning tokens, content empty: the
models thought until the budget ran out. The adapter read only
`message.content` and handed back "" with nothing to say why. Now a reply that
is empty, has no tool call and was stopped by the budget is asked once more
with the reasoning turned down ("none", or "low" where reasoning is
mandatory), and an empty reply always says which kind it was.
"""
from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models import openai as openai_module
from toll_harness.models.base import ModelInvocationError, empty_reply_note
from toll_harness.models.openai import OpenAIModelAdapter

OPENROUTER = "https://openrouter.ai/api/v1"
ANSWER = '{"overview": "Pitch the single.", "odds": 0.3, "steps": []}'


@pytest.fixture(autouse=True)
def _forget_mandatory_models():
    openai_module._REASONING_IS_MANDATORY.clear()
    yield
    openai_module._REASONING_IS_MANDATORY.clear()


def _reply(content="", *, finish="stop", completion=10, reasoning_tokens=0,
           reasoning=None, tool_calls=None, prompt=3000):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    if reasoning is not None:
        message.reasoning = reasoning
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=usage,
        model="m",
    )


def _cut(reasoning_tokens=4999):
    return _reply("", finish="length", completion=5000,
                  reasoning_tokens=reasoning_tokens, reasoning="thinking " * 50)


class FakeClient:
    """Answers each create() with the next scripted reply, or raises it."""

    def __init__(self, *script, base_url=OPENROUTER):
        self.base_url = base_url
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Status(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _ask(adapter, tools=()):
    return adapter.invoke(system="system", messages=[ModelMessage.text("user", "plan")],
                          tools=list(tools))


def test_a_cut_off_reply_is_asked_once_more_with_reasoning_off():
    client = FakeClient(_cut(), _reply(ANSWER, completion=600))
    adapter = OpenAIModelAdapter("deepseek/deepseek-v4.1-flash", max_tokens=5000,
                                 client=client)

    response = _ask(adapter)

    assert response.text == ANSWER
    assert len(client.requests) == 2
    first, second = client.requests
    assert "extra_body" not in first
    assert second["extra_body"] == {"reasoning": {"effort": "none"}}
    # Same question, same budget: only the reasoning setting changed.
    assert {k: v for k, v in second.items() if k != "extra_body"} == first
    meta = response.raw_metadata
    assert meta["retry"] == {"reasoning": {"effort": "none"}}
    assert meta["cut_off_try"]["reasoning_tokens"] == 4999
    assert meta["cut_off_try"]["finish_reason"] == "length"
    assert "cut_off" not in meta and empty_reply_note(response) == ""
    # The wasted try was billed: its output rides the totals, the input does
    # not (input is the conversation's size).
    assert response.usage.input_tokens == 3000
    assert response.usage.output_tokens == 5600
    assert response.usage.raw["cut_off_try"]["completion_tokens"] == 5000


def test_a_model_whose_reasoning_is_mandatory_is_asked_on_low_and_remembered():
    refused = _Status(400, "Reasoning is mandatory for this endpoint and cannot be disabled.")
    client = FakeClient(_cut(), refused, _reply(ANSWER, completion=1600, reasoning_tokens=1041))
    adapter = OpenAIModelAdapter("qwen/qwen3.8-max-0902", max_tokens=5000, client=client)

    response = _ask(adapter)

    assert response.text == ANSWER
    efforts = [r.get("extra_body", {}).get("reasoning") for r in client.requests]
    assert efforts == [None, {"effort": "none"}, {"effort": "low"}]
    assert response.raw_metadata["retry"] == {"reasoning": {"effort": "low"}}

    # A fresh adapter for the same model (the runner builds one per cycle)
    # goes straight to "low": the refusal is paid once per process.
    client2 = FakeClient(_cut(), _reply(ANSWER))
    again = _ask(OpenAIModelAdapter("qwen/qwen3.8-max-0902", max_tokens=5000,
                                    client=client2))
    assert again.text == ANSWER
    assert client2.requests[1]["extra_body"] == {"reasoning": {"effort": "low"}}


def test_the_mandatory_refusal_is_read_off_a_urllib_error_body_too():
    body = io.BytesIO(b'{"error":{"message":"Reasoning is mandatory for this endpoint '
                      b'and cannot be disabled.","code":400}}')
    refused = urllib.error.HTTPError(OPENROUTER, 400, "Bad Request", {}, body)
    client = FakeClient(_cut(), refused, _reply(ANSWER))
    response = _ask(OpenAIModelAdapter("z-ai/glm-5.3", max_tokens=5000, client=client))
    assert response.text == ANSWER
    assert client.requests[2]["extra_body"] == {"reasoning": {"effort": "low"}}


def test_cut_off_twice_is_said_never_handed_back_silent():
    client = FakeClient(_cut(), _cut(4990))
    response = _ask(OpenAIModelAdapter("mistralai/mistral-large-4-0", max_tokens=5000,
                                       client=client))

    assert response.text == ""
    assert len(client.requests) == 2  # once more, never a third time
    note = empty_reply_note(response)
    assert note.startswith("cut off: finish_reason length after 4990 reasoning tokens")
    assert "the second try with reasoning none was cut off too" in note
    assert response.raw_metadata["cut_off"] == note
    assert response.stop_reason == "length"


def test_a_refused_setting_hands_back_the_first_reply_marked():
    client = FakeClient(_cut(), _Status(404, "No endpoints found that can handle the "
                                             "requested parameters."))
    response = _ask(OpenAIModelAdapter("some/model", max_tokens=5000, client=client))
    assert response.text == ""
    assert "was refused (404)" in empty_reply_note(response)
    assert response.usage.output_tokens == 5000  # one call, counted once
    assert "cut_off_try" not in response.raw_metadata


def test_a_second_try_that_fails_outright_fails_like_any_call_and_says_why():
    client = FakeClient(_cut(), TimeoutError("timed out"))
    with pytest.raises(ModelInvocationError) as caught:
        _ask(OpenAIModelAdapter("some/model", max_tokens=5000, client=client))
    assert "cut off: finish_reason length" in str(caught.value)
    assert "timed out" in str(caught.value)


def test_openai_direct_is_marked_but_never_asked_again():
    client = FakeClient(_cut(), base_url="https://api.openai.com/v1")
    response = _ask(OpenAIModelAdapter("o-model", max_tokens=5000, client=client))
    assert len(client.requests) == 1
    assert "extra_body" not in client.requests[0]
    assert empty_reply_note(response).startswith("cut off: finish_reason length")


def test_a_whole_reply_and_a_truncated_one_with_words_are_untouched():
    client = FakeClient(_reply(ANSWER), _reply("half an answ", finish="length",
                                                completion=5000))
    adapter = OpenAIModelAdapter("gpt-example", max_tokens=5000, client=client)
    whole = _ask(adapter)
    truncated = _ask(adapter)
    assert len(client.requests) == 2  # one call each, no retry
    assert all("extra_body" not in r for r in client.requests)
    assert whole.text == ANSWER and truncated.text == "half an answ"
    assert empty_reply_note(whole) == empty_reply_note(truncated) == ""
    assert whole.raw_metadata["finish_reason"] == "stop"
    assert whole.raw_metadata["reasoning"] is False


def test_an_empty_reply_that_stopped_on_its_own_says_it_said_nothing():
    client = FakeClient(_reply("", finish="stop", completion=3))
    response = _ask(OpenAIModelAdapter("some/model", max_tokens=5000, client=client))
    assert len(client.requests) == 1
    assert empty_reply_note(response) == (
        "said nothing: finish_reason stop, no text and no tool call")


def test_a_tool_call_with_no_text_is_not_a_cut_off():
    call = SimpleNamespace(id="t1", type="function",
                           function=SimpleNamespace(name="state__save", arguments="{}"))
    client = FakeClient(_reply("", finish="length", completion=5000, reasoning_tokens=4000,
                               tool_calls=[call]))
    tool = ToolDefinition("state.save", "Save state", {"type": "object", "properties": {}})
    response = _ask(OpenAIModelAdapter("some/model", max_tokens=5000, client=client), [tool])
    assert len(client.requests) == 1
    assert response.tool_calls[0].name == "state.save"
    assert empty_reply_note(response) == ""


def test_a_budget_spent_on_reasoning_counts_even_when_the_provider_says_stop():
    spent = _reply("", finish="stop", completion=5000, reasoning_tokens=5000)
    client = FakeClient(spent, _reply(ANSWER))
    response = _ask(OpenAIModelAdapter("some/model", max_tokens=5000, client=client))
    assert response.text == ANSWER and len(client.requests) == 2


def test_reasoning_presence_and_tokens_ride_every_reply():
    client = FakeClient(_reply(ANSWER, reasoning="short thought", reasoning_tokens=12))
    response = _ask(OpenAIModelAdapter("some/model", max_tokens=5000, client=client))
    meta = response.raw_metadata
    assert meta["reasoning"] is True and meta["reasoning_chars"] == len("short thought")
    assert meta["reasoning_tokens"] == 12 and meta["max_tokens"] == 5000
    assert response.usage.raw["reasoning_tokens"] == 12


def test_both_calls_are_put_on_the_bill():
    class Ledger:
        def __init__(self):
            self.rows = []

        def add(self, model, **row):
            self.rows.append(row)

    client = FakeClient(_cut(), _reply(ANSWER, completion=600))
    adapter = OpenAIModelAdapter("some/model", max_tokens=5000, client=client)
    adapter.cost_ledger = Ledger()
    _ask(adapter)
    assert [row["out"] for row in adapter.cost_ledger.rows] == [5000, 600]


def test_the_draft_loop_log_says_cut_off_not_just_empty():
    from toll_harness.core.types import ModelResponse
    response = ModelResponse(
        message=ModelMessage(role="assistant", content=[]), text="", tool_calls=[],
        raw_metadata={"cut_off": "cut off: finish_reason length after 5000 reasoning tokens"},
    )
    assert empty_reply_note(response).startswith("cut off")
    assert empty_reply_note(SimpleNamespace(raw_metadata=None)) == ""
