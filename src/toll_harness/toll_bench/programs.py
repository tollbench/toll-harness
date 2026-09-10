"""STEVEN, 2026-09-09 -- FIND THE NEAREST PROGRAM AND CHANGE WHAT DIFFERS.

"The test is can the AI use strategy to build the correct plan that can
execute" -- and "use our kit of parts to code (it's just a JSON file)". So the
brief stopped handing out only a blank form. It now publishes ``plan_examples``:
twelve WORKED PROGRAMS, each ``{key, title, wants_like, proposal}``, and each
proposal a complete bid that already passes the validate door. The move is not
to compose a plan out of the parts list. The move is to find the program
nearest this want, copy it whole, change only what the want makes different,
compile it at the validate door and file.

WHAT FORCED THIS MODULE. A model handed twelve programs and told to "use them"
reads them, absorbs the flavour, and then writes its own plan anyway -- and the
thing it writes is a plausible composition that the door refuses on a mechanic
one of the twelve already had right. There is no way to tell those two runs
apart from the outside: both file a plan, both cite the examples. So the pick
is made HERE, deterministically, before the model sees the brief (the chosen
program rides the brief inline, in front of the other eleven), and what the
model did with it is logged as a DIFF against that program. The foreman reads
one line and knows whether the run copied or composed.

Nothing in this module writes a word of the agent's or files anything. It
picks, it explains the pick in one sentence, and it counts what changed.
"""

from __future__ import annotations

import json
import re
from typing import Any

# The sentence every planning surface leads with. One move, four beats.
PROGRAM_FIRST_SENTENCE = (
    "FIND THE NEAREST PROGRAM, THEN CHANGE WHAT DIFFERS. The brief carries "
    "`plan_examples`: worked programs, each one a COMPLETE proposal that "
    "already passes the validate door, with `wants_like` naming the wants it "
    "is for. Do not compose a plan out of parts. (1) Pick the program nearest "
    "this want -- `nearest_program` on the brief is the harness's own pick and "
    "rides it inline. (2) Copy its `proposal` WHOLE. (3) Change only what THIS "
    "want makes different: the words (pitch, titles, promises, messages), the "
    "recipient, the numbers. Keep its shape -- its steps, its acts, its "
    "`connect_account` rows, its question formats -- because that shape is "
    "what passes. (4) Compile it: call toll_bench.validate_proposal with this "
    "target_id, fix every problem it names, and file once. A plan that shares "
    "no shape with any of the twelve is a plan nobody has ever run."
)

# What a program looks like when it arrives. Kept as words, not a schema: the
# bench owns the shape and this package must not refuse a key it grows.
PROGRAM_KEYS = ("key", "title", "wants_like", "proposal")

_WORD = re.compile(r"[a-z0-9]+")

# Words that carry no want in them. Deliberately short: this is a tie-breaker
# between twelve programs, not a search engine, and a stopword list that grows
# starts deciding things it should not.
_STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but for to of in on at by with from into over under is am
    are was were be been being do does did doing have has had having i me my
    we our you your they them their it its this that these those as if then
    than so not no nor can will would should could want wants wanted need
    needs get gets got make makes made help please someone something anything
    about up out down off just very much more most some any all one two
    """.split()
)

# Weighting. `wants_like` is the line the bench wrote to say WHICH WANTS this
# program is for, so a hit there is worth two of a hit in the title, which is
# a name and only incidentally descriptive.
WANTS_LIKE_WEIGHT = 2
TITLE_WEIGHT = 1


def _tokens(value: Any) -> set[str]:
    """The meaningful words of anything, as a set."""
    if value is None:
        return set()
    if isinstance(value, (list, tuple)):
        found: set[str] = set()
        for item in value:
            found |= _tokens(item)
        return found
    text = value if isinstance(value, str) else str(value)
    return {
        word
        for word in _WORD.findall(text.lower())
        if len(word) > 2 and word not in _STOPWORDS
    }


def program_steps(example: Any) -> int:
    """How many steps this program carries. The tie-breaker.

    Read off the proposal when the whole program is in hand, and off the
    index row's own `steps` count when it is not: since 0.34.0 a brief may
    carry twelve INDEX ROWS and one program, and the pick has to be made the
    same way over either.
    """
    if not isinstance(example, dict):
        return 0
    steps = (example.get("proposal") or {}).get("steps")
    if isinstance(steps, list):
        return len(steps)
    count = example.get("steps")
    if isinstance(count, int) and not isinstance(count, bool):
        return count
    return 0


def score_program(want: Any, example: Any) -> int:
    """Token overlap of the want with one program's `wants_like` and title.

    Two points for a word the program's own `wants_like` uses, one for a word
    in its title. Deterministic, explainable in one line, and never a model
    call: the pick has to be the same every time or the diff below means
    nothing.
    """
    if not isinstance(example, dict):
        return 0
    asked = _tokens(want)
    if not asked:
        return 0
    like = _tokens(example.get("wants_like"))
    title = _tokens(example.get("title"))
    return WANTS_LIKE_WEIGHT * len(asked & like) + TITLE_WEIGHT * len(
        asked & (title - like)
    )


def nearest_program(brief: Any) -> dict[str, Any] | None:
    """The program on this brief nearest this want, or None.

    Highest token overlap wins. A TIE GOES TO THE SHORTER PROGRAM -- fewer
    steps is less to get wrong, and a short program that fits is a better
    start than a long one that also fits -- and a tie still standing is broken
    by the program key, so two identical briefs never pick differently.

    Returns the whole chosen program plus the score, the runner-up and one
    sentence saying why. A brief with no `plan_examples`, or a want nothing
    overlaps, answers None: an invented pick would be worse than no pick.
    """
    if not isinstance(brief, dict):
        return None
    examples = brief.get("plan_examples")
    if not isinstance(examples, list) or not examples:
        return None
    want = brief.get("want")
    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for example in examples:
        # An entry with no proposal is an INDEX ROW, and it is scored exactly
        # like a whole program: `wants_like` and `title` are what the pick has
        # ever read. The caller fetches the winner's proposal by key.
        if not isinstance(example, dict) or not example.get("key"):
            continue
        score = score_program(want, example)
        if score <= 0:
            continue
        ranked.append((-score, program_steps(example), str(example.get("key") or ""), example))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    example = best[3]
    shared = sorted(
        _tokens(want)
        & (_tokens(example.get("wants_like")) | _tokens(example.get("title")))
    )
    pick: dict[str, Any] = {
        "key": example.get("key"),
        "title": example.get("title"),
        "score": -best[0],
        "steps": best[1],
        "shared_words": shared,
        "proposal": example.get("proposal"),
        "why": program_why(example, -best[0], shared, runner_up),
    }
    if runner_up is not None:
        pick["runner_up"] = {"key": runner_up[3].get("key"), "score": -runner_up[0]}
    return pick


def program_why(
    example: dict[str, Any],
    score: int,
    shared: list[str],
    runner_up: tuple[int, int, str, dict[str, Any]] | None,
) -> str:
    """One sentence: which program, why it, and what to do with it."""
    words = ", ".join(shared[:6]) or "the shape of the want"
    tail = ""
    if runner_up is not None and -runner_up[0] == score:
        tail = (
            f" It tied with {runner_up[3].get('key')} and won on being the "
            "shorter program."
        )
    return (
        f"Program {example.get('key')} ({example.get('title')}) is the nearest "
        f"worked program to this want -- it is written for wants like this one "
        f"({words}).{tail} Copy its proposal WHOLE and change only what this "
        "want makes different: the words, the recipient, the numbers. Keep its "
        "steps, its acts and its account rows."
    )


# --------------------------------------------------------------------------
# THE SHELF: TWELVE INDEX ROWS AND ONE PROGRAM.
#
# WHAT FORCED IT (production fleet, 2026-09-09). 0.33.0 put the chosen program
# on the brief inline -- and left the other eleven there too, whole. Twelve
# worked proposals is about 19,000 tokens of a 131,072-token window spent
# before the model has read the want, and four agents died that day inside the
# provider with prompts over 129,000 tokens. A program the run will not copy
# is a program it does not need to read. So the brief carries an INDEX -- key,
# title, wants_like, how many steps, roughly how many tokens -- and exactly
# one program in full: the nearest one.
# --------------------------------------------------------------------------

INDEX_KEYS = ("key", "title", "wants_like", "steps", "approx_tokens", "url")

SHELF_SENTENCE = (
    "`plan_examples` is an INDEX of the worked programs on this bench, not the "
    "programs themselves: key, title, the wants each is for, its step count and "
    "roughly what it costs to read. The one program you need rides this brief "
    "in full as `nearest_program.proposal`. Copy that one; the index is there "
    "so you can see what else exists, not so you can read it all."
)


def approx_tokens(value: Any) -> int:
    """Roughly what this costs a model to read. Four characters a token."""
    try:
        return len(json.dumps(value, separators=(",", ":"), default=str)) // 4
    except (TypeError, ValueError):
        return len(str(value)) // 4


def index_row(example: Any) -> dict[str, Any]:
    """One program, as a line in the index."""
    if not isinstance(example, dict):
        return {}
    proposal = example.get("proposal")
    row = {
        "key": example.get("key"),
        "title": example.get("title"),
        "wants_like": example.get("wants_like"),
        "steps": program_steps(example),
        "approx_tokens": (
            approx_tokens(proposal)
            if isinstance(proposal, dict)
            else example.get("approx_tokens")
        ),
    }
    # The bench's index rows carry the door to the whole program. Kept when it
    # is there: a raw agent reading this index needs somewhere to go.
    if example.get("url"):
        row["url"] = example.get("url")
    return row


def program_index(examples: Any) -> list[dict[str, Any]]:
    """The shelf: one line per program, in the order the bench published them.

    Idempotent -- an index handed back in is the same index -- so a bench that
    already publishes the slim shape passes through untouched.
    """
    if not isinstance(examples, list):
        return []
    return [row for row in (index_row(example) for example in examples) if row.get("key")]


def program_sentence(pick: Any) -> str:
    """The plain line handed over on the brief beside the pick itself.

    A pick the BENCH made carries its own `why` and that is what is printed.
    A pick with none still gets a sentence: "no program is near this want" and
    "a program was picked and nobody said why" must be tellable apart.
    """
    if not isinstance(pick, dict):
        return (
            "No worked program on this brief is near this want. Build the plan "
            "from the form and validate it before filing."
        )
    why = pick.get("why")
    # The bench's own pick (contract 3.8) publishes `why` as an object --
    # {score, shared_words, sentence} -- and this package's pick publishes it
    # as the sentence itself. Read either; never print a dict at a model.
    if isinstance(why, dict):
        why = why.get("sentence") or why.get("why") or ""
    why = str(why or "").strip()
    if why:
        return why
    return (
        f"Program {pick.get('key')} ({pick.get('title')}) is the nearest worked "
        "program to this want. Copy its proposal WHOLE and change only what this "
        "want makes different: the words, the recipient, the numbers. Keep its "
        "steps, its acts and its account rows."
    )


# --------------------------------------------------------------------------
# DID IT COPY, OR DID IT COMPOSE?
#
# One log line per filing, comparing what was filed against the program it was
# handed. Leaf-by-leaf, on the PROGRAM's own fields: the program is the
# baseline, so a field the program never had is an addition and a field it had
# that the plan dropped is a removal. Words are expected to change -- that is
# the whole move -- so the line names the paths and lets the foreman read them.
# --------------------------------------------------------------------------

# Paths whose change is the POINT: every plan changes these and a diff that
# counted them would call every correct run "composed".
EXPECTED_TO_CHANGE = (
    "pitch_title",
    "pitch_body",
    "strategy",
    "skill_research",
    "smart_goals",
    "research_links",
    "wins",
    "capabilities",
    "total_ask_cents",
    "finish_line_cents",
    "allocation",
    "timeline_days",
)
# ...and the leaf names inside a step that are the agent's own words.
EXPECTED_LEAVES = (
    "title",
    "outcome_promise",
    "you",
    "purpose",
    "message",
    "subject",
    "body",
    "declared_odds",
    "declared_odds_reason",
    "line_item_amount",
    "brief",
)

DIFF_COPIED = "copied"
DIFF_COMPOSED = "composed"


def _leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    """Every leaf of a JSON value, keyed by its dotted path."""
    found: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            found.update(_leaves(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(_leaves(item, f"{prefix}.{index}" if prefix else str(index)))
    else:
        found[prefix or "$"] = value
    return found


def _is_expected(path: str) -> bool:
    head = path.split(".", 1)[0]
    if head in EXPECTED_TO_CHANGE:
        return True
    return path.rsplit(".", 1)[-1] in EXPECTED_LEAVES


def diff_from_program(proposal: Any, example: Any) -> dict[str, Any]:
    """What the model changed from the program it was handed.

    ``kept`` is the program's own shape that survived; ``changed`` is a leaf
    that exists in both and differs; ``removed`` is a leaf of the program the
    plan does not have; ``added`` is a leaf the plan grew. ``structural`` is
    the subset of those three that is NOT one of the fields every plan is
    supposed to rewrite -- a changed pitch is the job, a dropped act is a
    different program. The verdict reads off ``structural`` alone.
    """
    program = example.get("proposal") if isinstance(example, dict) else None
    if not isinstance(program, dict) or not isinstance(proposal, dict):
        return {
            "key": (example or {}).get("key") if isinstance(example, dict) else None,
            "verdict": DIFF_COMPOSED,
            "kept": 0,
            "changed": [],
            "added": [],
            "removed": [],
            "structural": [],
            "line": "no program to compare against; nothing copied",
        }
    theirs = _leaves(program)
    ours = _leaves(proposal)
    changed = sorted(path for path in theirs if path in ours and ours[path] != theirs[path])
    removed = sorted(path for path in theirs if path not in ours)
    added = sorted(path for path in ours if path not in theirs)
    kept = len(theirs) - len(changed) - len(removed)
    structural = sorted(
        path for path in changed + removed + added if not _is_expected(path)
    )
    verdict = (
        DIFF_COPIED
        if kept > 0 and len(structural) <= len(theirs) // 4
        else DIFF_COMPOSED
    )
    return {
        "key": example.get("key"),
        "verdict": verdict,
        "kept": kept,
        "of": len(theirs),
        "changed": changed,
        "added": added,
        "removed": removed,
        "structural": structural,
        "line": (
            f"program {example.get('key')}: {verdict}; kept {kept}/{len(theirs)} "
            f"fields, changed {len(changed)}, added {len(added)}, removed "
            f"{len(removed)}; off-shape {len(structural)}"
            + (f" [{', '.join(structural[:8])}]" if structural else "")
        ),
    }


def diff_json(diff: dict[str, Any]) -> str:
    """The diff as one compact JSON line, for a log a machine reads back."""
    return json.dumps(diff, separators=(",", ":"), sort_keys=True, default=str)
