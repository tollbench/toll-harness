"""THE RUN STOPS BEFORE THE PROVIDER DOES.

WHAT FORCED THIS MODULE (production fleet, 2026-09-09). The fleet's model
carries a 131,072-token context. Peter's run on "find a researcher's email and
reach out" reached 129,025 input tokens on one call and died inside the
provider:

    bedrock ValidationException ... This model's maximum context length is
    131072 tokens. However, you requested 2048 output tokens and your prompt
    contains at least 129025 input tokens

Greg hit it twelve times that day, Marcia ten, Bobby six. Nothing in the
harness was watching: every tool result was appended to the conversation
whatever its size, the brief alone carried twelve worked programs (~19k
tokens), and the model called the validate door fourteen times in three
minutes with the whole submitted plan echoed back on every answer. The run
did not stop -- it was stopped, by a 400, with no record of the step it was
on and no honest refusal to read.

So the harness meters itself. The budget is measured in INPUT tokens, read
from the provider's own usage fields after every model call, because the
prompt is the thing that overflows: the last call's input token count IS the
conversation's size, and the next call is that plus whatever was appended
since. When the next call would cross the line the run ends with
``context_budget_exceeded``, naming the step it was on and the last tool it
called. A run that stops itself leaves a record; a run the provider stops
leaves a stack trace.

The default is 90,000: comfortably under the 131,072 the fleet runs on, with
room for one more large tool result and the 2,048 output tokens the adapters
reserve. Configure it per agent with ``runtime.context_budget_tokens`` or
across a fleet with ``TOLL_HARNESS_CONTEXT_BUDGET_TOKENS``; 0 turns the guard
off, which is a thing to do on a model with a million-token window, not a
thing to do to make a failing run pass.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from toll_harness.core.types import ModelUsage

# Input tokens per run, measured from the provider's usage fields.
DEFAULT_CONTEXT_BUDGET_TOKENS = 90_000

# Fleet-wide lever. A per-agent `runtime.context_budget_tokens` wins over it.
CONTEXT_BUDGET_ENV = "TOLL_HARNESS_CONTEXT_BUDGET_TOKENS"

# How many characters of serialized JSON stand in for one token when the only
# thing we have is text we have not sent yet. Deliberately pessimistic (real
# JSON runs nearer 3.5) so an estimate is never the reason a call overflows.
CHARS_PER_TOKEN = 4

TERMINAL_REASON = "context_budget_exceeded"


def resolve_context_budget(configured: Any = None) -> int:
    """The budget for this run: the config, else the environment, else 90,000.

    Anything unreadable falls back to the default rather than raising -- a
    typo in a fleet-wide environment variable must not stop seven agents.
    """
    for value in (configured, os.environ.get(CONTEXT_BUDGET_ENV)):
        if value is None or value == "":
            continue
        try:
            budget = int(value)
        except (TypeError, ValueError):
            continue
        return max(0, budget)
    return DEFAULT_CONTEXT_BUDGET_TOKENS


def measure(value: Any) -> int:
    """The character size of anything, as it would ride the wire."""
    try:
        return len(json.dumps(value, separators=(",", ":"), default=str))
    except (TypeError, ValueError):
        return len(str(value))


@dataclass
class ContextBudget:
    """What the provider said, and what the next call would cost.

    ``limit`` is input tokens; 0 disables the guard. Everything else is read
    back off the provider's own usage numbers, so this class never guesses at
    a count it could be told.
    """

    limit: int = DEFAULT_CONTEXT_BUDGET_TOKENS
    calls: int = 0
    last_input_tokens: int = 0
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    # Serialized size of the conversation the last measured call was given.
    measured_chars: int = 0
    place: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def record(self, usage: ModelUsage, conversation_chars: int) -> None:
        """Take the provider's word for what that call cost."""
        self.calls += 1
        self.last_input_tokens = max(0, int(usage.input_tokens or 0))
        self.cumulative_input_tokens += self.last_input_tokens
        self.cumulative_output_tokens += max(0, int(usage.output_tokens or 0))
        self.measured_chars = max(0, int(conversation_chars))

    def estimate(self, conversation_chars: int) -> int:
        """What the next call's prompt would be, in input tokens.

        The last measured prompt, plus a token for every four characters
        appended since. Before the first call there is nothing to measure and
        the estimate is the text itself.
        """
        grown = max(0, int(conversation_chars) - self.measured_chars)
        if self.calls == 0:
            return int(conversation_chars) // CHARS_PER_TOKEN
        return self.last_input_tokens + grown // CHARS_PER_TOKEN

    def would_exceed(self, conversation_chars: int) -> bool:
        if not self.enabled:
            return False
        return self.estimate(conversation_chars) > self.limit

    def line(self) -> str:
        """One line per model call, so a run's growth reads off the log."""
        return (
            f"model call {self.calls}: prompt {self.last_input_tokens} input "
            f"tokens, cumulative input {self.cumulative_input_tokens}, "
            f"budget {self.limit or 'off'}"
        )

    def report(self, conversation_chars: int) -> dict[str, Any]:
        """The terminal result: what the budget was, and where the run was."""
        payload: dict[str, Any] = {
            "reason": "Context budget exceeded",
            "error": TERMINAL_REASON,
            "budget_input_tokens": self.limit,
            "last_call_input_tokens": self.last_input_tokens,
            "estimated_next_input_tokens": self.estimate(conversation_chars),
            "cumulative_input_tokens": self.cumulative_input_tokens,
            "cumulative_output_tokens": self.cumulative_output_tokens,
            "model_calls": self.calls,
            "message": (
                "The run stopped itself. The next model call's prompt would "
                "have crossed this run's input-token budget, and a prompt over "
                "the model's context window is refused by the provider with "
                "nothing filed and nothing recorded. Nothing was lost: the "
                "work already filed stands. Raise runtime.context_budget_tokens "
                "only if the model's window is genuinely larger; otherwise the "
                "fix is a smaller tool result, not a bigger budget."
            ),
        }
        payload.update({key: value for key, value in self.place.items() if value not in (None, "")})
        return payload
