from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from toll_harness.core.types import (
    JsonObject,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)
from toll_harness.models.base import ModelAdapter, ModelInvocationError

# PROMPT CACHING. OpenAI caches long prefixes by itself and takes no marker, so
# nothing is added for it. OpenRouter is the one that needs one: it passes
# `cache_control` through to Anthropic models, and without it an Anthropic model
# reached through OpenRouter pays full price for a prefix it has already seen.
# The marker rides the system message content parts, which is why it goes on
# only when the client is pointed at OpenRouter -- an unknown key on a strict
# endpoint is a refused call, not a slower one.
CACHE_MIN_CHARS = 4_000


# A REPLY CUT OFF BEFORE IT SAID ANYTHING (0.64.3). WHAT FORCED IT: prod
# heat, 2026-10-20 02:57Z. DeepSeek V4.1 Flash, GLM 5.3, Mistral Large 4 and
# Qwen 3.8 Max, all through OpenRouter, failed every plan try with "empty
# reply". One plan-sized call each at max_tokens 5000 proved it: every one
# came back finish_reason "length", 4,999-5,000 reasoning tokens, content
# empty -- they thought until the budget ran out and never wrote the answer.
# OpenRouter documents exactly this: reasoning tokens count against
# max_tokens, and when they spend it the reply is `length` with no content.
# So a reply that is empty, carries no tool call and was stopped by the
# budget is asked ONCE more with the reasoning turned down. The cap that
# worked differs by model: "none" answered on DeepSeek, Mistral and Kimi, and
# GLM and Qwen refuse it at the door ("Reasoning is mandatory for this
# endpoint") -- they answered on "low". A model that refused "none" is
# remembered for the life of the process, so the refusal is paid once.
#
# A SLICED REPLY GETS THE SAME ONE RETRY (0.68.0). A thinking model that
# spent most of the budget reasoning and was then stopped part way through its
# answer (`length`, text present, no tool call) has the same cause and the
# same cure: until 0.68.0 only an EMPTY cut-off was asked again, so a plan
# sliced mid-JSON was handed back as it stood and read as no plan at all. The
# lever here is the reasoning setting, so a sliced reply is asked again only
# when the model reasoned; one that did not think has nothing to turn down,
# and is marked (`sliced`) for its caller, which can ask for a shorter answer.
CUT_OFF_REASONING = {"effort": "none"}
MANDATORY_REASONING = {"effort": "low"}
_CUT_OFF_FINISH = frozenset({"length", "max_tokens", "max_output_tokens"})
_REASONING_IS_MANDATORY: set[str] = set()


def _is_openrouter(client: Any) -> bool:
    return "openrouter" in str(getattr(client, "base_url", "") or "").lower()


def _field(obj: Any, name: str) -> Any:
    """An attribute or a key: SDK objects carry extra fields as attributes,
    a plain decoded body carries them as keys."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _reasoning_tokens(usage: Any) -> int:
    details = _field(usage, "completion_tokens_details")
    try:
        return int(_field(details, "reasoning_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def _error_says_reasoning_is_mandatory(error: Exception) -> bool:
    """The provider's own words, read only for this one refusal and never
    passed on (a body can echo the request)."""
    texts = [str(error), str(getattr(error, "message", "") or "")]
    body = getattr(error, "body", None)
    if body is not None:
        texts.append(json.dumps(body, default=str) if not isinstance(body, str) else body)
    reader = getattr(error, "read", None)
    if callable(reader):  # urllib's HTTPError keeps the body unread
        try:
            raw = reader()
            texts.append(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))
        except Exception:  # noqa: BLE001 - a body that will not read says nothing
            pass
    said = " ".join(texts).lower()
    return "reasoning is mandatory" in said or "cannot be disabled" in said


def _status_of(error: Exception) -> int | None:
    for name in ("status_code", "status", "code"):
        value = getattr(error, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _tool_alias(name: str) -> str:
    # Tool names carry dots (e.g. "state.save"); OpenAI requires ^[a-zA-Z0-9_-]{1,64}$.
    return name.replace(".", "__")


def _canonical_tool_name(name: str) -> str:
    return name.replace("__", ".")


class OpenAIModelAdapter(ModelAdapter):
    """OpenAI Chat Completions adapter with no model-specific runtime behavior.

    model.model_id is required (no default) so the adapter never assumes a
    particular OpenAI model on the operator's behalf.
    """

    def __init__(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        max_tokens: int = 2048,
        client: Any | None = None,
        prompt_caching: bool = True,
    ):
        self._model_id = model_id
        self.max_tokens = max_tokens
        self.prompt_caching = prompt_caching
        if client is None:
            try:
                import openai
            except ImportError as error:
                raise RuntimeError("Install Toll Harness with the 'openai' extra") from error
            client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        self.client = client

    @property
    def model_id(self) -> str:
        return self._model_id

    def caches_a_stable_prefix(self) -> bool:
        # OpenAI caches a long prefix by itself and takes no marker; OpenRouter
        # needs the marker `_system_content` adds. Either way a repeated prefix
        # is cheaper than sending the same bytes anew.
        return bool(self.prompt_caching)

    def _system_content(self, system: str) -> Any:
        """The system content, and a cache marker only where it is understood."""
        text = str(system or "")
        if not (
            self.prompt_caching
            and len(text) >= CACHE_MIN_CHARS
            and _is_openrouter(self.client)
        ):
            return text
        return [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
        ]

    def _messages_to_openai(
        self, system: str, messages: Sequence[ModelMessage]
    ) -> list[JsonObject]:
        out: list[JsonObject] = [{"role": "system", "content": self._system_content(system)}]
        for message in messages:
            if message.role == "assistant":
                text_parts: list[str] = []
                tool_calls: list[JsonObject] = []
                for block in message.content:
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif block_type == "tool_call":
                        tool_calls.append(
                            {
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": _tool_alias(block["name"]),
                                    "arguments": json.dumps(block.get("arguments", {})),
                                },
                            }
                        )
                    else:
                        raise ValueError(f"Unsupported assistant content type: {block_type}")
                entry: JsonObject = {
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                }
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                out.append(entry)
            else:  # user turns carry text and/or tool results
                for block in message.content:
                    block_type = block.get("type")
                    if block_type == "text":
                        out.append({"role": "user", "content": str(block.get("text", ""))})
                    elif block_type == "tool_result":
                        out.append(
                            {
                                "role": "tool",
                                "tool_call_id": block["call_id"],
                                "content": json.dumps(block.get("output", {})),
                            }
                        )
                    else:
                        raise ValueError(f"Unsupported user content type: {block_type}")
        return out

    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        request: JsonObject = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "messages": self._messages_to_openai(system, messages),
        }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": _tool_alias(tool.name),
                        "description": f"{tool.name} v{tool.version}: {tool.description}",
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
            request["tool_choice"] = "auto"
        first = self._read(self._create(request))
        again = first["cut_off"] or (first["sliced"] and first["thought"])
        if not again or not _is_openrouter(self.client):
            # Everything that came back whole, and every endpoint that is not
            # OpenRouter, is answered exactly as before; a cut-off reply off
            # another endpoint is only marked, never asked again here.
            return self._respond(first)
        return self._respond(self._ask_again(request, first), cut=first)

    def _create(self, request: JsonObject) -> Any:
        try:
            return self.client.chat.completions.create(**request)
        except Exception as error:
            code = getattr(error, "type", None) or type(error).__name__
            message = getattr(error, "message", None) or str(error)
            raise ModelInvocationError("openai", code, message) from error

    def _ask_again(self, request: JsonObject, first: JsonObject) -> JsonObject:
        """ONE more call with the reasoning turned down, on the same budget.

        "none" first; a model that answers that reasoning cannot be turned
        off is asked on "low" instead, and remembered. A refusal of the
        setting itself (any 4xx) hands back the first reply, marked; anything
        else fails the call exactly as a first call would.
        """
        mandatory = self.model_id in _REASONING_IS_MANDATORY
        cap = MANDATORY_REASONING if mandatory else CUT_OFF_REASONING
        while True:
            retry = dict(request)
            extra = dict(request.get("extra_body") or {})
            extra["reasoning"] = dict(cap)
            retry["extra_body"] = extra
            try:
                response = self.client.chat.completions.create(**retry)
            except Exception as error:
                status = _status_of(error)
                if (
                    cap is CUT_OFF_REASONING
                    and status is not None
                    and 400 <= status < 500
                    and _error_says_reasoning_is_mandatory(error)
                ):
                    _REASONING_IS_MANDATORY.add(self.model_id)
                    cap = MANDATORY_REASONING
                    continue
                if status is not None and 400 <= status < 500:
                    # Nothing was generated: the first reply is the answer,
                    # marked with the refusal.
                    return dict(first, retry={"reasoning": dict(cap), "refused": status},
                                same_call=True)
                code = getattr(error, "type", None) or type(error).__name__
                message = getattr(error, "message", None) or str(error)
                raise ModelInvocationError(
                    "openai",
                    code,
                    f"{cut_off_words(first)}; the second try with reasoning "
                    f"{cap} failed: {message}",
                ) from error
            again = self._read(response)
            again["retry"] = {"reasoning": dict(cap)}
            return again

    def _read(self, response: Any) -> JsonObject:
        """One provider reply, normalized, with what stopped it and whether
        it thought out loud. Its spend is counted here, once per call."""
        choice = response.choices[0]
        model_message = choice.message
        normalized: list[JsonObject] = []
        calls: list[ToolCall] = []
        text = getattr(model_message, "content", None) or ""
        if text:
            normalized.append({"type": "text", "text": text})
        for tool_call in getattr(model_message, "tool_calls", None) or []:
            arguments = json.loads(tool_call.function.arguments or "{}")
            call = ToolCall(
                id=tool_call.id,
                name=_canonical_tool_name(tool_call.function.name),
                arguments=arguments,
            )
            calls.append(call)
            normalized.append(
                {
                    "type": "tool_call",
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
            )
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or (input_tokens + output_tokens)
        cached_tokens = int(
            getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        )
        # prompt_tokens includes the cached share; the cached share is priced lower.
        self.count_spend(
            fresh_in=max(0, input_tokens - cached_tokens),
            cache_read=min(cached_tokens, input_tokens),
            out=output_tokens,
        )
        finish = getattr(choice, "finish_reason", None)
        native = _field(choice, "native_finish_reason")
        reasoning = _field(model_message, "reasoning")
        reasoning_chars = len(reasoning) if isinstance(reasoning, str) else 0
        reasoning_tokens = _reasoning_tokens(usage)
        stops = {str(finish or "").lower(), str(native or "").lower()}
        spent = bool(reasoning_tokens) and output_tokens >= self.max_tokens
        return {
            "text": text,
            "normalized": normalized,
            "calls": calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "finish_reason": finish,
            "model": getattr(response, "model", self.model_id),
            "reasoning": bool(reasoning) or bool(_field(model_message, "reasoning_details")),
            "reasoning_chars": reasoning_chars,
            "reasoning_tokens": reasoning_tokens,
            # Empty, no tool call, and the budget is what stopped it.
            "cut_off": (
                not text.strip()
                and not calls
                and (bool(stops & _CUT_OFF_FINISH) or spent)
            ),
            # 0.68.0: words came back, no tool call, and the budget stopped
            # them part way: a sliced reply.
            "sliced": bool(text.strip()) and not calls and bool(stops & _CUT_OFF_FINISH),
            "thought": bool(
                reasoning_tokens or reasoning_chars or reasoning
                or _field(model_message, "reasoning_details")
            ),
        }

    def _respond(self, read: JsonObject, cut: JsonObject | None = None) -> ModelResponse:
        """The reply as the runtime reads it. When a cut-off first try came
        before it, its billed output is added in and it rides `cut_off_try`;
        the input count stays the answering call's, which is the
        conversation's size."""
        output_tokens = read["output_tokens"]
        total_tokens = read["total_tokens"]
        metadata: JsonObject = {
            "model": read["model"],
            "finish_reason": read["finish_reason"],
            "reasoning": read["reasoning"],
            "reasoning_chars": read["reasoning_chars"],
            "reasoning_tokens": read["reasoning_tokens"],
            "max_tokens": self.max_tokens,
        }
        raw: JsonObject = {
            "prompt_tokens": read["input_tokens"],
            "completion_tokens": read["output_tokens"],
            # Both providers report the cached share here; OpenAI as
            # prompt_tokens_details.cached_tokens, OpenRouter the same.
            "cached_tokens": read["cached_tokens"],
            "reasoning_tokens": read["reasoning_tokens"],
        }
        if read.get("retry"):
            metadata["retry"] = read["retry"]
        if cut is not None and not read.get("same_call"):
            first = {
                "finish_reason": cut["finish_reason"],
                "prompt_tokens": cut["input_tokens"],
                "completion_tokens": cut["output_tokens"],
                "reasoning_tokens": cut["reasoning_tokens"],
            }
            metadata["cut_off_try"] = first
            raw["cut_off_try"] = first
            # The first try's output was generated and billed.
            output_tokens += cut["output_tokens"]
            total_tokens += cut["output_tokens"]
        if read["cut_off"]:
            metadata["cut_off"] = cut_off_words(read)
        elif read.get("sliced"):
            # Not `cut_off`: that key says a reply came back EMPTY, and a
            # caller reads it only then (`empty_reply_note`).
            metadata["sliced"] = cut_off_words(read)
        elif not read["text"].strip() and not read["calls"]:
            metadata["empty"] = (
                f"said nothing: finish_reason {read['finish_reason'] or 'unreported'}, "
                "no text and no tool call"
            )
        return ModelResponse(
            message=ModelMessage(role="assistant", content=read["normalized"]),
            text=read["text"],
            tool_calls=read["calls"],
            usage=ModelUsage(
                input_tokens=read["input_tokens"],
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                raw=raw,
            ),
            stop_reason=read["finish_reason"],
            raw_metadata=metadata,
        )


def cut_off_words(read: JsonObject) -> str:
    """Plain words for a reply the budget stopped before it said anything, or
    (0.68.0) part way through what it was saying."""
    text = str(read.get("text") or "")
    came_back = (
        f"{len(text)} characters came back and stopped mid-reply"
        if text.strip() else "no text came back"
    )
    words = (
        f"cut off: finish_reason {read.get('finish_reason') or 'unreported'} "
        f"after {int(read.get('reasoning_tokens') or 0)} reasoning tokens "
        f"({int(read.get('output_tokens') or 0)} output tokens), {came_back}"
    )
    retry = read.get("retry")
    if isinstance(retry, dict):
        effort = (retry.get("reasoning") or {}).get("effort")
        if retry.get("refused"):
            words += f"; the second try with reasoning {effort} was refused ({retry['refused']})"
        else:
            words += f"; the second try with reasoning {effort} was cut off too"
    return words
