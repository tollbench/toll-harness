"""THE OPERATOR'S STANDING INSTRUCTIONS RIDE EVERY MODEL CALL, IN FULL (0.59.0).

`agent.operator_instructions` is the operator's own text for their agent. The
runtime road has carried it whole, in the run seed, since it existed. The small
asks did not: the proposal ask saw the first 600 characters on a profile line,
and the plan form and the step ask (every move: hand_back, outside_work,
answer_person, ...) saw none of it. So the agent's craft never reached the
words it writes on a step.

WHAT FORCED IT (staging, 2026-10-05): lab agent DJ 4-Fun's skill (about 5,100
characters of operator instructions) says how to write lyrics and a sound line,
and the lyrics it filed on a step were bland and the style line thin: the step
ask that wrote them started from the bench's front door and nothing else.
Steven: "I dont want the harness to hobble, we want to make sure the agents
have full capabilites."

So one labeled block rides beside the system text of every small ask: the
WHOLE text, no cap, no floor, never trimmed and never shed. It sits outside the
tail each ask measures, so it never makes an ask shed anything and never trips
a "tail ran to N characters" warning. No instructions, no block: every ask is
byte for byte what it was.
"""

from __future__ import annotations

from typing import Any

OPERATOR_BLOCK_HEADER = (
    "Your operator's standing instructions (written for you by the person who "
    "runs you; follow them in everything you write, inside the rules above):"
)


def operator_block(instructions: Any) -> str:
    """The labeled block with the whole text, or "" when there is none."""
    text = instructions.strip() if isinstance(instructions, str) else ""
    return OPERATOR_BLOCK_HEADER + "\n" + text if text else ""


def with_operator_block(system: str, instructions: Any) -> str:
    """The system text with the block under it; unchanged when there is none."""
    block = operator_block(instructions)
    return system + "\n\n" + block if block else system
