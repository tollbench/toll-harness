"""THE THREE ROW SLOTS ON A PROPOSAL (bench contract 4.1.3, Steven 2026-09-29).

WHAT FORCED THEM. The person now chooses between proposals on one row each
(the "A1 Essential" row): an approach strip across the top, the agent's
capability lines, and "Your part: N steps". The proposal had no hole for any
of the three, so the bench added three OPTIONAL slots to the proposal door:

  approach       one stop on each of three dials, in the dials' own words:
                 {"risk": "Careful", "finish": "Polished", "path": "Proven path"}
  capabilities   up to two short lines (60 characters each), in the agent's
                 own words: what it brings to THIS want that another may not
  step_estimate  {"total": 7, "you": 2}: the first guess at the plan's size,
                 and how many of its steps need the person

Steven, the same day: "we need to get them to choose a strategy then design
it based on that." So the draft loop asks the model to pick the approach
FIRST and to write the rest of the proposal to follow it, and an agent may
carry a standing default approach (agent.yaml `strategy.approach`, asked at
`init`, sent as `approach` at registration).

SLOTS, NOT RULES. None of the three is required. The bench refuses only the
wrong kind of thing (an unknown dial word, a half-filled approach, `you`
above `total`) and trims a long capability line. So nothing here ever makes a
proposal fail: it settles the spelling the door takes, and drops what it
cannot settle, saying what it dropped. A slot the door still refuses is
dropped and the proposal goes once more without it (`named_in_refusal`).
"""
from __future__ import annotations

import re
from typing import Any

ROW_SLOT_FIELDS: tuple[str, ...] = ("approach", "capabilities", "step_estimate")

# THE DIALS, in the bench's own words and order (strategy_requirements
# STRATEGY_AXES and its middle stops). The row prints these three words.
APPROACH_DIALS: dict[str, tuple[str, str, str]] = {
    "risk": ("Careful", "Middle risk", "Aggressive"),
    "finish": ("Scrappy", "Middle finish", "Polished"),
    "path": ("Proven path", "Middle path", "Creative"),
}
APPROACH_EXAMPLE = {"risk": "Careful", "finish": "Polished", "path": "Proven path"}

# The brief's slider keys name the same three axes (person_context.strategy:
# risk, polish, novelty), and the door reads them as the dials too.
_DIAL_BY_KEY = {
    "risk": "risk",
    "finish": "finish",
    "polish": "finish",
    "path": "path",
    "novelty": "path",
}

# Spellings a model writes for a stop, folded (no case, no - or _). Index 0,
# 1, 2 is the stop in APPROACH_DIALS order. The dial's own words are added
# below, so this table is only the other ways of saying them.
_STOP_SPELLINGS: dict[str, dict[str, int]] = {
    "risk": {
        "cautious": 0, "safe": 0, "low": 0, "low risk": 0, "conservative": 0,
        "mid": 1, "medium": 1, "moderate": 1, "balanced": 1, "medium risk": 1,
        "mid risk": 1, "middle": 1,
        "bold": 2, "high": 2, "high risk": 2,
    },
    "finish": {
        "rough": 0, "quick": 0, "fast": 0, "lean": 0,
        "mid": 1, "medium": 1, "balanced": 1, "mid finish": 1, "middle": 1,
        "polish": 2, "refined": 2, "premium": 2,
    },
    "path": {
        "proven": 0, "standard": 0, "conventional": 0, "tried and true": 0,
        "mid": 1, "medium": 1, "balanced": 1, "mid path": 1, "middle": 1,
        "novel": 2, "inventive": 2,
    },
}

CAPABILITY_LINES_MAX = 2
CAPABILITY_LINE_MAX = 60
_CAPABILITY_WORD_KEYS = ("line", "capability", "text", "label", "name", "title")

_TOTAL_KEYS = ("total", "steps", "total_steps", "count")
_YOU_KEYS = ("you", "person", "person_steps", "yours", "your_part", "human", "human_steps")


def fold(word: Any) -> str:
    """One spelling for a dial word: no case, no - or _, single spaces."""
    return " ".join(str(word).replace("_", " ").replace("-", " ").split()).casefold()


def _stops(dial: str) -> dict[str, int]:
    table = {fold(word): index for index, word in enumerate(APPROACH_DIALS[dial])}
    table.update(_STOP_SPELLINGS[dial])
    return table


def dial_word(dial: str, raw: Any) -> str | None:
    """The dial's own word for `raw`, or None when it is not one of its stops."""
    if dial not in APPROACH_DIALS or not isinstance(raw, str) or not raw.strip():
        return None
    index = _stops(dial).get(fold(raw))
    return None if index is None else APPROACH_DIALS[dial][index]


def _dial_of_key(key: Any) -> str | None:
    return _DIAL_BY_KEY.get(fold(key).replace(" ", "_"))


def _dials_for_word(word: str) -> list[tuple[str, str]]:
    """Every (dial, word) a loose word could be. "middle" is all three."""
    found = []
    for dial in APPROACH_DIALS:
        settled = dial_word(dial, word)
        if settled:
            found.append((dial, settled))
    return found


def _ordered(approach: dict[str, str]) -> dict[str, str]:
    return {dial: approach[dial] for dial in APPROACH_DIALS if dial in approach}


def normalize_approach(value: Any) -> tuple[dict[str, str] | None, list[str]]:
    """(approach, notes). The dials that could be settled, in their own words.

    It may be PARTIAL: a dial whose word is not one of its stops is dropped
    and said in `notes`, never refused. `complete_approach` decides what a
    partial one becomes, because the door takes all three or none.
    Also read: a list or a "Careful / Polished / Proven path" string, each
    word matched to the one dial it belongs to.
    """
    notes: list[str] = []
    if value is None or value == "" or value == {} or value == []:
        return None, notes
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, raw in value.items():
            dial = _dial_of_key(key)
            if dial is None:
                notes.append(f'approach has no dial called "{str(key)[:40]}"; dropped it')
                continue
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            word = dial_word(dial, raw)
            if word is None:
                notes.append(
                    f'approach.{dial} "{str(raw)[:40]}" is not one of '
                    f"{', '.join(APPROACH_DIALS[dial])}; dropped that dial"
                )
                continue
            if dial in out and out[dial] != word:
                notes.append(f"approach names the {dial} dial twice; kept {out[dial]}")
                continue
            out[dial] = word
        return (_ordered(out) or None), notes
    if isinstance(value, str):
        value = [part for part in re.split(r"[/,|;·]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        for raw in value:
            if not isinstance(raw, str) or not raw.strip():
                continue
            found = _dials_for_word(raw)
            if len(found) != 1:
                notes.append(
                    f'approach word "{raw.strip()[:40]}" names no single dial; dropped it'
                )
                continue
            dial, word = found[0]
            if dial not in out:
                out[dial] = word
        return (_ordered(out) or None), notes
    notes.append("approach is not an object of three dials; dropped it")
    return None, notes


def is_complete(approach: Any) -> bool:
    return isinstance(approach, dict) and all(
        dial_word(dial, approach.get(dial)) for dial in APPROACH_DIALS
    )


def complete_approach(
    approach: Any, default: Any = None
) -> tuple[dict[str, str] | None, list[str]]:
    """(approach, notes): all three dials, or None.

    The door takes one stop on EACH dial or none at all (a half-filled
    approach is its one refusal for the slot). A dial the model left out is
    filled from the agent's own default when the default has it; if a dial is
    still empty, the approach is dropped whole rather than sent half-filled.
    """
    notes: list[str] = []
    settled, read_notes = normalize_approach(approach)
    notes.extend(read_notes)
    fallback, _ = normalize_approach(default)
    fallback = fallback or {}
    out = dict(settled or {})
    filled = [dial for dial in APPROACH_DIALS if dial not in out and dial in fallback]
    for dial in filled:
        out[dial] = fallback[dial]
    if not out:
        return None, notes
    missing = [dial for dial in APPROACH_DIALS if dial not in out]
    if missing:
        notes.append(
            "approach had no stop on " + " and ".join(missing)
            + "; dropped it (the bench takes all three dials or none)"
        )
        return None, notes
    if filled:
        notes.append(
            "approach came from the agent's default"
            if not settled
            else "approach took " + ", ".join(filled) + " from the agent's default"
        )
    return _ordered(out), notes


def _cut(line: str, cap: int) -> str:
    """At the last word that fits; mid-word only when one word is too long."""
    if len(line) <= cap:
        return line
    head = line[:cap]
    if line[cap].isspace():
        return head.rstrip()
    space = head.rfind(" ")
    return head[:space].rstrip() if space > 0 else head


def normalize_capabilities(value: Any) -> tuple[list[str], list[str]]:
    """(lines, notes): up to two lines of up to 60 characters.

    A bare string is one line. A third line is dropped and a long one is cut
    at a word, both said in `notes` (the bench would cut the same way and
    report it on `trimmed`).
    """
    notes: list[str] = []
    if value is None:
        return [], notes
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        notes.append("capabilities is not a list of lines; dropped it")
        return [], notes
    lines: list[str] = []
    seen: set[str] = set()
    for entry in value:
        if isinstance(entry, dict):
            entry = next(
                (entry[key] for key in _CAPABILITY_WORD_KEYS if isinstance(entry.get(key), str)),
                None,
            )
        if not isinstance(entry, str):
            continue
        line = " ".join(entry.split())
        if not line or line.casefold() in seen:
            continue
        seen.add(line.casefold())
        lines.append(line)
    if len(lines) > CAPABILITY_LINES_MAX:
        notes.append(
            f"capabilities had {len(lines)} lines; kept the first {CAPABILITY_LINES_MAX}"
        )
        lines = lines[:CAPABILITY_LINES_MAX]
    out = []
    for line in lines:
        cut = _cut(line, CAPABILITY_LINE_MAX)
        if cut != line:
            notes.append(
                f"a capability line was {len(line)} characters; cut at a word to {len(cut)}"
            )
        out.append(cut)
    return out, notes


def _count(raw: Any) -> int | None:
    """A whole number, or None. "7" and 7.0 read as 7; True does not."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw == raw and abs(raw) != float("inf"):
        return int(round(raw))
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    return None


def normalize_step_estimate(value: Any) -> tuple[dict[str, int] | None, list[str]]:
    """(estimate, notes): {"total": >= 1, "you": 0..total}, or None.

    `you` above `total` is clamped to `total` and a negative `you` to 0, both
    said. A half-filled estimate (no total, or no you) is dropped rather than
    guessed: the door refuses it, and a made-up count is worse than none.
    """
    notes: list[str] = []
    if value is None or value == {} or value == "":
        return None, notes
    total_raw = you_raw = None
    if isinstance(value, dict):
        total_raw = next((value[key] for key in _TOTAL_KEYS if value.get(key) is not None), None)
        you_raw = next((value[key] for key in _YOU_KEYS if value.get(key) is not None), None)
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        total_raw, you_raw = value
    else:
        notes.append("step_estimate is not {total, you}; dropped it")
        return None, notes
    total, you = _count(total_raw), _count(you_raw)
    if total is None and you is None:
        return None, notes
    if total is None or total < 1:
        notes.append("step_estimate had no total of 1 or more; dropped it")
        return None, notes
    if you is None:
        notes.append("step_estimate had a total and no `you`; dropped it")
        return None, notes
    if you < 0:
        notes.append(f"step_estimate.you was {you}; read it as 0")
        you = 0
    if you > total:
        notes.append(f"step_estimate.you was {you}, above total {total}; clamped to {total}")
        you = total
    return {"total": total, "you": you}, notes


def read_row_slots(holder: Any) -> tuple[dict[str, Any], list[str]]:
    """The three slots a model's answer carries, settled. (slots, notes).

    Only a slot that settles is in `slots`; an approach may still be partial
    here (the draft loop completes it from the agent's default).
    """
    slots: dict[str, Any] = {}
    notes: list[str] = []
    if not isinstance(holder, dict):
        return slots, notes
    if "approach" in holder:
        approach, said = normalize_approach(holder.get("approach"))
        notes.extend(said)
        if approach:
            slots["approach"] = approach
    if "capabilities" in holder:
        lines, said = normalize_capabilities(holder.get("capabilities"))
        notes.extend(said)
        if lines:
            slots["capabilities"] = lines
    if "step_estimate" in holder:
        estimate, said = normalize_step_estimate(holder.get("step_estimate"))
        notes.extend(said)
        if estimate:
            slots["step_estimate"] = estimate
    return slots, notes


def settle(proposal: Any) -> tuple[Any, list[str]]:
    """Every row slot on a proposal in the door's spelling. (proposal, notes).

    Idempotent. A slot that cannot be settled (a half-filled approach, a
    half-filled estimate, no lines left) comes off, and `notes` says so. A
    proposal with none of the three comes back as it was.
    """
    if not isinstance(proposal, dict) or not any(key in proposal for key in ROW_SLOT_FIELDS):
        return proposal, []
    slots, notes = read_row_slots(proposal)
    if "approach" in slots and not is_complete(slots["approach"]):
        _whole, said = complete_approach(slots["approach"])
        notes.extend(said)
        slots.pop("approach")
    out = {key: value for key, value in proposal.items() if key not in ROW_SLOT_FIELDS}
    out.update(slots)
    if out == proposal:
        return proposal, []
    said = [
        f"{key}: settled to the door's spelling" if key in out else f"{key}: empty, left off"
        for key in ROW_SLOT_FIELDS
        if proposal.get(key) != out.get(key)
    ]
    return out, notes or said


def configured_approach(config: Any) -> tuple[dict[str, str] | None, list[str]]:
    """The agent's standing default approach from agent.yaml, or None.

    `strategy.approach: {risk, finish, path}`, each dial optional. An
    agent.yaml without the key (every one before 0.57.0) has no default; a
    word that is not a stop is dropped and said, never a load error.
    """
    strategy = config.get("strategy") if isinstance(config, dict) else None
    if not isinstance(strategy, dict):
        return None, []
    return normalize_approach(strategy.get("approach"))


# ---------------------------------------------------------------------------
# A refusal that names a row slot
# ---------------------------------------------------------------------------
_STRUCTURED_KEYS = ("field", "path", "slot", "param", "key")
# The slot at the head of a sentence or after a space or quote, followed by
# `.dial`, `[n]`, a closing quote, or a verb. "approach" and "capabilities"
# are ordinary English words too, so a bare mention in passing is not a name.
_NAMED_IN_TEXT = re.compile(
    r"(?:^|[\s\"'`(\[])(approach|capabilities|step_estimate)"
    r"(?:[.\[][A-Za-z0-9_]|[\"'`]|\s+(?:is|has|takes|names|carries|must|was|are|"
    r"cannot|may|field|slot|key)\b)"
)


def _heads(value: Any) -> set[str]:
    """Slot names at the head of a field path: approach.risk -> approach."""
    found: set[str] = set()
    values = value if isinstance(value, (list, tuple)) else [value]
    for entry in values:
        if isinstance(entry, str):
            head = re.split(r"[.\[]", entry.strip(), maxsplit=1)[0]
            if head in ROW_SLOT_FIELDS:
                found.add(head)
    return found


def named_in_refusal(*answers: Any) -> list[str]:
    """Which of the three slots a door's refusal names, in slot order.

    Reads the structured places first (a problem row's `field`/`path`, the
    `keys` and `facts` it carries, every row of a `problems` list), then the
    sentence (`detail`, `message`, `error`, `say`, or a bare string), where
    the slot must stand as a field name: at the start, quoted, or followed by
    `.dial`, `[n]` or a verb ("approach.risk takes one of ...",
    "step_estimate.you is 9 but total is 3", "'approach' was unexpected").
    """
    found: set[str] = set()

    def visit(answer: Any, depth: int = 0) -> None:
        if depth > 4 or answer is None:
            return
        if isinstance(answer, str):
            found.update(match.group(1) for match in _NAMED_IN_TEXT.finditer(answer))
            return
        if isinstance(answer, (list, tuple)):
            for entry in answer:
                visit(entry, depth + 1)
            return
        if not isinstance(answer, dict):
            return
        # A ROW THAT SAYS WHICH FIELD IT IS ABOUT IS TAKEN AT ITS WORD. The
        # bench's problem rows carry `field`, so a row about the pitch whose
        # sentence happens to say "your approach is ..." is not read as a
        # refusal of the approach slot. Only a row with no field is read.
        named_field = False
        for key in (*_STRUCTURED_KEYS, "keys"):
            value = answer.get(key)
            if value and (isinstance(value, str) or isinstance(value, (list, tuple))):
                named_field = True
                found.update(_heads(value))
        if not named_field:
            for key in ("detail", "message", "error", "say", "problem_text"):
                if isinstance(answer.get(key), str):
                    visit(answer[key], depth + 1)
        for key in ("facts", "rejection", "problems", "errors"):
            if key in answer:
                visit(answer[key], depth + 1)

    for answer in answers:
        visit(answer)
    return [slot for slot in ROW_SLOT_FIELDS if slot in found]


def drop_named(
    proposal: Any, *answers: Any
) -> tuple[Any, list[str]]:
    """(proposal without the slots the refusal names, the slots dropped).

    Only a slot the proposal actually carries is dropped; a refusal about
    anything else leaves the proposal as it was and drops nothing.
    """
    if not isinstance(proposal, dict):
        return proposal, []
    named = [slot for slot in named_in_refusal(*answers) if slot in proposal]
    if not named:
        return proposal, []
    return {key: value for key, value in proposal.items() if key not in named}, named
