"""THE ROOM A PROMPT GETS IS THE MODEL'S WINDOW, NOT A NUMBER FROM 2026-09.

WHAT FORCED THIS MODULE (lab agent Nia, DJ 4-Fun, claude-opus-5-5 on the
claude_code rail, 2026-10-26). Steven asked her to revise a song workflow and
her log shows the harness cutting what she saw:

    Brief for target 8d06fe06 ran to 41352 characters; shed bid_template,
    block_templates to fit the window (budget 60000)
    draft loop tail ran to 14000 characters (budget 8000) with nothing left to
    shed but the question
    draft loop tail was 21379 characters (budget 16000); dropped `example` ...
    dropped `tools_on_this_want` ... dropped `forecast_context`

So she bid and planned without the block menu, the bid template, the example,
her tools on this want and the forecast context. Every one of those numbers
was set against the 131,072-token window the fleet ran on in September
(core/budget.py), and none of them knew what model it was guarding. Steven,
2026-10-06: "I dont want the harness to hobble, we want to make sure the
agents have full capabilities."

THE RULE, ONE LINE. Every prompt budget the harness keeps (the brief a tool
hands back, each draft-loop ask, the step ask, the tools index, what the
person said, the operator's materials, one step) is the number it always was,
times the model's window over 131,072 -- and never less than the number it
always was. A model with a window at or under 131,072 (or one this package
cannot name) keeps exactly the numbers it ran on before 0.73.0, and still
sheds; a 200,000-token model gets 1.5 times the room and a 1,000,000-token
model 7.6 times.

THE WINDOW IS A SETTING. `model.context_window_tokens` in agent.yaml wins, then
TOLL_HARNESS_CONTEXT_WINDOW_TOKENS across a fleet, then the small table below,
then 131,072. THE TABLE IS CONSERVATIVE BY DESIGN: a Claude id is 200,000
tokens, the window every Claude since Claude 3 has, and 1,000,000 only when the
id itself carries the `[1m]` tag. Several Claude models do run a 1M window
(Opus 4.6 and later, Sonnet 4.6 and later, Fable), but whether a given rail,
account and id actually gets it is the operator's to say, in agent.yaml or on
the id -- a guess too big is a prompt the provider refuses. A model the table
does not list is never guessed at.
"""

from __future__ import annotations

import os
import re
from typing import Any

# The window today's prompt budgets were set against: the fleet's model on
# 2026-09-09 (core/budget.py). Also the window assumed for a model this
# package cannot name, so an unknown model's prompts are byte for byte what
# they were before 0.73.0.
REFERENCE_WINDOW_TOKENS = 131_072
DEFAULT_CONTEXT_WINDOW_TOKENS = REFERENCE_WINDOW_TOKENS

# Fleet-wide lever. A per-agent `model.context_window_tokens` wins over it.
CONTEXT_WINDOW_ENV = "TOLL_HARNESS_CONTEXT_WINDOW_TOKENS"

ONE_MILLION = 1_000_000

# First match wins, on the model id lower-cased with any provider prefix
# (`us.anthropic.`, `anthropic/`, `google/`) taken off. A `[1m]` tag on the id
# is the 1M window and is read before this table; a plain Claude id is never
# given more than 200,000 here, whatever the model can do.
KNOWN_WINDOWS: tuple[tuple[str, int], ...] = (
    # Every Claude since Claude 3, as a plain id (claude-opus-5-5 included).
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


def window_of(model: Any) -> int:
    """The window of a built model adapter.

    `context_window_tokens` is set on every adapter `config.build_model_adapter`
    builds. An adapter built some other way (a test's fake, a caller's own) is
    resolved from its model id, else the environment, else 131,072.
    """
    tokens = _positive_int(getattr(model, "context_window_tokens", None))
    if tokens is not None:
        return tokens
    try:
        model_id = getattr(model, "model_id", None)
    except Exception:  # noqa: BLE001 - an abstract or failing property is "unknown"
        model_id = None
    return resolve_context_window(None, model_id if isinstance(model_id, str) else None)


def room(base: int, window_tokens: Any = None) -> int:
    """A prompt budget for this window: `base` was set against 131,072 tokens.

    It grows in proportion to the window and never shrinks below `base`, so a
    small or unknown window keeps the budget it always had and sheds exactly
    where it shed before -- only in the new order.
    """
    tokens = _positive_int(window_tokens) or DEFAULT_CONTEXT_WINDOW_TOKENS
    if tokens <= REFERENCE_WINDOW_TOKENS:
        return int(base)
    return int(base) * tokens // REFERENCE_WINDOW_TOKENS
