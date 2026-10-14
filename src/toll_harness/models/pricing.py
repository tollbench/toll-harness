"""What a model call costs, from a small table.

THE TABLE IS A TABLE, NOT A BILL. Dollars per million tokens, nearest published
list prices; several rows are placeholders until they are re-checked against an
invoice (marked below). A model that is not here is counted in tokens and
priced as unknown (``price_usd`` returns ``None``), never guessed.

Used for the optional ``agent_cost`` hand-back slot (0.63.0). Nothing here
touches a model call or a wire; it only turns token counts into dollars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rate:
    """Dollars per million tokens."""

    input: float
    cache_read: float
    cache_write: float
    output: float


# model id (normalized, see ``normalize_model``) -> rate.
#   claude-haiku-4-5    published list price (1 / 5, cache read 0.1x, write 1.25x).
#   claude-opus-5-5     the Opus-class price the bench's own kit agent uses.
#   gpt-6-1-sol         the GPT-class price the bench's own kit agent uses.
#   PLACEHOLDERS, RE-CHECK AGAINST THE BILL: claude-sonnet-5-5 (Sonnet class),
#   claude-fable-5-1 (priced as Opus class), gpt-6-astra (priced as gpt-6-1-sol).
PRICES: dict[str, Rate] = {
    "claude-opus-5-5": Rate(5.0, 0.5, 6.25, 25.0),
    "claude-fable-5-1": Rate(5.0, 0.5, 6.25, 25.0),
    "claude-sonnet-5-5": Rate(3.0, 0.3, 3.75, 15.0),
    "claude-haiku-4-5": Rate(1.0, 0.1, 1.25, 5.0),
    "gpt-6-1-sol": Rate(1.25, 0.125, 1.25, 10.0),
    "gpt-6-astra": Rate(1.25, 0.125, 1.25, 10.0),
}

_PROVIDER_PREFIX = re.compile(r"^(?:(?:us|eu|apac|global)\.)?(?:anthropic|openai)[./]")
_DATE_SUFFIX = re.compile(r"-(?:20\d{6})$")
_BEDROCK_SUFFIX = re.compile(r"-v\d+(?::\d+)?$")


def normalize_model(model_id: object) -> str:
    """The table key for a model id as a provider or CLI spells it.

    Lower-cased; a Bedrock/provider prefix, a ``[1m]``-style tag, a ``@version``
    and a ``-v1:0`` or ``-YYYYMMDD`` suffix are dropped; dots become dashes so
    ``claude-opus-5.5`` and ``claude-opus-5-5`` are one model.
    """
    text = str(model_id or "").strip().lower()
    text = re.sub(r"\[.*?\]", "", text)
    text = text.split("@", 1)[0]
    text = _PROVIDER_PREFIX.sub("", text)
    text = _BEDROCK_SUFFIX.sub("", text)
    text = _DATE_SUFFIX.sub("", text)
    return text.replace(".", "-").strip("-")


def rate_for(model_id: object) -> Rate | None:
    return PRICES.get(normalize_model(model_id))


def price_usd(
    model_id: object,
    *,
    fresh_in: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    out: int = 0,
) -> float | None:
    """Dollars for these tokens, or None when the model is not in the table.

    ``fresh_in`` is the input that was not served from or written to a cache.
    """
    rate = rate_for(model_id)
    if rate is None:
        return None
    return (
        max(0, fresh_in) * rate.input
        + max(0, cache_read) * rate.cache_read
        + max(0, cache_write) * rate.cache_write
        + max(0, out) * rate.output
    ) / 1_000_000
