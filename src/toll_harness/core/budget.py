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

The default is 90,000 on a 131,072-token window: comfortably under it, with
room for one more large tool result and the 2,048 output tokens the adapters
reserve. Since 0.74.0 THE DEFAULT FOLLOWS THE MODEL'S WINDOW in the same
proportion (137,329 on 200,000; 686,645 on 1,000,000): this is the ONE budget
the harness keeps, and it is a run's, never a prompt's. Configure it per agent
with ``runtime.context_budget_tokens`` or across a fleet with
``TOLL_HARNESS_CONTEXT_BUDGET_TOKENS``; 0 turns the guard off.

THE WINDOW IS A SETTING (0.73.0). ``model.context_window_tokens`` in
agent.yaml wins, then ``TOLL_HARNESS_CONTEXT_WINDOW_TOKENS``, then a small
conservative table (a plain Claude id is 200,000, an id carrying ``[1m]`` is
1,000,000, Gemini 2/3 is 1,048,576), then 131,072. A model the table does not
list is never guessed at.
"""

from __future__ import annotations

import json
import os
import re
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

# The window the 90,000 default was set against (the fleet's model, 2026-09-09),
# and the window assumed for a model this package cannot name.
REFERENCE_WINDOW_TOKENS = 131_072
DEFAULT_CONTEXT_WINDOW_TOKENS = REFERENCE_WINDOW_TOKENS
# Fleet-wide lever. A per-agent `model.context_window_tokens` wins over it.
CONTEXT_WINDOW_ENV = "TOLL_HARNESS_CONTEXT_WINDOW_TOKENS"
ONE_MILLION = 1_000_000
# First match wins, on the lower-cased id with any provider prefix taken off.
KNOWN_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude-", 200_000),
    ("gemini-2", 1_048_576),
    ("gemini-3", 1_048_576),
)
_PREFIX = re.compile(r"^(?:(?:us|eu|apac|global)\.)?(?:anthropic|google|openai)[./]")


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip().replace("_", ""))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def known_window(model_id: Any) -> int | None:
    """The window this package can source for a model id, or None."""
    text = str(model_id or "").strip().lower()
    if not text:
        return None
    if "[1m]" in text:
        return ONE_MILLION
    text = _PREFIX.sub("", text.split("@", 1)[0]).replace(".", "-")
    for prefix, tokens in KNOWN_WINDOWS:
        if text.startswith(prefix):
            return tokens
    return None


def resolve_context_window(configured: Any = None, model_id: Any = None) -> int:
    """The window for this agent: the config, the environment, the table, 131,072.

    An unreadable or non-positive setting falls through to the next source
    rather than raising: a window that cannot be read is never a reason a
    model call does not happen.
    """
    for value in (configured, os.environ.get(CONTEXT_WINDOW_ENV)):
        tokens = _positive_int(value)
        if tokens is not None:
            return tokens
    return known_window(model_id) or DEFAULT_CONTEXT_WINDOW_TOKENS


def resolve_context_budget(configured: Any = None, window_tokens: Any = None) -> int:
    """The budget for this run: the config, else the environment, else the
    default for this model's window (90,000 on 131,072, in proportion above
    it, never below 90,000).

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
    window = _positive_int(window_tokens) or REFERENCE_WINDOW_TOKENS
    if window <= REFERENCE_WINDOW_TOKENS:
        return DEFAULT_CONTEXT_BUDGET_TOKENS
    return DEFAULT_CONTEXT_BUDGET_TOKENS * window // REFERENCE_WINDOW_TOKENS


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
