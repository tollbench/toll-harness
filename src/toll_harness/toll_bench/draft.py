"""THE DRAFT LOOP — one plan, built up in pieces, at the bench's own door.

RULE 241 (Steven Ochs, 2026-09-09; bench contract 3.11):

    "send the outline for the full plan, then we send back the template for
     them to fill out, then they send it back and we send back each part that
     is refused until we get through the whole plan. If the plan is 3 steps or
     thirty that's how we get through it."

WHAT FORCED IT. Until this module the harness asked one model call for a WHOLE
proposal, handed the whole document to the validate door, and handed every
problem back at once. Overnight on 2026-09-08 every model but the strongest
answered that list by rewriting the whole document and breaking something new
on each pass; the next morning raw Sonnet 5 spent four whole-document passes on
an off-shelf want and never filed. The missing thing was never better
diagnostics. It was somewhere to put a PARTIAL answer, and the bench now has
one:

    PUT   /api/bench/targets/<id>/proposals/draft   the outline in
    PATCH /api/bench/targets/<id>/proposals/draft   one piece at a time
    GET   /api/bench/targets/<id>/proposals/draft   read it back
    POST  .../proposals {"from_draft": true}        file what the bench holds

So this is what the harness asks the model for, and it is all it asks for:

  1. an OUTLINE — steps in order, each an `ask` and a `title`, and for a step
     that touches the world the `tool` and the service it runs `on`. Not the
     whole brief, not a worked program, no promises, no money, no questions.
  2. one STEP's blanks at a time — the step's mechanics exactly as the bench
     expanded them, plus that step's blank paths with the bench's own sentence
     on each — answered as {path: value} patches.
  3. one NEXT_FIX at a time — one path, its current value, the code and the
     bench's one sentence of fix, with the step around it for context.

THE BOUNDS ARE THE BENCH'S AND THERE ARE NO KNOBS HERE. The bench caps the
rounds (three per opening problem, ceiling 200) and expires a draft at 24
hours; when it says `closed` this module opens ONE fresh outline and, if that
closes too, gives up on the want for this cycle. The only numbers below are a
stall guard (the same fix named over and over is not progress) and a prompt
budget that a loop prompt should never come near.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from toll_harness.core.types import ModelMessage
from toll_harness.toll_bench import blocks

_LOGGER = logging.getLogger("toll_harness.draft")

# THE BENCH'S BOUND IS THE ONLY BOUND (Steven, 2026-09-09: "we don't have to
# have a three strike rule ... 3 strikes on a 30 step job is too little", "I
# don't think we should do any levers"). The loop runs until the answer says
# `ready` or says `closed`. A draft is over when the bench says it is over --
# the rounds cap is its arithmetic, three per opening problem, so a thirty-step
# plan gets a thirty-step plan's worth of rounds and a three-step plan does
# not.
#
# ONE PUT PER RUN, AND A RUN RESUMES WHAT IS STANDING. A PUT is not a read: it
# REPLACES whatever draft the bench is holding and sets the rounds back to
# zero, so every answer already given is thrown away. The watch loop comes back
# to the same want every scan interval and a `file_informed_plan` obligation
# stands in the queue until the plan files -- so a loop that opens with a PUT
# opens a NEW draft every cycle and never finishes one. Live on 2026-09-09:
# dozens of PUTs on the same targets in two minutes, red the same number every
# time, almost no PATCH between them. A run now READS first (a GET costs no
# round) and PUTs only when there is nothing standing to carry on with.
# A loop prompt is SMALL BY DESIGN -- one step, or one problem. The 90k context
# budget in core/budget.py still guards a runtime run; this is the guard for a
# prompt this module builds itself, and it should never fire.
PROMPT_CHAR_BUDGET = 12_000
# The keys of an expanded step that are the platform's own machinery and the
# first thing to shed when a step's context runs long. The blanks themselves
# are never shed: they are the question.
_HEAVY_STEP_KEYS = ("service_setup", "statement", "har_blocks", "examples")

DRAFT_SYSTEM = (
    "You are writing ONE plan for a Toll Bench want, and the bench is holding "
    "it for you while you write. You are asked for one piece at a time and you "
    "answer with that piece and nothing else. Never rewrite the whole "
    "document: that is the failure this door exists to stop. Answer with JSON "
    "and no other words, no code fence, no explanation."
)

OUTLINE_INSTRUCTION = (
    "Write the OUTLINE of your plan for the want below. Steps in the order "
    "they happen, and nothing else.\n"
    'Every step carries an `ask` (APPROVE, CHOOSE, PROVIDE, GRANT or CONTACT) '
    "and a `title` in your own words.\n"
    "A step that touches the world outside this platform -- an email, a "
    "booking, a calendar event, a publish, a purchase -- also names the tool "
    "it runs and the service it runs on:\n"
    '  {"ask": "APPROVE", "title": "Offer them the times and book it", '
    '"tool": "gmail.message.send", "on": "google-gmail"}\n'
    "A step that touches nothing outside names its block instead, or just its "
    'ask:  {"ask": "APPROVE", "title": "Find three cafes", "block": '
    '"research"}\n'
    "Do NOT write promises, a pitch, questions, money, odds, arguments or "
    "connection rows here. The bench fills every mechanic it owns and then "
    "asks you for each of your own words, one at a time.\n"
    'Answer with JSON and nothing else: {"steps": [ ... ]}.'
)

BLANKS_INSTRUCTION = (
    "The bench expanded your outline and filled every mechanic it owns. Below "
    "is ONE step of that plan exactly as it now stands, and every blank on it "
    "that is YOURS to write, each with the bench's own sentence saying what "
    "belongs there.\n"
    "Fill them in your own words. Leave nothing you can answer empty. Change "
    "nothing else: paths not listed are the platform's.\n"
    'Answer with JSON and nothing else: {"patches": [{"path": "<the exact '
    'path>", "value": <your value>}, ...]}.'
)

REPEATED_FIX_INSTRUCTION = (
    "THE BENCH IS NAMING THE SAME THING AGAIN. Your last patch did not clear "
    "it. `you_sent_last_round` is exactly what you sent and "
    "`what_is_in_the_document_now` is what the bench has for that path after "
    "it. Sending the same value a third time cannot work: read the code and "
    "the fix sentence again and send something DIFFERENT -- a different "
    "value, or the same idea in the shape the code asks for. If the path is "
    "a list, send the whole list, not one entry of it.\n"
)

FIX_INSTRUCTION = (
    "The bench read the plan and named ONE thing to change. Change exactly "
    "that one thing. Do not touch any other path and do not resend the "
    "document.\n"
    'Answer with JSON and nothing else: {"patches": [{"path": "<the path '
    'named below>", "value": <the new value>}]}.'
)


# ---------------------------------------------------------------------------
# READING THE MODEL — JSON, however it wrapped it
# ---------------------------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def read_json_object(text: Any) -> dict[str, Any]:
    """The first JSON object in a model's answer, or {}.

    Models fence their JSON, prefix it with a sentence, or answer with the
    object alone. All three read the same here; anything else reads as {} and
    the caller treats it as a round that said nothing.
    """
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    fenced = _FENCE.search(raw)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def read_patches(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """[{path, value}] out of whatever shape the model answered in.

    Three shapes are accepted because a raw model writes whichever one it
    read: the `patches` list the door takes, a bare {path: value} map, and a
    single {"path": ..., "value": ...}.
    """
    if not isinstance(answer, dict):
        return []
    listed = answer.get("patches")
    out: list[dict[str, Any]] = []
    if isinstance(listed, list):
        for entry in listed:
            if isinstance(entry, dict) and str(entry.get("path") or "").strip():
                out.append({"path": str(entry["path"]).strip(), "value": entry.get("value")})
        return out
    if str(answer.get("path") or "").strip():
        return [{"path": str(answer["path"]).strip(), "value": answer.get("value")}]
    for key, value in answer.items():
        if key in ("kind", "note", "notes", "reasoning") or not isinstance(key, str):
            continue
        if "." in key or "[" in key:
            out.append({"path": key.strip(), "value": value})
    return out


def read_outline(answer: dict[str, Any]) -> dict[str, Any]:
    """The outline out of the model's answer. `steps` is the only key that
    rides; anything else the model volunteered is a word it was not asked for
    yet and the bench would only hand back as a blank."""
    steps = answer.get("steps") if isinstance(answer, dict) else None
    if not isinstance(steps, list):
        for key in ("outline", "plan", "proposal"):
            holder = answer.get(key) if isinstance(answer, dict) else None
            if isinstance(holder, dict) and isinstance(holder.get("steps"), list):
                steps = holder["steps"]
                break
    if not isinstance(steps, list):
        return {}
    return {"steps": [step for step in steps if isinstance(step, dict)]}


# ---------------------------------------------------------------------------
# THE PIECES — a step at a time, a fix at a time
# ---------------------------------------------------------------------------
_STEP_PATH = re.compile(r"^steps\.(\d+)(?:\.|$)")


def step_of(path: Any) -> int | None:
    match = _STEP_PATH.match(str(path or ""))
    return int(match.group(1)) if match else None


def group_blanks(blank_rows: Any) -> list[tuple[int | None, list[dict[str, Any]]]]:
    """The blanks grouped a STEP AT A TIME, in the order the document names
    them. `None` is the group of top-level fields (the pitch, the strategy,
    the questions, the money) -- one piece too, and asked for like one."""
    groups: list[tuple[int | None, list[dict[str, Any]]]] = []
    index: dict[int | None, list[dict[str, Any]]] = {}
    for row in blank_rows if isinstance(blank_rows, list) else []:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        key = step_of(row["path"])
        if key not in index:
            index[key] = []
            groups.append((key, index[key]))
        index[key].append(row)
    return groups


def step_context(document: Any, index: int | None) -> Any:
    """One step of the draft as it stands, for the model to write into. None
    for the top-level group, where the context is the want itself."""
    if index is None or not isinstance(document, dict):
        return None
    steps = document.get("steps")
    if not isinstance(steps, list) or index >= len(steps):
        return None
    return steps[index]


def _fit(value: Any, budget: int = PROMPT_CHAR_BUDGET) -> Any:
    """A step small enough to hand over. The platform's own machinery comes
    off first; the agent's words and the blanks never do."""
    if value is None:
        return None
    text = json.dumps(value, separators=(",", ":"), default=str)
    if len(text) <= budget or not isinstance(value, dict):
        return value
    trimmed = dict(value)
    for key in _HEAVY_STEP_KEYS:
        if key in trimmed:
            trimmed.pop(key)
            if len(json.dumps(trimmed, separators=(",", ":"), default=str)) <= budget:
                break
    return trimmed


# ---------------------------------------------------------------------------
# WHAT THE OUTLINE CALL IS TOLD — the want, the strategy, the grammar, the tools
# ---------------------------------------------------------------------------
BLOCK_GRAMMAR = (
    "A step is either work you do yourself or one BLOCK the platform runs. "
    "A block that touches the world names the tool and the service; the "
    "connection to that service is a row ON the same step and never a step of "
    "its own (rule 236). A step that only hands something back, asks a "
    "question, or asks for access needs no tool."
)


def block_grammar_summary(brief: Any, act_kinds: Any = None) -> str:
    """One short paragraph: the grammar, and the block kinds this bench has.

    The kinds are read off whatever the bench published -- the brief's block
    catalog, else the act registry -- and never invented here.
    """
    kinds: list[str] = []
    catalog = (brief or {}).get("block_templates") if isinstance(brief, dict) else None
    if isinstance(catalog, dict):
        kinds = [str(kind) for kind in catalog]
    if not kinds and isinstance(act_kinds, dict):
        registry = act_kinds.get("act_kinds") or act_kinds.get("kinds") or act_kinds
        if isinstance(registry, dict):
            kinds = [str(kind) for kind in registry]
        elif isinstance(registry, list):
            kinds = [
                str(entry.get("kind"))
                for entry in registry
                if isinstance(entry, dict) and entry.get("kind")
            ]
    kinds = sorted({kind for kind in kinds if kind})[:24]
    if not kinds:
        return BLOCK_GRAMMAR
    return BLOCK_GRAMMAR + " The blocks this bench has: " + ", ".join(kinds) + "."


# The outline needs three things about a tool: what it is called, what service
# it runs on, and what it does in one line. The bench's index also carries each
# call's argument list and shapes, and those are NOT an outline's business --
# the draft door writes the required arguments into the document and hands them
# back as blanks with their own sentences, one step at a time. So the index is
# narrowed here rather than pasted in.
TOOLS_CHAR_BUDGET = 4_000


def _tool_row(row: Any) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    tool = str(row.get("tool") or "").strip()
    if not tool:
        return None
    out = {"tool": tool, "on": str(row.get("provider") or row.get("on") or "")}
    line = str(row.get("one_line") or row.get("does") or "").strip()
    if line:
        out["does"] = line
    return out


def tools_index(brief: Any, budget: int = TOOLS_CHAR_BUDGET) -> list[dict[str, str]]:
    """The tools an outline may name.

    THE BENCH PUBLISHES THIS NOW (bench 965e61c5a): `tools` on the brief is
    every call a `calls` act can name -- {family, provider, tool, required,
    fields, one_line, shapes} -- built by the same `tool_arguments` the draft
    door fills a run's arguments with, so the index and the form cannot
    disagree. It is ALWAYS A LIST, including empty, and it replaces reading a
    worked program to find out what calls exist: since Steven's ruling on
    2026-09-09 the brief carries no `nearest_program` and no `plan_examples`
    at all (the programs stay public at GET /api/bench/plan-examples and are
    never pushed), and nothing in this loop reads them.

    The last row of the bench's index is the wildcard -- composio:<service>/
    <TOOL>, the door to ~1,500 other services -- so it is kept whatever the
    budget does to the rows above it: an index that silently ends at the
    budget would read as "these are all the tools there are".
    """
    published = (brief or {}).get("tools") if isinstance(brief, dict) else None
    if isinstance(published, list) and published:
        rows = [row for row in (_tool_row(entry) for entry in published) if row]
        if not rows:
            return []
        wildcard = rows[-1] if "<" in rows[-1]["tool"] else None
        body = rows[:-1] if wildcard else rows
        kept: list[dict[str, str]] = []
        spent = len(json.dumps(wildcard, default=str)) if wildcard else 0
        for row in body:
            spent += len(json.dumps(row, default=str))
            if spent > budget and kept:
                break
            kept.append(row)
        if wildcard:
            kept.append(wildcard)
        return kept
    # A bench that publishes no index: the platform's own four, and the verbs
    # this package holds a connection floor for. Nothing is invented, and no
    # program is read to find one.
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(tool: Any, service: Any = "") -> None:
        name = str(tool or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        found.append({"tool": name, "on": str(service or "")})

    for provider_key, actions in blocks.GRANT_MIN_ACTIONS.items():
        for action in actions:
            add(action, provider_key)
    for tool in blocks.PLATFORM_TOOLS:
        add(tool, "")
    return found


# What the person said, in the order it matters to an outline. The list is
# also the order things come OFF when the answer runs long: the last line the
# person wrote about this want beats a list of facts from every want they ever
# posted.
_STRATEGY_KEYS = (
    "want_in_own_words",
    "feedback_first",
    "person_context",
    "required_blocks",
    "person_already_connected",
    "contact_research_note",
    "timeline_days",
    "public_answers",
    "person_facts",
)
# The outline call is the one prompt that carries free text the harness did not
# write. A brief can hold a lot of it, and the loop's whole point is a small
# question, so this is where it stops.
STRATEGY_CHAR_BUDGET = 3_000


def person_strategy(brief: Any) -> Any:
    """What the person said about how they want this done, in their words.

    Not a summary and not this module's opinion: the fields the brief already
    publishes for it, in priority order, up to the budget, and null where the
    person said nothing.
    """
    if not isinstance(brief, dict):
        return None
    strategy: dict[str, Any] = {}
    for key in _STRATEGY_KEYS:
        value = brief.get(key)
        if not value:
            continue
        candidate = dict(strategy)
        candidate[key] = value
        if len(json.dumps(candidate, default=str)) > STRATEGY_CHAR_BUDGET and strategy:
            continue
        strategy = candidate
    return strategy or None


# ---------------------------------------------------------------------------
# THE LOOP
# ---------------------------------------------------------------------------
def rounds_left(answer: dict[str, Any]) -> int | None:
    """The rounds the BENCH says are left, or None when it does not say.

    This is the whole safety net. It is not a harness lever: it is the bench's
    own arithmetic read back off its own answer, so a bench that would answer
    for ever still stops the loop at the number it published.
    """
    rounds = answer.get("rounds") if isinstance(answer, dict) else None
    if not isinstance(rounds, dict):
        return None
    left = rounds.get("left")
    return int(left) if isinstance(left, (int, float)) else None


def _spent(answer: dict[str, Any]) -> bool:
    return rounds_left(answer) == 0


class DraftLoop:
    """Drives rule 241 against one want: outline in, pieces back, then file.

    The model is asked small questions and never sees the whole document; the
    bench is the only validator. Nothing here decides whether a plan is good.
    """

    def __init__(self, model: Any, provider: Any, *, logger: logging.Logger | None = None):
        self.model = model
        self.provider = provider
        self.log = logger or _LOGGER
        self.rounds = 0
        self.calls = 0
        self.trail: list[dict[str, Any]] = []
        # Every patch sent, in order, so a repeated fix can be handed back with
        # what the agent already tried.
        self._sent: list[tuple[str, Any]] = []

    # -- the model ---------------------------------------------------------
    def _ask(self, instruction: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
        prompt = instruction + "\n\n" + body
        if len(prompt) > PROMPT_CHAR_BUDGET:
            # A loop prompt should never come near this. Say so out loud
            # rather than quietly spending a window on it.
            self.log.warning(
                "draft loop prompt ran to %d characters (budget %d); the loop's "
                "prompts are small by design, so this is worth reading",
                len(prompt),
                PROMPT_CHAR_BUDGET,
            )
        self.calls += 1
        response = self.model.invoke(
            system=DRAFT_SYSTEM,
            messages=[ModelMessage.text("user", prompt)],
            tools=[],
        )
        return read_json_object(getattr(response, "text", ""))

    # -- the log -----------------------------------------------------------
    def _record(self, target_id: str, kind: str, answer: dict[str, Any], what: str) -> None:
        """ONE LINE PER ROUND. Round, what is left, and the one thing the
        bench named -- so a stuck loop is readable in the run log without
        turning the log into a copy of the plan."""
        fix = answer.get("next_fix") or {}
        rounds = answer.get("rounds") or {}
        line = {
            "target": target_id,
            "kind": kind,
            "round": self.rounds,
            "on": what,
            "remaining": answer.get("remaining"),
            "ready": bool(answer.get("ready")),
            "used": rounds.get("used"),
            "left": rounds.get("left"),
            "next_fix": fix.get("path"),
            "code": fix.get("code"),
            "closed": answer.get("closed"),
        }
        self.trail.append(line)
        self.log.info(
            "draft loop %s target=%s round=%d on=%s remaining=%s rounds=%s/%s "
            "next_fix=%s (%s)%s",
            kind,
            target_id,
            self.rounds,
            what,
            line["remaining"],
            line["used"],
            rounds.get("cap"),
            line["next_fix"],
            line["code"],
            " CLOSED" if line["closed"] else (" READY" if line["ready"] else ""),
        )

    # -- the three doors ---------------------------------------------------
    def _put(self, target_id: str, kind: str, outline: dict[str, Any]) -> dict[str, Any]:
        answer = self.provider.put_draft(target_id, outline, kind=kind)
        self.rounds = 0
        self._record(target_id, kind, answer, "outline")
        return answer

    PREVIEW = 120

    def _last_sent_for(self, path: str) -> dict[str, Any] | None:
        """The last patch that TOUCHED this path, whatever it was addressed to.

        A path is not always answered at its own address: the bench names
        `finalist_questions.0.0` and the agent patches `finalist_questions`,
        which is the right move (the whole list goes back, not one entry of
        it). An exact-match lookup finds nothing there and tells the agent it
        sent nothing, which is worse than saying nothing at all. So an
        ancestor counts, and so does a descendant.
        """
        if not path:
            return None
        for sent_path, value in reversed(self._sent):
            if (
                sent_path == path
                or path.startswith(sent_path + ".")
                or sent_path.startswith(path + ".")
            ):
                return {"path": sent_path, "value": value}
        return None

    def _preview(self, value: Any) -> str:
        try:
            text = value if isinstance(value, str) else json.dumps(value, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            text = str(value)
        text = " ".join(str(text).split())
        return text if len(text) <= self.PREVIEW else text[: self.PREVIEW - 1] + "\u2026"

    def _patch(
        self, target_id: str, kind: str, patches: Sequence[dict[str, Any]], what: str
    ) -> dict[str, Any]:
        # WHAT WENT OUT, EVERY ROUND. A stall is unreadable without it: on
        # 2026-09-09 a run spent rounds 123-132 on one REJ-15 and the log could
        # say only that the bench kept naming the same path, never what the
        # agent kept answering with.
        for entry in patches:
            path = str(entry.get("path") or "")
            self.log.info(
                "draft loop %s target=%s round=%d patch %s = %s",
                kind,
                target_id,
                self.rounds + 1,
                path or "?",
                self._preview(entry.get("value")),
            )
            self._sent.append((path, entry.get("value")))
        answer = self.provider.patch_draft(target_id, list(patches), kind=kind)
        self.rounds += 1
        self._record(target_id, kind, answer, what)
        return answer

    # What the first read of a cycle can find. Only ONE of these ends in a PUT.
    NOTHING_STANDING = "nothing_standing"   # 404 no_draft: open one
    STANDING = "standing"                   # carry it on, never PUT over it
    OVER = "over"                           # closed, or a door that will not
                                            # answer: leave the want this cycle

    def _read_first(self, target_id: str, kind: str) -> tuple[str, dict[str, Any]]:
        """THE FIRST STEP OF EVERY CYCLE. `GET .../proposals/draft` (and
        `?kind=plan` for a plan), which costs no round.

        A PUT over a standing draft is not a retry, it is a demolition: it
        replaces the document and sets the rounds back to zero. Live on
        2026-09-09, three units did exactly that on their own drafts -- one at
        10 rounds with 9 problems left and one at 18 rounds with 3, both a few
        answers from ready, both thrown away by the next cycle's outline. And
        since ebea60922 the bench COUNTS a repeated PUT as a round and keeps a
        used-up draft closed until it expires, so starting over now costs the
        want for a day. So: resume what stands, open only what is not there,
        and when the bench says the draft is over, leave the want alone until
        it expires.
        """
        try:
            answer = self.provider.read_draft(target_id, kind=kind)
        except Exception:  # noqa: BLE001 - a read that will not answer is not a licence to PUT
            self.log.warning(
                "draft loop %s target=%s: the draft could not be read; PUTting "
                "nothing this cycle",
                kind,
                target_id,
                exc_info=True,
            )
            return self.OVER, {}
        if not isinstance(answer, dict):
            return self.OVER, {}
        if answer.get("closed"):
            self.log.info(
                "draft loop %s target=%s: the standing draft is closed (%s); "
                "no PUT until it expires",
                kind,
                target_id,
                answer.get("closed"),
            )
            return self.OVER, answer
        error = str(answer.get("error") or "")
        if error == "no_draft" or int(answer.get("status") or 0) == 404:
            return self.NOTHING_STANDING, answer
        if not answer.get("ok"):
            return self.OVER, answer
        document = answer.get("draft")
        if not isinstance(document, dict) or not document:
            # An answer with no document is not a draft to carry on with, and
            # not a refusal either. Treat it as nothing standing.
            return self.NOTHING_STANDING, answer
        self.rounds = int((answer.get("rounds") or {}).get("used") or 0)
        self._record(target_id, kind, answer, "resume")
        return self.STANDING, answer

    def _open(
        self, target_id: str, kind: str, want: Any, strategy: Any, brief: Any, act_kinds: Any
    ) -> dict[str, Any] | None:
        """THE ONE PUT. None when the model was asked for an outline and gave
        none."""
        if kind == "plan":
            # THE PLAN STARTS FROM THE STEPS ALREADY FILED (rule 113). An
            # outline here would replace the bid the person picked, so the
            # draft is opened empty and the bench hands back the owned plan
            # with the selection answers beside it.
            return self._put(target_id, kind, {})
        outline = read_outline(
            self._ask(
                OUTLINE_INSTRUCTION,
                {
                    "want": want,
                    "what_the_person_said": strategy,
                    "block_grammar": block_grammar_summary(brief, act_kinds),
                    "tools": tools_index(brief),
                },
            )
        )
        if not outline.get("steps"):
            return None
        return self._put(target_id, kind, outline)

    # -- the pieces --------------------------------------------------------
    def _fill_the_blanks(
        self, target_id: str, kind: str, answer: dict[str, Any], want: Any
    ) -> dict[str, Any]:
        """One step at a time: the step as the bench expanded it, its blanks,
        and the patches that answer them."""
        for index, rows in group_blanks(answer.get("blanks")):
            if answer.get("closed") or answer.get("ready") or _spent(answer):
                break
            payload = {
                "want": want,
                "step_number": None if index is None else index + 1,
                "step": _fit(step_context(answer.get("draft"), index)),
                "blanks": [
                    {
                        "path": row.get("path"),
                        "what_belongs_there": row.get("note"),
                        "example": row.get("example"),
                        "required": bool(row.get("required")),
                    }
                    for row in rows
                ],
            }
            if index is None:
                payload["these_are"] = (
                    "the fields of the bid itself, not of any one step"
                )
            patches = read_patches(self._ask(BLANKS_INSTRUCTION, payload))
            if not patches:
                self.log.warning(
                    "draft loop %s target=%s: the model answered a step's blanks "
                    "with nothing patchable; moving to the next piece",
                    kind,
                    target_id,
                )
                continue
            answer = self._patch(
                target_id,
                kind,
                patches,
                "blanks" if index is None else f"step {index + 1}",
            )
        return answer

    def _answer_the_fixes(
        self, target_id: str, kind: str, answer: dict[str, Any], want: Any
    ) -> dict[str, Any]:
        """ONE problem at a time, for as long as the bench names one.

        There is no strike count here. A thirty-step plan has thirty steps'
        worth of things to fix and the bench already knows how many rounds that
        is worth; the loop stops when the bench says `ready`, says `closed`, or
        publishes no rounds left.

        WHAT A REPEAT GETS IS A BETTER PROMPT, NOT A LIMIT. When the bench
        names the same path with the same code twice running, the next ask
        carries what was sent last round and what the bench has for that path
        now. Live on 2026-09-09, Greg spent rounds 123-132 on one REJ-15 on
        `finalist_questions.0.0`, asked the same question in the same words
        every time, and answered it the same way every time.
        """
        last_named: tuple[str, str] | None = None
        while (
            not answer.get("ready")
            and not answer.get("closed")
            and not _spent(answer)
            and isinstance(answer.get("next_fix"), dict)
        ):
            fix = answer["next_fix"]
            path = str(fix.get("path") or "")
            code = str(fix.get("code") or "")
            index = step_of(path)
            payload: dict[str, Any] = {
                "want": want,
                "fix_this": {
                    "path": fix.get("path"),
                    "current_value": fix.get("current"),
                    "code": fix.get("code"),
                    "fix": fix.get("fix"),
                    "detail": fix.get("detail"),
                },
                "step_number": None if index is None else index + 1,
                "step": _fit(step_context(answer.get("draft"), index)),
            }
            # SAME PATH, SAME CODE AS LAST ROUND: say so, and hand back what
            # was sent beside what the bench has now. No strike rule and no
            # round limit -- a model that is told its last answer did not land,
            # and shown it, can send something else; a model that is asked the
            # same question in the same words answers it the same way.
            instruction = FIX_INSTRUCTION
            if (path, code) == last_named and path:
                instruction = REPEATED_FIX_INSTRUCTION + FIX_INSTRUCTION
                payload["your_last_patch_did_not_clear_this"] = {
                    "path": path,
                    "you_sent_last_round": self._last_sent_for(path),
                    "what_is_in_the_document_now": fix.get("current"),
                }
                self.log.info(
                    "draft loop %s target=%s round=%d: %s (%s) again; telling "
                    "the agent what it sent last round",
                    kind,
                    target_id,
                    self.rounds + 1,
                    path,
                    code or "?",
                )
            last_named = (path, code)
            patches = read_patches(self._ask(instruction, payload))
            if not patches:
                self.log.warning(
                    "draft loop %s target=%s: no patch came back for %s; "
                    "stopping this draft",
                    kind,
                    target_id,
                    path,
                )
                break
            answer = self._patch(target_id, kind, patches, f"fix {path or '?'}")
        return answer

    # -- the whole thing ---------------------------------------------------
    def run(
        self,
        target_id: str,
        *,
        kind: str = "bid",
        brief: Any = None,
        act_kinds: Any = None,
        proposal_id: str | None = None,
        idempotency_key: str = "",
        file: bool = True,
    ) -> dict[str, Any]:
        """Outline -> blanks -> fixes -> file. One want, one answer.

        `file` false runs the whole loop and stops at the door: the document
        the bench holds comes back on `draft` and nothing is filed.
        """
        want = (brief or {}).get("want") if isinstance(brief, dict) else None
        strategy = person_strategy(brief)
        # READ FIRST, EVERY CYCLE. This is the loop's first step and the only
        # thing that decides whether an outline goes out at all.
        state, answer = self._read_first(target_id, kind)
        if state == self.OVER:
            return self._gave_up(
                target_id, kind,
                "draft_closed" if answer.get("closed") else
                str(answer.get("error") or "draft_unreadable"),
                str(
                    answer.get("closed")
                    or answer.get("message")
                    or "The draft on this want could not be read, so nothing was sent."
                ),
                answer,
            )
        if state == self.NOTHING_STANDING:
            opened = self._open(target_id, kind, want, strategy, brief, act_kinds)
            if opened is None:
                return self._gave_up(
                    target_id, kind, "no_outline",
                    "The model was asked for an outline and answered with no steps.",
                )
            answer = opened
        if not answer.get("ok") and not answer.get("closed"):
            return self._gave_up(
                target_id, kind,
                str(answer.get("error") or "draft_door_refused"),
                str(answer.get("message") or answer.get("error") or ""),
                answer,
            )
        if not answer.get("closed"):
            answer = self._fill_the_blanks(target_id, kind, answer, want)
        if not answer.get("closed"):
            answer = self._answer_the_fixes(target_id, kind, answer, want)
        if answer.get("closed"):
            # A closed draft is never re-PUT, in this run or the next: the
            # bench counts a repeated PUT as a round and holds a used-up draft
            # closed until it expires, so starting over costs the want a day.
            self.log.warning(
                "draft loop %s target=%s closed: %s",
                kind,
                target_id,
                answer.get("closed"),
            )
        if not answer.get("ready"):
            return self._gave_up(
                target_id, kind,
                "draft_closed" if answer.get("closed") else "draft_not_ready",
                str(
                    answer.get("closed")
                    or "The draft still has problems and the loop stopped before filing."
                ),
                answer,
            )
        if not file:
            return {
                "ok": True,
                "filed": False,
                "dry_run": True,
                "kind": kind,
                "draft": answer.get("draft"),
                "rounds": self.rounds,
                "model_calls": self.calls,
                "trail": self.trail,
            }
        if kind == "plan":
            filed = self.provider.file_plan_from_draft(
                target_id, str(proposal_id or ""), idempotency_key
            )
        else:
            filed = self.provider.file_from_draft(target_id, idempotency_key)
        self.log.info(
            "draft loop %s target=%s filed=%s after %d round(s) and %d model call(s)",
            kind,
            target_id,
            bool(filed.get("ok")),
            self.rounds,
            self.calls,
        )
        return {
            "ok": bool(filed.get("ok")),
            "filed": bool(filed.get("ok")),
            "kind": kind,
            "response": filed,
            "proposal_id": filed.get("proposal_id"),
            "rounds": self.rounds,
            "model_calls": self.calls,
            "trail": self.trail,
        }

    def _gave_up(
        self,
        target_id: str,
        kind: str,
        error: str,
        message: str,
        answer: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.log.warning(
            "draft loop %s target=%s gave up after %d round(s): %s (%s)",
            kind,
            target_id,
            self.rounds,
            error,
            message,
        )
        out = {
            "ok": False,
            "filed": False,
            "kind": kind,
            "error": error,
            "message": message,
            "rounds": self.rounds,
            "model_calls": self.calls,
            "trail": self.trail,
        }
        if answer:
            out["remaining"] = answer.get("remaining")
            out["next_fix"] = answer.get("next_fix")
            if answer.get("status") is not None:
                out["status"] = answer.get("status")
        return out
