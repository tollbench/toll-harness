"""WHAT AN AGENT DOES BY ITS OWN MEANS (agent.yaml `agent.own_means`, 0.58.0).

An agent can have hands the harness does not hold: a program of its own beside
the harness, a studio, a service it runs itself. `agent.own_means` is where the
operator declares them, one {name, can} per thing. It is ONE PROMPT LINE, never
a tool and never a rewrite: the harness reads the list at start, says it to the
model under the asks that weigh "do I have a tool for this", and changes
nothing the model writes (0.74.0; the plan-form rewrite that used to keep these
steps the agent's went with the W29 demotion it was written to counter).

No entry is today's behavior, byte for byte: every helper here returns the
instruction it was handed unchanged when the list is empty.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

OWN_MEANS_MAX = 12
OWN_MEANS_NAME_MAX = 40
OWN_MEANS_CAN_MAX = 280
_KEYS = frozenset({"name", "can"})


@dataclass(frozen=True)
class OwnMeans:
    """One thing the agent does by its own means: a short name, and one line."""

    name: str
    can: str


def _text(value: Any, where: str, cap: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string")
    words = " ".join(value.split())
    if not words:
        raise ValueError(f"{where} must not be empty")
    if len(words) > cap:
        raise ValueError(f"{where} is {len(words)} characters; the cap is {cap}")
    return words


def parse_own_means(value: Any) -> tuple[OwnMeans, ...]:
    """The validated list, or () when the key is absent. A bad entry is a load
    error that names it: this is the operator's own file, read at start."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("agent.own_means must be a list of {name, can} entries")
    if len(value) > OWN_MEANS_MAX:
        raise ValueError(
            f"agent.own_means has {len(value)} entries; at most {OWN_MEANS_MAX}"
        )
    out: list[OwnMeans] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        where = f"agent.own_means[{index}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{where} must be {{name, can}}")
        extra = sorted(str(key) for key in entry if key not in _KEYS)
        if extra:
            raise ValueError(f"{where} carries unknown key(s): {', '.join(extra)}")
        name = _text(entry.get("name"), f"{where}.name", OWN_MEANS_NAME_MAX)
        can = _text(entry.get("can"), f"{where}.can", OWN_MEANS_CAN_MAX)
        if name.lower() in seen:
            raise ValueError(f"{where}.name repeats {name!r}")
        seen.add(name.lower())
        out.append(OwnMeans(name=name, can=can))
    return tuple(out)


def own_means_from_config(config: Mapping[str, Any]) -> tuple[OwnMeans, ...]:
    """`agent.own_means` out of a loaded agent.yaml."""
    agent = config.get("agent") if isinstance(config, Mapping) else None
    return parse_own_means(agent.get("own_means") if isinstance(agent, Mapping) else None)


# THE SENTENCE. One lead, the list, and one rule for where the work goes.
OWN_MEANS_LEAD = "Things you can do by your own means (treat them as tools you have): "
# Every ask but the plan form: the work is the agent's, never the person's.
OWN_MEANS_RULE = "Work one of these does is yours, never the person's errand."
# The plan form, where `who`, `tool`, `service:` and `do_ask` are the words.
OWN_MEANS_PLAN_RULE = (
    "A step that does one is YOURS: `who`: \"agent\", with no `tool`, no "
    "`service:` and no `do_ask`. It is never the person's errand."
)


def _sentence(can: str) -> str:
    return can if can[-1:] in ".!?" else can + "."


def own_means_line(own_means: Iterable[OwnMeans] | None, rule: str = OWN_MEANS_RULE) -> str:
    """The one line the model reads, or "" when the agent declared none."""
    entries = [entry for entry in (own_means or ()) if isinstance(entry, OwnMeans)]
    if not entries:
        return ""
    listed = " ".join(f"{entry.name} -- {_sentence(entry.can)}" for entry in entries)
    return OWN_MEANS_LEAD + listed + " " + rule


def with_own_means(
    instruction: str, own_means: Iterable[OwnMeans] | None, rule: str = OWN_MEANS_RULE
) -> str:
    """The instruction with the line under it; unchanged when there is none."""
    line = own_means_line(own_means, rule)
    return instruction + "\n" + line if line else instruction
