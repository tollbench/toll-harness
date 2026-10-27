from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from toll_harness.core.types import ModelMessage, ModelResponse, ToolDefinition


class ModelInvocationError(RuntimeError):
    """Raised when a provider rejects or fails an invocation."""

    def __init__(self, provider: str, code: str, message: str):
        super().__init__(f"{provider} {code}: {message}")
        self.provider = provider
        self.code = code
        self.message = message


def empty_reply_note(response: Any) -> str:
    """Why a reply came back with no text, in plain words, or "".

    0.64.3: an adapter that can tell marks it on `raw_metadata` -- `cut_off`
    when the output budget stopped the model before it said anything (a
    thinking model that spent max_tokens reasoning), `empty` when it stopped
    on its own and said nothing. A caller prints this instead of guessing,
    so "cut off" and "said nothing" are never the same line. An adapter that
    marks nothing answers "".
    """
    metadata = getattr(response, "raw_metadata", None)
    if not isinstance(metadata, dict):
        return ""
    for key in ("cut_off", "empty"):
        note = metadata.get(key)
        if isinstance(note, str) and note:
            return note
    return ""


# WHAT A PROVIDER SAYS WHEN THE OUTPUT LIMIT, NOT THE MODEL, ENDED A REPLY
# (0.68.0): OpenAI and OpenRouter say `length`, Anthropic and Bedrock say
# `max_tokens`, the Responses API says `max_output_tokens`.
CUT_OFF_STOPS = frozenset({"length", "max_tokens", "max_output_tokens"})


def stop_reason_of(response: Any) -> str:
    """What stopped this reply, as the provider said it, or ""."""
    reason = getattr(response, "stop_reason", None)
    if not reason:
        metadata = getattr(response, "raw_metadata", None)
        if isinstance(metadata, dict):
            reason = metadata.get("finish_reason")
    return str(reason or "")


def what_came_back(response: Any) -> str:
    """One model reply, measured for the log line every call writes (0.68.0):
    its size, its tool calls, what the provider billed out, and what stopped
    it. Sizes and counts only, never the text."""
    text = str(getattr(response, "text", "") or "")
    calls = getattr(response, "tool_calls", None) or []
    usage = getattr(response, "usage", None)
    out = getattr(usage, "output_tokens", None) if usage is not None else None
    stop = stop_reason_of(response) or "unreported"
    words = f"answer {len(text)} chars, {len(calls)} tool call(s), "
    words += f"out {out} tokens" if isinstance(out, int) and out else "out unreported"
    words += f", stop {stop}"
    if was_cut_off(response):
        words += " (cut off by the output limit)"
    return words


def was_cut_off(response: Any) -> bool:
    """True when the output limit ended this reply, empty or sliced (0.68.0).

    Read off the provider's own stop reason, or off the adapter's mark
    (`cut_off` on an empty reply, `sliced` on one that stopped mid-answer).
    """
    if stop_reason_of(response).lower() in CUT_OFF_STOPS:
        return True
    metadata = getattr(response, "raw_metadata", None)
    if not isinstance(metadata, dict):
        return False
    return any(isinstance(metadata.get(key), str) and metadata.get(key)
               for key in ("cut_off", "sliced"))


def asked_again_over_a_cut_off(response: Any) -> bool:
    """True when the adapter itself already asked once more because the first
    reply was cut off (0.64.3 `retry`, 0.68.0 for a sliced reply too), so a
    caller never spends a second retry on the same cut-off."""
    metadata = getattr(response, "raw_metadata", None)
    return isinstance(metadata, dict) and bool(
        metadata.get("retry") or metadata.get("cut_off_try")
    )


class ModelAdapter(ABC):
    """Provider-neutral intelligence boundary."""

    # 0.63.0. What the agent spent on a deal, for the optional `agent_cost`
    # hand-back slot. `cost_ledger` is set by whoever builds the runtime (None
    # counts nothing). `cost_basis` is how the adapter's dollars are to be read:
    # "metered" when the call is billed to an API key, "subscription_estimate"
    # when it runs on a plan and the dollars are a share priced from a table.
    cost_ledger: Any = None
    cost_basis: str = "metered"
    # 0.73.0. The model's context window in tokens, set by whoever builds the
    # adapter (`model.context_window_tokens`, else the environment, else the
    # table in core/window.py). Every prompt budget the harness keeps grows
    # with it; None is "not set" and reads as the model id's known window.
    context_window_tokens: int | None = None

    def count_spend(
        self,
        *,
        fresh_in: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        out: int = 0,
        model: object = None,
    ) -> None:
        """Put one billed call on the current deal's tab. No ledger, no deal, or
        no tokens counts nothing, and counting never fails a model call."""
        ledger = self.cost_ledger
        if ledger is None:
            return
        try:
            ledger.add(
                model or self.model_id,
                basis=self.cost_basis,
                fresh_in=fresh_in,
                cache_read=cache_read,
                cache_write=cache_write,
                out=out,
            )
        except Exception:  # noqa: BLE001 - the bill is never worth the call
            pass

    @property
    @abstractmethod
    def model_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        raise NotImplementedError

    def caches_a_stable_prefix(self) -> bool:
        """Does repeating one block in front of every call cost less here?

        FALSE IS THE HONEST DEFAULT and it is what every adapter that does not
        answer gets: an unknown provider is charged full price for every byte,
        so a caller that would move something into a repeated prefix must keep
        sending it once instead. Adapters that know better say so.
        """
        return False
