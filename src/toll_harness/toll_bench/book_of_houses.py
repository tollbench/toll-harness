from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from toll_harness.core.budget import measure
from toll_harness.email.book_of_houses import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.fleet import FleetStore
from toll_harness.toll_bench import blocks, draft, programs
from toll_harness.tools import sniff as sniffer

_LOGGER = logging.getLogger("toll_harness.toll_bench")

# The refusal codes the block door speaks (contract 2.44, rules 228 and 229).
REJ_REQUIRED_BLOCK = "REJ-32"
REJ_BLOCK_DECLARATION = "REJ-33"
REJ_HOLLOW_BLOCK = "REJ-34"
# RULE 236: a block whose account nothing on its step opens. Like REJ-32 it
# CARRIES THE FORM, so it is repaired and filed once.
REJ_BLOCK_GRANT = blocks.REJ_BLOCK_GRANT
# RULE 236 (2026-09-08): a connection filed as a STEP OF ITS OWN. This one
# carries NO form -- the shape it hands back is the connect_account row, in the
# refusal's own words -- so it is repaired from the BRIEF's template instead,
# and it is deliberately not in REJ_CARRIES_THE_FORM.
REJ_GRANT_STEP_REMOVED = blocks.REJ_GRANT_STEP_REMOVED
# RULE 242 (2026-09-10): an act whose agent half reads an account has its row
# on the step BEFORE; the same row on the act's step is refused. The refusal
# carries its own `fix` and field like every structural refusal, so it takes
# the generic path: nothing is repaired at home and nothing is re-filed.
REJ_ROW_NEEDED_BEFORE = blocks.REJ_ROW_NEEDED_BEFORE
REJ_FRAME = blocks.REJ_FRAME
# RULE 238 (2026-09-08): an act on the person's own account that names nobody
# to send to, or a raw address typed into the plan. Like REJ-38 it carries no
# `plan_template`: what it hands back is the QUESTION, and the brief published
# that question on `bid_template.finalist_questions` -- so it is repaired from
# there and is deliberately not in REJ_CARRIES_THE_FORM.
REJ_CONTACT_ROUTE = blocks.REJ_CONTACT_ROUTE
# RULE: SAY WHERE EVERY ARGUMENT CAME FROM (2026-09-09). The bid door's
# fourth question refuses an argument with no declared source and a tool
# whose account row is missing from its own step.
REJ_ARGUMENT_PROVENANCE = blocks.REJ_ARGUMENT_PROVENANCE
REJ_CARRIES_THE_FORM = (REJ_REQUIRED_BLOCK, REJ_BLOCK_GRANT)

# CONTRACT 3.0 (2026-09-05): the free validate door, call 3 of six. It runs the
# WHOLE bid door -- the shared validator plus the bid-only checks (REJ-14,
# REJ-15, REJ-31) -- and answers with every problem at once, each carrying a
# plain-words `fix`. It writes nothing: no row, no refusal, no idempotency key,
# and open_bid_count does not move. A bench that reports a contract below this
# has no such route and the local mirror is the whole pre-check there.
VALIDATE_DOOR_MIN_CONTRACT_MAJOR = 3
# One repair pass. The door is free, so the model gets its list of problems
# back once and files on the next call; a harness that kept bouncing the same
# plan would spend the run and teach itself nothing.
MAX_DOOR_REPAIR_PASSES = 1


def _retry_tag(rej: str | None) -> str:
    """The idempotency suffix for the one re-file a carried form earns."""
    return str(rej or "rej").replace("-", "").lower()


# An act in one of these states is standing: it is the person's move or it has
# already run. Filing another copy of the same kind is a duplicate.
LIVE_ACT_STATES: frozenset[str] = frozenset(
    {"pending", "held", "approved", "executed", "sent"}
)

# Kinds the platform files and closes on a block step when the catalog cannot
# be read. Today the registry publishes a `declaration` for exactly one.
BLOCK_KINDS_FALLBACK: frozenset[str] = frozenset({"meeting"})

# THE OUTSIDE ACT (Steven, 2026-09-05) -- WHAT THE PLATFORM HAS NO HANDS FOR.
# The bounds of the evidence door, checked here so a refusal the harness can
# see costs no call. The summary is the whole receipt the person reads.
EVIDENCE_SUMMARY_MIN = 10
EVIDENCE_SUMMARY_MAX = 2000
EVIDENCE_MAX_LINKS = 5
EVIDENCE_MAX_RECEIPTS = 5

# The refusals the evidence door speaks. Surfaced to the model VERBATIM: the
# door knows whether the person has tapped Allow, and its sentence is the one
# that says what to do next.
EVIDENCE_DOOR_REFUSALS: frozenset[str] = frozenset(
    {
        "no_outside_act",
        "not_allowed_yet",
        "already_done",
        "invalid_evidence",
    }
)

# RULE 230 (Steven, 2026-09-05) -- DELIVERING A FILE.
# 50 MB per file, 100 MB per want, both the platform's numbers. The per-file
# cap is checked here as well so a 300 MB render is refused in one sentence
# the model can act on instead of being pushed up the wire and refused there.
ARTIFACT_MAX_BYTES = 50 * 1024 * 1024

# The refusals the file doors speak. Surfaced to the model VERBATIM: the
# platform is the scanner, and its sentence is the one that tells the model
# what to do next.
# EVERY STRUCTURED REFUSAL THE OUTCOME DOOR CAN SAY (0.38.0).
#
# WHAT FORCED THE ADDITIONS: `stand_in` was not on this list, so a 422 the
# agent could have FIXED was raised as an exception instead. Down the old
# road, `ToolRegistry.execute` flattens any exception to
# `{"error": "<str(error)>"}`, so the model saw one sentence of English and
# lost `field`, `reason` and `fix` -- the three keys that say what to change.
# One fleet unit re-earned that refusal every forty seconds on production on
# 2026-09-11 without ever being told which field was wrong.
#
# The rule for this list: a 4xx the agent can act on comes back as a RESULT,
# with the bench's own keys on it. Anything else still raises.
FILE_DOOR_REFUSALS: frozenset[str] = frozenset(
    {
        "deliverable_missing",
        "deliverable_type_mismatch",
        "deliverable_unfetchable",
        "deliverable_too_large",
        "deliverable_empty",
        "claim_url_rejected",
        "out_of_turn_filing",
        "title_too_long",
        "artifact_budget_exceeded",
        # LAW B (2026-09-09): a stand-in is not a value. The bench names the
        # field, quotes the value, says why, and points at `the_person_said`.
        "stand_in",
        # The step is not free to close yet: something the agent owes first.
        "reply_owed",
        "acts_not_filed",
        "outcome_promises_send",
        "options_are_the_delivery",
        # The filing's own shape, note and words.
        "note_required",
        "note_too_long",
        "document_required",
        "document_invalid",
        "block_over_cap",
        "prose_over_cap",
        "outcome_text_too_long",
        "link_in_outcome_text",
        "credential_request_rejected",
        "secret_rejected",
        "off_platform_payment",
        # The deal itself, not the filing: nothing to re-file until it changes.
        "deal_not_active",
        # RULE 233 (2026-09-05): the shape door. The bench counts the empty
        # boxes on the cards and its sentence names the card and the field.
        *blocks.SHAPE_DOOR_REFUSALS,
    }
)

# Refusals no re-filing can clear: the deal, not the delivery.
FILE_DOOR_TERMINAL: frozenset[str] = frozenset({"deal_not_active"})

# The bench's own keys on a refusal body, lifted to the TOP of the result.
# They used to sit only under `detail`, where a model reading a tool result
# does not look, and `fix` is the whole point of the sentence.
FILE_DOOR_BODY_KEYS: tuple[str, ...] = (
    "field", "reason", "fix", "how", "rej", "detail", "kinds", "owed",
    "deliverable", "next",
)

# THE MIRROR WARNS ONCE, THEN THE DOOR DECIDES. `current-step` publishes the
# step's `deliverable` but NOT its file receipts, so a harness restarted
# between the delivery and the outcome cannot see a file that is genuinely on
# the step. Refusing that filing forever would be the validate-door mistake in
# a new place: a mirror must never bury a filing production would take. So the
# local check speaks once per step -- which is what the model needs, because
# the usual case is that it never delivered anything -- and any second attempt
# goes to the bench, whose `deliverable_missing` is the authoritative refusal.
MAX_DELIVERABLE_WARNINGS = 1

# --------------------------------------------------------------------------
# WHAT A TOOL HANDS BACK IS SPENT OUT OF THE MODEL'S WINDOW.
#
# WHAT FORCED THESE LIMITS (production fleet, 2026-09-09). Four agents died
# inside the provider on prompts over 129,000 tokens against a 131,072-token
# context. The brief was carrying twelve worked programs (~19k tokens); the
# validate door was echoing the whole submitted plan back on every answer and
# the model called it fourteen times in three minutes; proposals/mine is over
# 100KB of the agent's own filed plans. None of that was ever read twice, and
# all of it stayed in the conversation forever. Every number below is a
# ceiling on what a tool RETURNS, never on what the harness knows: the full
# payload goes to the run log, which is where the foreman reads it.
# --------------------------------------------------------------------------

# The brief, after the programs are shelved. Roughly 15,000 tokens -- a real
# brief off the live bench runs to about 48,000 characters and passes whole;
# this is the backstop for the outlier, not a diet for every want.
BRIEF_CHAR_BUDGET = 60_000
# THE DOOR LOOP. Three answers is a compile, four is a loop: on 2026-09-09 a
# run called the validate door fourteen times in three minutes, never filed,
# and each answer re-entered the conversation whole.
MAX_VALIDATE_ATTEMPTS = 3
# The newest N of an unbounded list. A step thread, a step's acts and the
# materials released to a deal all grow for the life of the deal.
STEP_THREAD_MESSAGE_LIMIT = 20
STEP_ACT_LIMIT = 12
RELEASED_MATERIAL_LIMIT = 20
# A door answer names every problem at once; past this many the plan is not
# nearly right and the rest is noise. The count is always the true count.
PROBLEM_LIMIT = 25
PROBLEM_TEXT_MAX = 600

# RULES 168 AND 170, APPLIED TO THE FOUR QUESTIONS (contract 2.37, 2026-09-04).
# The finalist questions were the last person-facing ask outside HAR: four plain
# strings the selection modal drew as four blank text boxes. Each question is now
# either a HAR block -- the SAME {id, format, title, description?, required?,
# config?} shape a step's har_blocks carries -- or a legacy plain string, which
# counts as a text box. At most TWO of the four may be text, so four plain
# strings can no longer be filed: the string shape is for reading old rows, not
# for filing. The bench refuses the rest as REJ-15; it is checked here so the one
# filing a target allows is never spent on it. What forced it: a hot-pot bid
# asked "Should 'Portland area' mean Portland city limits or the wider metro
# area?" -- a two-way choice -- as a blank box, bundled four separate facts into
# one question, asked a yes/no as prose, and asked for dates in a text box.
FINALIST_QUESTIONS_REQUIRED = blocks.FINALIST_QUESTIONS_CAP

# CONTRACT 2.42 / rule 226 -- the five bid-homework blocks. All required on a
# NEW bid (REJ-31 at the server door), and all FROZEN afterwards: the informed
# plan revises steps and never these, which is why they are carried across
# verbatim when a plan revision is rebuilt from the sealed original below.
# `wins` may legitimately be an empty list -- an agent with no resolved walks
# yet cites none and is not penalised -- so it is checked for PRESENCE, not
# for content.
HOMEWORK_FIELDS = (
    "strategy",
    "capabilities",
    "wins",
    "research_links",
    "skill_research",
)
FINALIST_QUESTION_TEXT_MAX = 2
FINALIST_TITLE_MAX = 300
FINALIST_DESCRIPTION_MAX = 400
# The canonical HAR format slugs (the step contract's enum).
HAR_FORMAT_SLUGS = frozenset(
    {
        "short_answer",
        "written_response",
        "single_choice",
        "multiple_choice",
        "rank",
        "structured_form",
        "date_time",
        "location",
        "file_upload",
        "media_upload",
        "download_return",
        "external_link",
        "code_reference",
        "confirm_correct",
        "review_approve",
        "agreement",
        "signature",
        "connect_account",
        "grant_access",
        "invite_share",
        "payment_authorize",
        "schedule",
        "communication",
        "yes_no",
        "number",
        # RULE 237/238 (2026-09-08/09). QUESTION-ONLY, and never a step format:
        # the person picks people out of their own private Contacts and each
        # pick arrives as a reference for `acts[].contact_ref`. It is on the
        # brief's own `bid_template.finalist_questions` and on
        # `question_templates.contact_picker`, so a mirror that did not know
        # the slug answered "`contact_picker` is not a HAR format slug" about
        # the very question the bench handed out -- and with the validate door
        # unreachable that is `local_validation_failed` and a legal plan buried
        # at home.
        "contact_picker",
    }
)
# A question is asked, never approved, granted or paid: those belong on a step of
# the plan, where the person has already chosen this agent.
FINALIST_REFUSED_FORMATS = frozenset(
    {
        "review_approve",
        "confirm_correct",
        "agreement",
        "signature",
        "grant_access",
        "connect_account",
        "payment_authorize",
    }
)
FINALIST_TEXT_FORMATS = frozenset({"short_answer", "written_response"})
# RULE 237 (Steven, 2026-09-07): ONE picker, and it may ask for as many people
# as the want needs -- two for an introduction, eighty for a wedding list.
# `count` is the ONLY thing it may carry; the people themselves are always the
# person's to choose, so a picker that names one is not a picker. The ceiling
# is the person's own Contacts book (private_contacts.MAX_SLOTS), a sanity
# bound on one JSON field rather than a product limit. A picker is not a text
# box, so it never counts against the two-text cap.
CONTACT_PICKER_FORMAT = blocks.CONTACT_PICKER_FORMAT
CONTACT_PICKER_COUNT_MAX = 500
CONTACT_PICKER_KEYS = frozenset(
    {"id", "format", "title", "description", "required", "config", "ask"}
)
# Rule 170: a choice control must offer real options, not an empty dropdown that
# forces a type-in. Same minimums the step blocks carry.
FINALIST_CHOICE_MIN_OPTIONS = {"single_choice": 2, "multiple_choice": 3, "rank": 3}
# The renderer adds "Other (type in)" itself; an agent must not ship the sentinel.
HAR_OTHER_SENTINEL = "__other__"
# Rule B: a text box whose wording is really a two-way question.
FINALIST_BINARY_LEADS = frozenset(
    {"do", "does", "is", "are", "should", "can", "could", "would", "will"}
)


def _reads_as_a_choice(text: Any) -> str | None:
    """Return the format a text question should have used, or None.

    Rule B of contract 2.37. "A or B?", "either X or Y", "which of ..." is a
    single_choice; a Do/Does/Is/Are/Should/Can/Could/Would/Will question is a
    yes_no. The choice test runs first, because "Should it mean X or Y?" is a
    choice before it is a yes/no.
    """
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return None
    ends_in_question = lowered.endswith("?")
    if "which of" in lowered:
        return "single_choice"
    if "either " in lowered and " or " in lowered:
        return "single_choice"
    if ends_in_question and " or " in lowered:
        return "single_choice"
    lead = lowered.split(" ", 1)[0].strip("\"'([")
    if ends_in_question and lead in FINALIST_BINARY_LEADS:
        return "yes_no"
    return None


def _count_real_options(options: Any) -> int:
    """Count options that are not the renderer's own "Other (type in)"."""
    if not isinstance(options, list):
        return 0
    total = 0
    for option in options:
        if isinstance(option, dict):
            marker = option.get("id") or option.get("value")
        else:
            marker = option
        if isinstance(marker, str) and marker.strip() == HAR_OTHER_SENTINEL:
            continue
        total += 1
    return total


def _has_other_sentinel(options: Any) -> bool:
    if not isinstance(options, list):
        return False
    for option in options:
        marker = option.get("id") or option.get("value") if isinstance(option, dict) else option
        if isinstance(marker, str) and marker.strip() == HAR_OTHER_SENTINEL:
            return True
    return False


def _contact_picker_problems(block: dict[str, Any], pos: str) -> list[dict[str, str]]:
    """RULE 237's shape for the one picker, mirrored off the bid door.

    Three things, and nothing else: `count` is all the config may hold, it is
    a whole number from 1 to the person's own book, and the block carries no
    contact of its own. The words are the agent's; the people are the
    person's.
    """
    problems: list[dict[str, str]] = []
    config = block.get("config")
    if config not in (None, {}):
        if not isinstance(config, dict) or set(config) - {"count"}:
            problems.append(
                {
                    "path": pos,
                    "message": (
                        "`contact_picker` takes only config.count; the person "
                        "chooses who (rule 237, REJ-15)"
                    ),
                }
            )
        else:
            wanted = config.get("count")
            if (
                isinstance(wanted, bool)
                or not isinstance(wanted, int)
                or not 1 <= wanted <= CONTACT_PICKER_COUNT_MAX
            ):
                problems.append(
                    {
                        "path": pos,
                        "message": (
                            "`contact_picker` config.count must be a whole number "
                            f"from 1 to {CONTACT_PICKER_COUNT_MAX} -- how many "
                            "people this plan reaches, one picker however many "
                            "that is (rule 237, REJ-15)"
                        ),
                    }
                )
    if any(key not in CONTACT_PICKER_KEYS for key in block):
        problems.append(
            {
                "path": pos,
                "message": (
                    "`contact_picker` cannot carry a contact or an address of "
                    "its own; the person picks them from their private book and "
                    "each pick arrives as a reference for acts[].contact_ref "
                    "(rule 238, REJ-15)"
                ),
            }
        )
    if block.get("ask") not in (None, "PROVIDE"):
        problems.append(
            {"path": pos, "message": "`contact_picker` is a PROVIDE question (REJ-15)"}
        )
    return problems


def _finalist_block_problems(block: dict[str, Any], pos: str) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for key in ("id", "title"):
        value = block.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(
                {
                    "path": pos,
                    "message": (
                        f"a question block needs a non-empty `{key}`; the three required "
                        "fields are id, format and title (REJ-15)"
                    ),
                }
            )
    title = block.get("title")
    if isinstance(title, str) and len(title.strip()) > FINALIST_TITLE_MAX:
        problems.append(
            {"path": pos, "message": f"title exceeds {FINALIST_TITLE_MAX} chars (REJ-15)"}
        )
    description = block.get("description")
    if isinstance(description, str) and len(description.strip()) > FINALIST_DESCRIPTION_MAX:
        problems.append(
            {
                "path": pos,
                "message": f"description exceeds {FINALIST_DESCRIPTION_MAX} chars (REJ-15)",
            }
        )
    fmt = block.get("format")
    if not isinstance(fmt, str) or not fmt.strip():
        problems.append(
            {
                "path": pos,
                "message": (
                    "a question block needs a non-empty `format`, one of the HAR format "
                    "slugs (REJ-15)"
                ),
            }
        )
        return problems
    fmt = fmt.strip()
    if fmt in FINALIST_REFUSED_FORMATS:
        problems.append(
            {
                "path": pos,
                "message": (
                    f"format `{fmt}` is not a question: approve, grant and payment formats "
                    "belong on a step of the plan, never on a question asked before the "
                    "person has chosen you (REJ-15)"
                ),
            }
        )
        return problems
    if fmt not in HAR_FORMAT_SLUGS:
        problems.append(
            {"path": pos, "message": f"`{fmt}` is not a HAR format slug (REJ-15)"}
        )
        return problems
    if fmt == CONTACT_PICKER_FORMAT:
        problems.extend(_contact_picker_problems(block, pos))
        return problems
    config = block.get("config") if isinstance(block.get("config"), dict) else {}
    if fmt in FINALIST_CHOICE_MIN_OPTIONS:
        need = FINALIST_CHOICE_MIN_OPTIONS[fmt]
        found = _count_real_options(config.get("options"))
        if found < need:
            problems.append(
                {
                    "path": pos,
                    "message": (
                        f"a `{fmt}` question needs at least {need} real options in "
                        f"config.options (found {found}); an empty dropdown is a text box "
                        "wearing a control (rule 170, REJ-15)"
                    ),
                }
            )
        if _has_other_sentinel(config.get("options")):
            problems.append(
                {
                    "path": pos,
                    "message": (
                        "the renderer adds \"Other (type in)\" itself; a `__other__` option "
                        "of your own is refused (REJ-15)"
                    ),
                }
            )
    if fmt == "number":
        unit = config.get("unit")
        if not isinstance(unit, str) or not unit.strip():
            problems.append(
                {
                    "path": pos,
                    "message": "a `number` question needs a non-empty config.unit (REJ-15)",
                }
            )
    return problems


def finalist_question_problems(questions: Any) -> list[dict[str, str]]:
    """Local mirror of the bench's REJ-15 gate on finalist_questions."""
    path = "finalist_questions"
    if (
        not isinstance(questions, list)
        or len(questions) != 1
        or not isinstance(questions[0], list)
        or len(questions[0]) != FINALIST_QUESTIONS_REQUIRED
    ):
        return [
            {"path": path, "message": "must contain exactly one array of four questions"}
        ]
    problems: list[dict[str, str]] = []
    # RULE 237: ONE picker, however many people it asks for. Two of them is
    # two books opened for one plan, and the bid door refuses it in one line.
    pickers = sum(
        1
        for question in questions[0]
        if isinstance(question, dict)
        and str(question.get("format") or "").strip() == CONTACT_PICKER_FORMAT
    )
    if pickers > 1:
        problems.append(
            {
                "path": path,
                "message": (
                    "use one contact_picker question for the people this plan "
                    "contacts; `config.count` is how many of them there are "
                    "(rule 237, REJ-15)"
                ),
            }
        )
    text_questions = 0
    for index, question in enumerate(questions[0]):
        pos = f"{path}[1][{index + 1}]"
        wording: Any = None
        if isinstance(question, str):
            text = question.strip()
            if not text:
                problems.append({"path": pos, "message": "must be a non-empty string"})
                continue
            if len(text) > FINALIST_TITLE_MAX:
                problems.append(
                    {"path": pos, "message": f"exceeds {FINALIST_TITLE_MAX} chars"}
                )
                continue
            text_questions += 1
            wording = text
        elif isinstance(question, dict):
            problems.extend(_finalist_block_problems(question, pos))
            fmt = question.get("format")
            if isinstance(fmt, str) and fmt.strip() in FINALIST_TEXT_FORMATS:
                text_questions += 1
                wording = question.get("title")
        else:
            problems.append(
                {
                    "path": pos,
                    "message": (
                        "must be a HAR block object with id, format and title, or a plain "
                        "string (REJ-15)"
                    ),
                }
            )
            continue
        suggested = _reads_as_a_choice(wording)
        if suggested:
            problems.append(
                {
                    "path": pos,
                    "message": (
                        f"reads as a choice but is a text box: file it as a `{suggested}` "
                        "block with the answers spelled out, so the person taps instead of "
                        "typing (rule 170, REJ-15)"
                    ),
                }
            )
    if text_questions > FINALIST_QUESTION_TEXT_MAX:
        problems.append(
            {
                "path": path,
                "message": (
                    f"{text_questions} of the four questions are text boxes; at most "
                    f"{FINALIST_QUESTION_TEXT_MAX} may be (short_answer, written_response, "
                    "or a legacy plain string, which counts as one). The person taps: file "
                    "the rest as HAR blocks -- single_choice, multiple_choice, rank, "
                    "yes_no, number, date_time, schedule, or one structured_form when "
                    "several related facts belong together (rule 168, REJ-15)"
                ),
            }
        )
    return problems


class BookOfHousesTollBenchProvider:
    """Maps the public, agent-scoped Book of Houses API to Toll Harness tools."""

    def __init__(
        self,
        api: BookOfHousesApiClient,
        *,
        fleet: FleetStore | None = None,
        fleet_agent_id: str | None = None,
        fleet_proposal_limit: int = 4,
        open_bid_limit: int | None = None,
    ):
        self.api = api
        self.fleet = fleet
        self.fleet_agent_id = fleet_agent_id
        self.fleet_proposal_limit = fleet_proposal_limit
        # Optional market-scan crowding limit; None disables the check.
        self.open_bid_limit = open_bid_limit
        # Once reachable, stay confirmed for a window instead of re-fetching
        # /me every watch cycle (2026-08-27: the 7-agent fleet alone was
        # ~2,400 /me calls an hour). A fresh reachability ping waits at most
        # this long for its ack.
        self._reachable_cached: dict[str, Any] | None = None
        self._reachable_until = 0.0
        # Contract 2.44 state. The act registry is fetched once per process;
        # the block memo is what the last current_step said about which steps
        # the platform is running itself (rule 229), so the harness never
        # files an act or an outcome on a block it does not own; and the
        # refusal counter is what keeps ONE correction from becoming a loop
        # (a harness filed and withdrew about a hundred times in 90 minutes on
        # 2026-09-04).
        self._act_kinds: dict[str, Any] | None = None
        self._platform_blocks: dict[str, dict[str, Any]] = {}
        self._last_step_id: str | None = None
        self._block_refusals: dict[str, int] = {}
        # Contract 3.0: probed once per provider, then remembered. None means
        # "not asked yet"; False means this bench publishes no validate door.
        self._validate_door: bool | None = None
        self._door_repairs: dict[str, int] = {}
        # RULE 230. What each step's SIGNED plan promised to hand back, and
        # every file receipt known to be attached to it -- the server's, plus
        # anything this process filed through deliver_file. A step whose
        # channel is `file` cannot close on words alone, and finding that out
        # at the door costs the person a round.
        self._step_deliverables: dict[str, dict[str, Any]] = {}
        self._step_receipts: dict[str, list[dict[str, Any]]] = {}
        self._deliverable_warnings: dict[str, int] = {}
        # RULE 233. The shape mirror warns once per step on a MISSING or SHORT
        # set of cards (an earlier receipt on the step may already carry
        # them), and every time on a blank box in the document in hand.
        self._shape_warnings: dict[str, int] = {}
        # 0.34.0. The shelf: programs fetched by key and remembered, the
        # validate door's attempt count per want, and the last problems it
        # named there -- so the fourth call answers honestly instead of
        # spending another round trip on the same refusal.
        self._plan_example_cache: dict[str, dict[str, Any]] = {}
        self._validate_attempts: dict[str, int] = {}
        self._last_door_problems: dict[str, dict[str, Any]] = {}

    def protocol(self) -> dict[str, Any]:
        return self.api.protocol()

    def guide(self, topic: str) -> dict[str, Any]:
        ranges = {
            "start": ("## Autonomous start", "## Your to-do list"),
            "attention": ("## Your to-do list", "## Work pulse"),
            "bidding": ("### GET /targets/open", "## What people like"),
            "finalist": (
                "### GET /targets/<target_id>/proposals/<proposal_id>/answers",
                "### A complete bid you can copy",
            ),
            "delivery": ("## The process after acceptance", "## How you are scored"),
        }
        if topic not in ranges:
            raise ValueError(f"Unknown guide topic: {topic}")
        document = self.api.skill()
        start_heading, end_heading = ranges[topic]
        start = document.find(start_heading)
        end = document.find(end_heading, start + len(start_heading))
        if start < 0 or end < 0:
            raise RuntimeError(f"Current production guide is missing the {topic} section")
        protocol = self.protocol()
        return {
            "topic": topic,
            "contract_version": protocol.get("contract_version"),
            "rules_version_hash": protocol.get("rules_version_hash"),
            "instructions": document[start:end].strip(),
        }

    def proposal_schema(self) -> dict[str, Any]:
        return self.api.proposal_schema()

    def capability_taxonomy(self) -> dict[str, Any]:
        return self.api.capability_taxonomy()

    def status(self) -> dict[str, Any]:
        return self.api.me()

    REACHABLE_CACHE_SECONDS = 120.0

    def ensure_reachable(self) -> dict[str, Any]:
        if self._reachable_cached is not None and time.monotonic() < self._reachable_until:
            return self._reachable_cached
        status = self.status()
        acknowledgements = 0
        for _ in range(2):
            reachability = status.get("reachability_test") or {}
            if reachability.get("reachable") or reachability.get("reachable_at"):
                break
            status = self.api.ack_reachability_ping()
            acknowledgements += 1
        reachability = status.get("reachability_test") or {}
        result = {
            "ok": bool(reachability.get("reachable") or reachability.get("reachable_at")),
            "acknowledgements": acknowledgements,
            "reachability_test": reachability,
        }
        if result["ok"]:
            # The cached answer describes a confirmation that did no work:
            # zero acknowledgements, marked cached.
            self._reachable_cached = {**result, "acknowledgements": 0, "cached": True}
            self._reachable_until = time.monotonic() + self.REACHABLE_CACHE_SECONDS
        return result

    def attention(self, *, wait: int = 0) -> dict[str, Any]:
        return self.api.attention(wait=wait)

    def events(self, *, after: str | None = None, wait: int = 0) -> dict[str, Any]:
        return self.api.events(after=after, wait=wait)

    def list_targets(self) -> dict[str, Any]:
        return self.api.open_targets()

    def read_brief(self, target_id: str) -> dict[str, Any]:
        """The want, and the whole legal FORM to bid with.

        RULE 231 (2026-09-05): the brief carries `person_connected`, the
        provider keys the person has already connected on earlier wants,
        ALWAYS PRESENT and empty for nearly everyone. It is a fact the plan is
        built on -- Drive connected, plan a Drive hand-back; nothing
        connected, plan the download path -- so it is also handed over as one
        plain sentence the model cannot skim past.
        """
        response = self.api.target_brief(target_id)
        brief = response.get("brief")
        if isinstance(brief, dict):
            brief["person_already_connected"] = blocks.connected_sentence(
                brief.get("person_connected")
            )
            # PROGRAM FIRST (Steven, 2026-09-09), ONE PROGRAM (2026-09-09,
            # the evening). Twelve worked programs on a brief are twelve
            # things to read and nothing to do -- and about 19,000 tokens of
            # a 131,072-token window, which is how four agents died inside
            # the provider the same day. The pick is made here or taken from
            # the bench, it rides the brief in full, and the other eleven are
            # an INDEX. Always present, None when nothing overlaps this want.
            self._shelve_the_programs(brief)
            # ...and when the person answered the contact question with "find
            # them for me", the recipient is not on the form and never will be.
            brief["contact_research_note"] = (
                blocks.CONTACT_RESEARCH_SENTENCE
                if blocks.contact_research_of(brief)
                else ""
            )
            self._fit_the_brief(target_id, brief)
        return response

    def _plan_example(self, key: Any) -> dict[str, Any] | None:
        """One worked program, by key, or None.

        The route is contract 3.8 (`GET /api/bench/plan-examples/<key>`). An
        older bench, an older client or a bad key all answer None and the
        caller keeps whatever the brief already gave it: reading a program is
        never worth a failed run. Fetched once per process and remembered.
        """
        key = str(key or "")
        if not key:
            return None
        if key in self._plan_example_cache:
            return self._plan_example_cache[key]
        fetch = getattr(self.api, "plan_example", None)
        program: dict[str, Any] | None = None
        if callable(fetch):
            try:
                response = fetch(key)
            except Exception as error:  # noqa: BLE001 - a missing shelf is not a failure
                _LOGGER.warning("Plan example %s could not be read (%s)", key, error)
                response = None
            if isinstance(response, dict):
                candidate: Any = response
                for holder in ("plan_example", "program", "example"):
                    if isinstance(response.get(holder), dict):
                        candidate = response[holder]
                        break
                if isinstance(candidate, dict) and isinstance(candidate.get("proposal"), dict):
                    program = candidate
        if program is not None:
            self._plan_example_cache[key] = program
        return program

    def _shelve_the_programs(self, brief: dict[str, Any]) -> None:
        """One program in full, the rest as an index. Both always present.

        The bench's own `nearest_program` wins when it publishes one -- it
        reads the want with more than token overlap -- and this package picks
        only when it does not. Either way exactly ONE program is inlined; a
        pick whose proposal has to be fetched is fetched by key.
        """
        examples = brief.get("plan_examples")
        pick = brief.get("nearest_program")
        if not isinstance(pick, dict) or not pick.get("key"):
            pick = programs.nearest_program(brief)
        if isinstance(pick, dict) and not isinstance(pick.get("proposal"), dict):
            fetched = self._plan_example(pick.get("key"))
            if fetched is not None:
                pick = {
                    **pick,
                    "title": pick.get("title") or fetched.get("title"),
                    "wants_like": pick.get("wants_like") or fetched.get("wants_like"),
                    "proposal": fetched.get("proposal"),
                }
            else:
                _LOGGER.warning(
                    "Program %s is the nearest to this want and its proposal "
                    "could not be read; the brief carries the pick without it",
                    pick.get("key"),
                )
        brief["nearest_program"] = pick if isinstance(pick, dict) else None
        brief["program_to_copy"] = programs.program_sentence(brief["nearest_program"])
        index = programs.program_index(examples)
        if not index:
            return
        before = programs.approx_tokens(examples)
        brief["plan_examples"] = index
        brief["program_shelf"] = programs.SHELF_SENTENCE
        after = programs.approx_tokens(index) + programs.approx_tokens(
            brief["nearest_program"]
        )
        _LOGGER.info(
            "Brief shelf: %d programs indexed, %s inline; ~%d tokens instead of ~%d",
            len(index),
            (brief["nearest_program"] or {}).get("key") or "none",
            after,
            before,
        )

    def _fit_the_brief(self, target_id: str, brief: dict[str, Any]) -> None:
        """Shed the brief's duplicates until it fits, loudest thing last.

        Nothing here is a judgement about what a plan needs: it is the order
        in which a brief's own copies of itself come off. `bid_template` is
        `plan_template` inside the whole bid payload and `bid_template_notes`
        already names every blank in it; `block_templates` is the catalog, and
        the one block this want needs is in the program that rides the brief
        in full. Both are still whole on the brief the harness reads at filing
        time, so a plan is still repaired from the real form.
        """
        if measure(brief) <= BRIEF_CHAR_BUDGET:
            return
        shed: list[str] = []
        if isinstance(brief.get("bid_template"), (dict, list)):
            brief["bid_template"] = None
            brief["bid_template_note"] = (
                "The whole-bid template was left off this brief to keep it "
                "readable. `bid_template_notes` names every blank it carries, "
                "and `nearest_program.proposal` is a filled bid of the same "
                "shape -- copy that."
            )
            shed.append("bid_template")
        catalog = brief.get("block_templates")
        if measure(brief) > BRIEF_CHAR_BUDGET and isinstance(catalog, dict) and catalog:
            brief["block_templates"] = {
                str(kind): (len(steps) if isinstance(steps, list) else 1)
                for kind, steps in catalog.items()
            }
            brief["block_templates_note"] = (
                "The block catalog was too big to hand over whole, so this is "
                "the kinds it holds and how many steps each block is. The "
                "block this want needs is already written out inside "
                "`nearest_program.proposal`: pull it from there, in full and "
                "in its order (rule 236). The harness still reads the real "
                "catalog when it files, so a block you get wrong is repaired "
                "rather than refused."
            )
            shed.append("block_templates")
        if shed:
            _LOGGER.warning(
                "Brief for target %s ran to %d characters; shed %s to fit the "
                "window (budget %d)",
                target_id,
                measure(brief),
                ", ".join(shed),
                BRIEF_CHAR_BUDGET,
            )

    def list_act_kinds(self) -> dict[str, Any]:
        """The act registry (contract 2.44). Each kind publishes `wanted_when`
        (which wants need it), `declaration` (the bid-time fields, no context
        needed) and `template` (the step, ready to file). Read it before
        declaring a block: the fields are the kind's, not the harness's."""
        if self._act_kinds is None:
            self._act_kinds = self.api.act_kinds()
        return self._act_kinds

    @staticmethod
    def _published_steps(brief: dict[str, Any]) -> list[dict[str, Any]]:
        """Every step this brief published: the skeleton AND the catalog.

        Contract 3.0 hands out a blank `plan_template` and puts the blocks in
        `block_templates`, so a rule-236 repair that read only the skeleton
        would find no `connect_account` row on any live brief and silently do
        nothing at all.
        """
        return blocks.published_template_steps(
            brief.get("plan_template"), brief.get("block_templates")
        )

    def _grant_requirements(self) -> dict[str, tuple[str, ...]]:
        """{kind: providers} off the act registry's own `requires_grants`.

        RULE 236: nothing about which kind runs on which connection is written
        in the harness any more -- the last hardcoded copy of that fact refused
        the correct one-step plan at home for a day. The registry answers it,
        the catalog read is memoized on the provider, and a read that fails
        leaves the local mirror SILENT rather than refusing a legal plan: the
        bench's own validate door is free and is the judge.
        """
        try:
            catalog = self.list_act_kinds().get("kinds") or {}
        except Exception:  # noqa: BLE001 - a catalog read never blocks a filing
            return {}
        if not isinstance(catalog, dict):
            return {}
        needs: dict[str, tuple[str, ...]] = {}
        for name, spec in catalog.items():
            if not isinstance(spec, dict):
                continue
            declared = spec.get("requires_grants")
            if declared is None and isinstance(spec.get("declaration"), dict):
                declared = spec["declaration"].get("requires_grants")
            providers = tuple(
                provider.strip().lower()
                for provider in (declared or [])
                if isinstance(provider, str) and provider.strip()
            )
            if providers:
                needs[str(name).strip().lower()] = providers
        return needs

    def _block_kinds(self) -> frozenset[str]:
        """The kinds the PLATFORM writes, files, runs and closes (rule 229).

        A kind that publishes a `declaration` or a `template` is a block: its
        step is written at signing and its act is filed by the platform when
        the step opens. Read once, best effort; the fallback is the one kind
        that publishes them today.
        """
        try:
            catalog = self.list_act_kinds().get("kinds") or {}
        except Exception:  # noqa: BLE001 - a catalog read must never block work
            return BLOCK_KINDS_FALLBACK
        found = {
            str(name).strip().lower()
            for name, entry in catalog.items()
            if isinstance(entry, dict) and (entry.get("declaration") or entry.get("template"))
        }
        return frozenset(found) or BLOCK_KINDS_FALLBACK

    def _brief_for(self, target_id: str) -> dict[str, Any]:
        """The live brief, or an empty dict. Never raises except on a 404.

        The blocks a want requires ride the brief, so the bid path reads it
        once and uses it for the fleet round, the required blocks and the
        template. A brief we cannot read means no local block repair, never a
        refused filing.
        """
        try:
            response = self.api.target_brief(target_id)
        except BookOfHousesApiError:
            raise
        except Exception as error:  # noqa: BLE001 - a brief read never blocks a bid
            _LOGGER.warning("Brief read failed for target %s: %s", target_id, error)
            return {}
        return response.get("brief") or {}

    def _door_is_published(self) -> bool:
        """Whether this bench publishes the free validate door. Probed once.

        The protocol call already rides every run and names the contract, so
        the probe costs nothing extra and needs no guesswork: contract 3.0 is
        where the door appears. A bench that answers 404 to the route anyway
        (an odd deployment, a proxy) flips this to False on the first call and
        the local mirror carries the rest of the run.
        """
        if self._validate_door is not None:
            return self._validate_door
        available = False
        try:
            version = str((self.protocol() or {}).get("contract_version") or "")
            available = int(version.split(".")[0]) >= VALIDATE_DOOR_MIN_CONTRACT_MAJOR
        except Exception:  # noqa: BLE001 - an unreadable protocol is "no door"
            available = False
        self._validate_door = available
        return available

    def validate_at_the_door(
        self, target_id: str, proposal: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The bench's own answer on this plan, or None when there is no door.

        Never raises for the absence of the route: a 404 whose body is not the
        bench's own "target not found" envelope is a missing path, and an
        older server simply has none. A 404 that IS the bench's envelope means
        the target closed, and that belongs to the caller.
        """
        if not self._door_is_published():
            return None
        try:
            return self.api.validate_proposal(target_id, proposal)
        except BookOfHousesApiError as error:
            if error.status in (404, 405) and error.code == "http_error":
                _LOGGER.info(
                    "This bench publishes no proposals/validate route; using "
                    "the local mirror for the rest of this run"
                )
                self._validate_door = False
                return None
            if error.status in (401, 403):
                # The token cannot open the door; the filing door may still
                # take the bid, so this is a downgrade and not a refusal.
                self._validate_door = False
                return None
            raise
        except Exception as error:  # noqa: BLE001 - a free check never blocks a filing
            _LOGGER.warning("Validate door call failed (%s); falling back", error)
            return None

    @staticmethod
    def _door_problem_payload(
        door: dict[str, Any], local: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """What the model reads back: every problem at once, with its fix."""
        problems = [p for p in (door.get("problems") or []) if isinstance(p, dict)]
        if local is not None and not local.get("ok"):
            # The offline mirror sometimes knows one more thing (a schema the
            # server has not published yet). It never blocks a filing, but a
            # repair pass may as well carry it.
            problems = problems + [
                {
                    "code": "LOCAL",
                    "field": problem.get("path"),
                    "detail": problem.get("message"),
                    "fix": problem.get("message"),
                    "step_index": None,
                }
                for problem in (local.get("problems") or [])
                if isinstance(problem, dict)
            ]
        return {
            "problems": problems,
            "problem_count": len(problems),
            "corrections": door.get("corrections") or [],
        }

    def _block_refusal(
        self, target_id: str, error: BookOfHousesApiError
    ) -> dict[str, Any]:
        """One correction, then the round is over.

        REJ-33 and REJ-34 are format refusals the model can fix: the kind's
        own sentence says what is wrong. It gets exactly one correction. The
        second identical door closing is terminal for this round and logged,
        because a harness that keeps re-filing spends the person's board and
        teaches itself nothing.
        """
        count = self._block_refusals.get(target_id, 0) + 1
        self._block_refusals[target_id] = count
        terminal = count > 1
        if terminal:
            _LOGGER.warning(
                "Block refusal %s on target %s for the %d time: terminal for this round",
                error.rej,
                target_id,
                count,
            )
        payload: dict[str, Any] = {
            "ok": False,
            "error": "block_declaration_refused",
            "rej": error.rej,
            "detail": error.message,
            "attempt": count,
            "terminal": terminal,
            "message": (
                "The bench refused the declared block in the kind's own words. "
                "Fix the fields it names and file once more."
                if not terminal
                else (
                    "The bench refused the declared block again. This round is "
                    "over for this want; do not file it a third time."
                )
            ),
        }
        if error.plan_template:
            payload["plan_template"] = error.plan_template
        return payload

    @staticmethod
    def _grant_gap_never_blocks_the_filing(
        validation: dict[str, Any], plan_template: Any
    ) -> dict[str, Any]:
        """A grant gap with no template to fix it is the door's to refuse.

        The local mirror of REJ-35 exists so a round is never spent on a plan
        the door will refuse. When there is nothing to insert -- an older
        server, or a brief that has closed behind a selection -- refusing at
        home would only bury the plan the person is waiting on. File it, and
        let the refusal carry the form.

        RULE 236: "something to insert" now means a published `connect_account`
        ROW as well as a GRANT step, because the row is what the repair copies.
        """
        if validation.get("ok"):
            return validation
        template = plan_template if isinstance(plan_template, list) else []
        if any(
            isinstance(step, dict)
            and (blocks.grant_provider(step) or blocks.connect_row_providers(step))
            for step in template
        ):
            return validation
        remaining = [
            problem
            for problem in validation.get("problems") or []
            if problem.get("rej") != REJ_BLOCK_GRANT
        ]
        if remaining:
            return {**validation, "problems": remaining}
        return {**validation, "ok": True, "problems": []}

    def _retire_grant_step_after(
        self, target_id: str, error: Any, plan: dict[str, Any], plan_template: Any
    ) -> tuple[dict[str, Any], list[str]]:
        """RULE 236 / REJ-38: the door refused a connection filed as a step.

        The refusal is the whole teaching -- it names the step, the provider
        and the exact `connect_account` row to put on the step that uses it --
        so it is LOGGED VERBATIM here and handed back verbatim by
        ``_grant_step_removed_refusal``. The repair reads the row off the
        BRIEF's template, because this refusal carries no `plan_template` of
        its own, and the caller re-files ONCE. No move, no re-file: a harness
        that kept bouncing the same plan would spend the whole run.
        """
        _LOGGER.warning(
            "Target %s refused the plan %s -- a connection was filed as a step "
            "of its own: %s",
            target_id,
            error.rej,
            error.message,
        )
        fixed, moved = blocks.retire_grant_steps(plan, plan_template)
        if moved:
            _LOGGER.warning(
                "Target %s: rule 236 moved the connection into the action (%s); "
                "re-filing once",
                target_id,
                "; ".join(moved),
            )
        else:
            _LOGGER.warning(
                "Target %s: nothing in this plan to move into the action, so "
                "the door's own refusal is the answer; not re-filed",
                target_id,
            )
        return fixed, moved

    @staticmethod
    def _grant_step_removed_refusal(error: Any) -> dict[str, Any]:
        """The door's REJ-38, handed to the model in the door's own words."""
        return {
            "ok": False,
            "error": "grant_step_removed",
            "rej": error.rej,
            "detail": error.message,
            "terminal": False,
            "fix": blocks.CONNECTION_IN_THE_ACTION_SENTENCE,
            "message": (
                "Nothing was filed. The bench refused this plan REJ-38: a "
                "connection is not a step of its own, it is a "
                "`connect_account` row on the step of the action that uses it "
                "(the one exception is rule 242's connect step right before an "
                "action whose agent half reads that account). "
                "`detail` names the step and carries the exact row to add. "
                "Pull the block out of the brief's block_templates WHOLE "
                "instead of composing its steps, and file once more."
            ),
        }

    def _ask_who_after(
        self, target_id: str, error: Any, proposal: dict[str, Any], brief: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """RULE 238 / REJ-40: the door refused a plan that asked nobody who.

        The repair is one question and the brief published it. Logged verbatim
        like REJ-38, filed ONCE more, and when the brief carries no picker --
        an older bench, or a want nothing on it can reach a person for -- the
        door's own sentence is the answer and nothing is re-filed.
        """
        _LOGGER.warning(
            "Target %s refused the bid %s -- the plan reaches a person and "
            "names nobody to send to: %s",
            target_id,
            error.rej,
            error.message,
        )
        research = blocks.contact_research_of(brief)
        if research is not None:
            # The person already said they have nobody to pick. The answer is
            # not a question, it is the binding: the send reads its recipient
            # off a research run.
            fixed, bound = blocks.bind_contact_research(proposal, research)
            if bound:
                _LOGGER.warning(
                    "Target %s: bound the outreach to the research the person "
                    "asked for (%s); re-filing once",
                    target_id,
                    "; ".join(bound),
                )
                return fixed, "; ".join(bound)
            _LOGGER.warning(
                "Target %s: this person asked us to find the recipient and "
                "nothing in this plan can be bound to it; the door's own "
                "refusal is the answer",
                target_id,
            )
            return proposal, None
        fixed, asked = blocks.merge_contact_picker(
            proposal,
            proposal.get("steps"),
            brief.get("bid_template"),
            brief.get("bid_template_notes"),
            # The door has just said this plan reaches a person. It reads the
            # lane table this package does not have, so it is not asked again.
            reaches_a_person=True,
        )
        if asked:
            _LOGGER.warning(
                "Target %s: put the brief's own contact_picker on the bid (%s); "
                "re-filing once",
                target_id,
                asked,
            )
        else:
            _LOGGER.warning(
                "Target %s: this brief publishes no contact_picker to add, so "
                "the door's own refusal is the answer; not re-filed",
                target_id,
            )
        return fixed, asked

    def _bind_the_research_after(
        self, target_id: str, error: Any, proposal: dict[str, Any], brief: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """REJ-41: the door refused an argument with no source.

        One repair exists and only one: where this person asked us to FIND the
        recipient, the send is bound to a research run. Everything else the
        door refuses under this code -- a run quoting a run that has not
        happened, a tool whose account row is missing, an address typed into
        the plan -- is the model's own to fix in its own words, and the door
        already said which. Nothing is re-filed then.
        """
        _LOGGER.warning(
            "Target %s refused the bid %s -- an argument named no source it "
            "could come from: %s",
            target_id,
            error.rej,
            error.message,
        )
        research = blocks.contact_research_of(brief)
        if research is None:
            return proposal, []
        fixed, bound = blocks.bind_contact_research(proposal, research)
        if bound:
            _LOGGER.warning(
                "Target %s: bound the outreach to the research run (%s); "
                "re-filing once",
                target_id,
                "; ".join(bound),
            )
        return fixed, bound

    @staticmethod
    def _argument_provenance_refusal(error: Any) -> dict[str, Any]:
        """The door's REJ-41, handed to the model in the door's own words."""
        return {
            "ok": False,
            "error": "argument_provenance",
            "rej": error.rej,
            "detail": error.message,
            "terminal": False,
            "fix": blocks.ARGUMENT_PROVENANCE_SENTENCE,
            "message": (
                "Nothing was filed. The bench refused this plan REJ-41: a run "
                "took an argument from somewhere it cannot come from, or its "
                "tool has no `connect_account` row on the step that runs it. "
                "`detail` names the run and the argument. Every value is a "
                "literal you wrote or one of three sources -- an answer to one "
                "of your four questions, a run declared BEFORE this one, or a "
                "draft this plan wrote -- and a recipient is never an address "
                "typed into the plan."
            ),
        }

    def _log_program_diff(
        self, target_id: str, proposal: dict[str, Any], brief: Any
    ) -> dict[str, Any] | None:
        """One line saying whether this filing copied a program or composed one."""
        pick = brief.get("nearest_program") if isinstance(brief, dict) else None
        if pick is None:
            pick = programs.nearest_program(brief)
        if not pick:
            return None
        diff = programs.diff_from_program(proposal, pick)
        _LOGGER.info(
            "Target %s filed against %s -- %s | %s",
            target_id,
            pick.get("key"),
            diff["line"],
            programs.diff_json(diff),
        )
        return diff

    @staticmethod
    def _contact_route_refusal(error: Any) -> dict[str, Any]:
        """The door's REJ-40, handed to the model in the door's own words."""
        return {
            "ok": False,
            "error": "contact_route",
            "rej": error.rej,
            "detail": error.message,
            "terminal": False,
            "fix": blocks.CONTACT_PICKER_SENTENCE,
            "message": (
                "Nothing was filed. The bench refused this plan REJ-40: an act "
                "that runs on the person's own account names nobody to send it "
                "to, or a plan field holds a raw address. `detail` carries the "
                "door's own words. THE FOUR QUESTIONS ARE FROZEN AT BID TIME, so "
                "on a plan revision the picker cannot be added now: what this "
                "plan must carry is the `contact_ref` the person's own pick "
                "filled in, or a `found_contact` {name, email, source_url} the "
                "person approves beside the exact message."
            ),
        }

    def _platform_owned(self, step_id: str) -> dict[str, Any] | None:
        """What the platform is running on this step, or None.

        RULE 229: a block is a step the platform writes, files, runs and
        closes. When its act is standing or already executed, the harness has
        no move on it: filing another act is a duplicate (409) and filing the
        outcome takes words that are the platform's to write. The memo is fed
        by current_step, the call the deal dispatch already makes.
        """
        entry = self._platform_blocks.get(str(step_id or ""))
        return entry if entry and entry.get("kinds") else None

    def platform_owned_block(self, step_id: str) -> dict[str, Any] | None:
        """The rule-229 memo, for the dispatch: {"kinds": {kind: state}} when
        the platform is running a block on this step, else None. Read off the
        last current_step call; never a call of its own."""
        return self._platform_owned(step_id)

    # THE AGENT'S OWN FILED PLANS, WHICH IT WROTE AND DOES NOT NEED BACK.
    # /proposals/mine carries every bid this agent ever filed, each with its
    # whole plan: the route's own comment says the body can exceed 100KB, and
    # it is re-read into the conversation on every cycle. The bid the agent
    # must ACT on keeps its plan; the rest come back as the row -- ids, money,
    # status, the deal block, the person's answers and the move.
    PROPOSAL_PLAN_KEYS = (
        "steps",
        "steps_original",
        "pitch_body",
        "strategy",
        "skill_research",
        "research_links",
        "smart_goals",
        "finalist_questions",
        "wins",
        "capabilities",
        "campaign",
        "allocation",
    )
    # Emitted twice by the bench under both vocabularies (contract 2.23). One
    # copy is enough; the newer word is the one kept.
    PROPOSAL_DUPLICATE_KEYS = ("finalist_answers", "finalist_health")

    # THE MODEL'S VIEW OF ITS OWN BIDS IS SMALL (0.35.5). On 2026-09-09 one
    # `list_proposals` call handed a GLM run 213,096 characters (~53k tokens):
    # 79 bids, sixteen of them accepted deals long ended, each kept WHOLE
    # because it carried a deal id. The run burned 268k input tokens and was
    # cut off by its budget before it filed anything. A settled bid -- expired,
    # rejected, withdrawn, or a deal that ended -- is one line now, only the
    # newest few of those are listed, and the whole answer is capped: past the
    # cap the oldest live plans drop out first. `_owned_proposals` (the
    # harness's own reader) is untouched and still sees every bid whole.
    SETTLED_STATUSES = frozenset({"expired", "rejected", "withdrawn", "superseded"})
    ENDED_DEAL_STATUSES = frozenset({"ended", "resolved", "lapsed"})
    SETTLED_ROWS_KEPT = 12
    LIST_PROPOSALS_CHARS = 48_000

    def list_proposals(self) -> dict[str, Any]:
        rows = self.api.proposals()
        return self._proposals_view(rows)

    def _proposals_view(self, rows: Any) -> dict[str, Any]:
        live: list[dict[str, Any]] = []
        settled: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            if self._settled(row):
                settled.append(self._settled_line(row))
            else:
                live.append(self._proposal_row(row))
        settled.sort(key=lambda r: str(r.get("filed_at") or ""), reverse=True)
        omitted = max(0, len(settled) - self.SETTLED_ROWS_KEPT)
        settled = settled[: self.SETTLED_ROWS_KEPT]
        payload: dict[str, Any] = {
            "ok": True,
            "proposals": live + settled,
            "settled_omitted": omitted,
        }
        if omitted:
            payload["note"] = (
                f"{omitted} older settled bid(s) are not listed; the bench still holds them."
            )
        # The cap: the oldest live plan goes first, the bid's row stays.
        with_plans = sorted(
            (i for i, r in enumerate(live) if "steps" in r),
            key=lambda i: str(live[i].get("filed_at") or ""),
        )
        while with_plans and self._measure(payload) > self.LIST_PROPOSALS_CHARS:
            row = live[with_plans.pop(0)]
            for key in self.PROPOSAL_PLAN_KEYS:
                row.pop(key, None)
            row["plan_omitted"] = (
                "This list is too long to carry every plan, so this one is not "
                "handed back. The bench holds it; the deal's live step comes "
                "from toll_bench.current_step."
            )
        return payload

    @staticmethod
    def _measure(value: Any) -> int:
        try:
            return len(json.dumps(value, separators=(",", ":"), default=str))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return len(str(value))

    def _settled(self, row: dict[str, Any]) -> bool:
        if row.get("your_move"):
            return False
        deal = row.get("deal") if isinstance(row.get("deal"), dict) else {}
        deal_status = str(deal.get("status") or "")
        if deal_status in self.ENDED_DEAL_STATUSES:
            return True
        if deal_status:
            return False
        return str(row.get("status") or "") in self.SETTLED_STATUSES

    @staticmethod
    def _settled_line(row: dict[str, Any]) -> dict[str, Any]:
        deal = row.get("deal") if isinstance(row.get("deal"), dict) else {}
        steps = row.get("steps")
        line = {
            key: row.get(key)
            for key in ("id", "target_goal_id", "status", "filed_at", "total_ask_cents")
        }
        line["deal"] = {"deal_id": deal.get("deal_id"), "status": deal.get("status")}
        line["steps_count"] = len(steps) if isinstance(steps, list) else 0
        line["settled"] = True
        return line

    def _owned_proposals(self) -> list[dict[str, Any]]:
        """Every filed bid, WHOLE. `list_proposals` is the MODEL's view.

        The informed plan inherits from the SEALED steps of the bid it
        revises, so this path needs the plan the tool keeps off the wire. It
        reads the API where there is one and falls through to the tool
        otherwise, which is how a provider whose bids are stubbed still works.
        """
        fetch = getattr(self.api, "proposals", None)
        if callable(fetch):
            rows = fetch()
            if isinstance(rows, list):
                return rows
        return self.list_proposals().get("proposals") or []

    def _proposal_row(self, proposal: Any) -> Any:
        """One filed bid, with its plan only where the plan is the work.

        A proposal with a move on it -- a plan to file, a deal to sign, a
        person's answers to read -- keeps everything: that plan is what the
        next filing is written from. A settled or open bid keeps its row and
        says how many steps it had.
        """
        if not isinstance(proposal, dict):
            return proposal
        row = {
            key: value
            for key, value in proposal.items()
            if key not in self.PROPOSAL_DUPLICATE_KEYS
        }
        deal = proposal.get("deal") or {}
        acting = bool(proposal.get("your_move")) or bool(
            isinstance(deal, dict) and deal.get("deal_id")
        )
        if acting:
            row.pop("steps_original", None)
            return row
        steps = proposal.get("steps")
        for key in self.PROPOSAL_PLAN_KEYS:
            row.pop(key, None)
        row["steps_count"] = len(steps) if isinstance(steps, list) else 0
        row["plan_omitted"] = (
            "This bid has no move on it, so its plan is not handed back. It is "
            "the plan you filed; the bench still holds it."
        )
        return row

    def validate_proposal(
        self,
        proposal: dict[str, Any],
        target_id: str | None = None,
        *,
        enforce_cap: bool = True,
    ) -> dict[str, Any]:
        """Check a plan before filing it. THE PROBLEMS COME BACK, NOT THE PLAN.

        With a `target_id` on a contract 3.0 bench this is the BENCH'S OWN
        answer (call 3 of six): every problem at once, each with a plain-words
        `fix`. It files nothing and counts against nothing. Without a
        target_id, or against an older bench, it is the offline mirror below:
        faster, always available, and never authoritative.

        WHAT CHANGED IN 0.34.0. The answer used to carry `corrected_plan` --
        the whole submitted proposal, echoed back -- and a run on 2026-09-09
        called this door fourteen times in three minutes, so fourteen copies
        of the plan sat in the conversation and the fifteenth model call was
        refused by the provider at 129,025 input tokens. The model never
        needed it: where the door can fix the mechanics on its own,
        `submit_proposal` calls the same door and files the corrected plan for
        it. So what comes back is the problems, a one-line summary and the
        counters. The whole door answer, corrected plan included, is written
        verbatim to the run log.

        AND THREE ANSWERS IS A COMPILE, FOUR IS A LOOP. Each want gets
        `MAX_VALIDATE_ATTEMPTS` trips to the door; the fourth returns the
        door's own last problems and the instruction to file nothing.
        """
        local = self._local_validation(proposal)
        if not target_id:
            return local
        key = str(target_id)
        attempts = self._validate_attempts.get(key, 0)
        if enforce_cap and attempts >= MAX_VALIDATE_ATTEMPTS:
            return self._door_loop_is_over(key)
        door = self.validate_at_the_door(target_id, proposal)
        if door is None:
            return {**local, "source": "local_mirror"}
        if enforce_cap:
            attempts += 1
            self._validate_attempts[key] = attempts
        problems = self._door_problem_payload(door, local)["problems"]
        summary = self._problem_summary(problems, ok=bool(door.get("ok")))
        _LOGGER.info(
            "validate attempt %d/%d on target %s: %s",
            attempts,
            MAX_VALIDATE_ATTEMPTS,
            key,
            summary,
        )
        if not door.get("ok"):
            self._last_door_problems[key] = {"problems": problems, "summary": summary}
            self._log_refusal("validate", key, door)
        return {
            "ok": bool(door.get("ok")),
            "problems": self._trim_problems(problems),
            "problem_count": len(problems),
            "summary": summary,
            "corrected_ok": bool(door.get("corrected_ok")),
            "corrections": [
                str(line)[:PROBLEM_TEXT_MAX] for line in (door.get("corrections") or [])
            ][:PROBLEM_LIMIT],
            "attempt": attempts,
            "attempts_left": max(0, MAX_VALIDATE_ATTEMPTS - attempts),
            "source": "bench_validate_door",
            "note": (
                "Nothing was filed. This call never writes a row. Your plan is "
                "NOT echoed back here -- you have it; the whole door answer is "
                "in the run log. When `corrected_ok` is true the door could fix "
                "the mechanics itself and submit_proposal will file that "
                "corrected plan for you, so submit rather than asking for it. "
                f"You have {max(0, MAX_VALIDATE_ATTEMPTS - attempts)} more trips "
                "to this door on this want."
            ),
        }

    def _door_loop_is_over(self, target_id: str) -> dict[str, Any]:
        """The fourth trip to the door. The honest refusal, and nothing filed.

        WHAT FORCED IT: on 2026-09-09 a run called the validate door fourteen
        times in three minutes, filed nothing, and died in the provider. A
        door that keeps answering teaches a stuck model to keep asking.
        """
        last = self._last_door_problems.get(target_id) or {}
        problems = last.get("problems") or []
        _LOGGER.warning(
            "validate door: target %s has spent all %d attempts; filing nothing (%s)",
            target_id,
            MAX_VALIDATE_ATTEMPTS,
            last.get("summary") or "no problems recorded",
        )
        return {
            "ok": False,
            "error": "validate_attempts_exhausted",
            "detail": last.get("summary") or "",
            "terminal": True,
            "attempts": MAX_VALIDATE_ATTEMPTS,
            "attempts_left": 0,
            "problems": self._trim_problems(problems),
            "problem_count": len(problems),
            "summary": last.get("summary") or "",
            "fix": (
                "File nothing on this want. Fix nothing else: the door has "
                "already said the same thing three times."
            ),
            "message": (
                "The validate door has answered this plan "
                f"{MAX_VALIDATE_ATTEMPTS} times and it still has problems. "
                "Nothing was filed and nothing was counted against you. Stop "
                "compiling: do not call the door again on this want and do not "
                "file. The door's own last words are here and verbatim in the "
                "run log."
            ),
        }

    @staticmethod
    def _problem_summary(problems: list[dict[str, Any]], *, ok: bool = False) -> str:
        """One line: how many problems, and which codes.

        Codes are counted, not listed one by one: a real answer off the live
        door carried thirty of the offline mirror's `LOCAL` problems, and a
        line reading "LOCAL, LOCAL, LOCAL" seventeen times says less than
        "LOCAL x30". A code seen once names the step it is on.
        """
        if not problems:
            return "no problems" if ok else "the door named no problems"
        counted: dict[str, list[Any]] = {}
        for problem in problems:
            code = str(problem.get("code") or "?")
            counted.setdefault(code, []).append(problem.get("step_index"))
        parts: list[str] = []
        for code, steps in counted.items():
            if len(steps) == 1:
                parts.append(f"{code} (step {steps[0]})" if steps[0] else code)
            else:
                parts.append(f"{code} x{len(steps)}")
        return f"{len(problems)} problem(s): " + ", ".join(parts)

    @staticmethod
    def _trim_problems(problems: Any) -> list[dict[str, Any]]:
        """The door's problems, capped and with every string bounded.

        A problem is a code, where it is, what is wrong and how to fix it. The
        detail is the door's own words and is kept; it is only stopped from
        quoting an entire plan back into the conversation.
        """
        if not isinstance(problems, list):
            return []
        trimmed: list[dict[str, Any]] = []
        for problem in problems[:PROBLEM_LIMIT]:
            if not isinstance(problem, dict):
                continue
            row = {
                key: (value[:PROBLEM_TEXT_MAX] if isinstance(value, str) else value)
                for key, value in problem.items()
            }
            trimmed.append(row)
        return trimmed

    @staticmethod
    def _log_refusal(door: str, target_id: str, payload: Any) -> None:
        """A door refusal, verbatim, in the run log.

        RULE OF THE NIGHT (2026-09-09): the foreman could not read why a bid
        failed, because the only record of a refusal was the tool result that
        went to the model and nowhere else. Every refusal -- the free validate
        door and the filing door both -- is written here in full, as one JSON
        line, with its codes.
        """
        try:
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            body = str(payload)
        _LOGGER.warning("REFUSAL %s door target=%s %s", door, target_id, body)

    def _local_validation(self, proposal: dict[str, Any]) -> dict[str, Any]:
        schema = self.proposal_schema()
        try:
            from jsonschema import Draft202012Validator
        except ImportError as error:  # pragma: no cover - dependency installation failure
            raise RuntimeError("jsonschema is required for proposal validation") from error
        errors = sorted(
            Draft202012Validator(schema).iter_errors(proposal),
            key=lambda item: list(item.path),
        )
        problems = [
            {
                "path": ".".join(str(part) for part in error.absolute_path) or "$",
                # jsonschema prints the offending INSTANCE in its message, so a
                # whole plan can arrive as one "problem". The door's words are
                # kept; only their length is bounded.
                "message": str(error.message)[:PROBLEM_TEXT_MAX],
            }
            for error in errors
            # finalist_questions has its own gate below, which knows the block
            # shape of contract 2.37. A production schema that still spells the
            # field as four plain strings must not refuse a block-shaped
            # question at home, and it must not double-report one either.
            if not (
                list(error.absolute_path)[:1] == ["finalist_questions"]
            )
        ]
        smart_goals = proposal.get("smart_goals")
        if not isinstance(smart_goals, list) or len(smart_goals) != 1:
            problems.append({"path": "smart_goals", "message": "must contain exactly one goal"})
        problems.extend(finalist_question_problems(proposal.get("finalist_questions")))
        for field in ("pitch_title", "pitch_body"):
            if not str(proposal.get(field) or "").strip():
                problems.append({"path": field, "message": "is required and cannot be blank"})
        # Rule 226. Caught at home so a missing block costs a local round trip
        # rather than a server rejection. `wins` is present-not-empty by
        # design: [] is the honest answer for an agent with no finished walks.
        for field in HOMEWORK_FIELDS:
            value = proposal.get(field)
            if value is None:
                problems.append(
                    {"path": field, "message": "is required (contract 2.42, rule 226)"}
                )
            elif field == "wins":
                if not isinstance(value, list):
                    problems.append({"path": field, "message": "must be an array"})
            elif isinstance(value, str):
                if not value.strip():
                    problems.append({"path": field, "message": "cannot be blank"})
            elif isinstance(value, list):
                if not value:
                    problems.append({"path": field, "message": "cannot be empty"})
        total = proposal.get("total_ask_cents")
        allocation = proposal.get("allocation")
        if isinstance(total, int) and not isinstance(total, bool) and isinstance(allocation, dict):
            amounts = list(allocation.values())
            if all(isinstance(amount, int) and not isinstance(amount, bool) for amount in amounts):
                if sum(amounts) != total:
                    problems.append(
                        {
                            "path": "allocation",
                            "message": f"amounts must sum to total_ask_cents ({total})",
                        }
                    )
        steps = proposal.get("steps")
        finish_line_cents = proposal.get("finish_line_cents")
        if (
            isinstance(total, int)
            and not isinstance(total, bool)
            and isinstance(steps, list)
            and isinstance(finish_line_cents, int)
            and not isinstance(finish_line_cents, bool)
        ):
            line_items = [step.get("line_item_amount") for step in steps if isinstance(step, dict)]
            if len(line_items) == len(steps) and all(
                isinstance(amount, int) and not isinstance(amount, bool) for amount in line_items
            ):
                if sum(line_items) + finish_line_cents != total:
                    problems.append(
                        {
                            "path": "steps",
                            "message": (
                                "line_item_amount values plus finish_line_cents must sum to "
                                f"total_ask_cents ({total})"
                            ),
                        }
                    )
        # RULE 121, ENFORCED AT THE BENCH AS REJ-29 (contract 2.34, 2026-09-03).
        # Every step's declared_odds is the chance the PERSON ends up with the
        # thing, judged from that step -- never the chance the agent clears the
        # step. A plan is filed all at once, so nothing is learned between its
        # steps: a later step declared LOWER than an earlier one can only mean
        # the steps were priced one at a time. Caught here so the filing is not
        # spent on it. Equal is fine. Restating mid-walk (rule 122) may fall.
        # Same order as the bench: an illegal number anywhere is the schema's
        # report (REJ-16 there) and the line is never compared; only a plan
        # whose numbers are all legal gets the line check.
        odds_line = [
            step.get("declared_odds") for step in steps if isinstance(step, dict)
        ] if isinstance(steps, list) else []
        all_legal = odds_line and all(
            not isinstance(v, bool) and isinstance(v, (int, float)) and 0 < v < 1
            for v in odds_line
        )
        if all_legal:
            prev_val, prev_idx = None, None
            for i, v in enumerate(odds_line):
                if prev_val is not None and v < prev_val - 1e-12:
                    problems.append(
                        {
                            "path": f"steps.{i}.declared_odds",
                            "message": (
                                f"step {i + 1} declares {v * 100:.0f}% but step {prev_idx + 1} "
                                f"declared {prev_val * 100:.0f}%. Every declared_odds is your "
                                "chance the PERSON ends up with the thing, judged from that "
                                "step, not the chance you clear the step. Nothing is learned "
                                "between steps at filing time, so the line cannot fall: price "
                                "the whole outcome from each step (rule 121; the bench refuses "
                                "this as REJ-29)"
                            ),
                        }
                    )
                    break
                prev_val, prev_idx = v, i
        # RULE 229 / REJ-33 (contract 2.44): a declared block's fields are the
        # KIND'S, and the kind refuses them in its own sentence. The same
        # limits are checked here -- the window grammar, the duration range,
        # the invitee address, and a message carrying a date or a clock time --
        # so a window typo never costs the one bid this target allows.
        problems.extend(blocks.declaration_problems(steps))
        # RULE 236 / REJ-35: a block that runs on the person's account carries
        # the `connect_account` ROW on its own step. Steven, 2026-09-08: "fix
        # it, remove the old path and lets do it." Which kinds need which
        # provider is the REGISTRY's answer, never a list written here, and a
        # registry we could not read leaves this silent.
        problems.extend(blocks.grant_problems(steps, self._grant_requirements()))
        # RULE 230 (2026-09-05): the typed deliverable. Only a step that
        # CARRIES one is checked, so research, choice, handover and access
        # steps draw no new refusal. A blank left empty is named in the
        # validate door's own plain words.
        problems.extend(blocks.deliverable_problems(steps))
        # THE `calls` KIND, AND WHERE EVERY ARGUMENT CAME FROM (REJ-41,
        # 2026-09-09). Shape and provenance only: an unknown `$from` source, a
        # run quoting a run that has not happened, an account-lane tool with no
        # `connect_account` row on its step, an address typed into a recipient.
        # The TOOL itself is never judged here -- composio:, key: and mcp: name
        # somebody else's catalog and a plain verb names the bench's own.
        problems.extend(
            blocks.calls_problems(steps, proposal.get("finalist_questions"))
        )
        return {
            "ok": not problems,
            "problems": problems,
            "note": (
                "Local validation uses the current production JSON schema plus required "
                "pitch, goal, `finalist_questions` (block shape, the two-text cap, and the "
                "choice-worded text box), declared-odds-line and declared-block field "
                "checks. Production remains authoritative at submit."
            ),
        }

    # ------------------------------------------------------------------
    # THE DRAFT DOOR (rule 241, contract 3.11)
    # ------------------------------------------------------------------
    # The bench holds the plan while it is written, so the WHOLE-DOCUMENT
    # repair loop this class used to run is not the road any more: the outline
    # goes in, the blanks and the one next_fix come back, and the document the
    # bench has been holding is what gets filed. Nothing below repairs a plan
    # and nothing below invents a word -- the door is the validator now.
    #
    # A refusal on any of these three calls comes back as its BODY, not as an
    # exception: a 409 `draft_closed` carries the sentence saying why and the
    # loop reads it to decide whether to start a fresh outline.
    def _draft_answer(self, call: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return call(*args, **kwargs)
        except BookOfHousesApiError as error:
            body = dict(getattr(error, "body", None) or {})
            body.setdefault("ok", False)
            body.setdefault("error", error.code)
            body.setdefault("message", error.message)
            body["status"] = error.status
            return body

    def put_draft(
        self, target_id: str, outline: dict[str, Any], *, kind: str = "bid"
    ) -> dict[str, Any]:
        """The OUTLINE in. The bench expands every mechanic it owns and names
        every blank that is the agent's, each with one sentence."""
        return self._draft_answer(
            self.api.put_proposal_draft, target_id, dict(outline or {}), kind
        )

    def patch_draft(
        self, target_id: str, patches: list[dict[str, Any]], *, kind: str = "bid"
    ) -> dict[str, Any]:
        """ONE PIECE BACK, by path. Spends one of the bench's rounds."""
        return self._draft_answer(
            self.api.patch_proposal_draft, target_id, list(patches or []), kind
        )

    def read_draft(self, target_id: str, *, kind: str = "bid") -> dict[str, Any]:
        """The draft as it stands. Costs no round."""
        return self._draft_answer(self.api.get_proposal_draft, target_id, kind)

    def file_from_draft(
        self, target_id: str, idempotency_key: str = ""
    ) -> dict[str, Any]:
        """File the document the bench is holding, through the ordinary bid
        door, unchanged: `POST .../proposals {"from_draft": true}`.

        Nothing is repaired here. Every mechanic was the bench's own and every
        word was the agent's, checked at the door on the way in, so the only
        things this method owns are the ones filing has always owned: the
        reachability handshake and this fleet's slot on the want.
        """
        reachability = self.ensure_reachable()
        if not reachability.get("ok"):
            return {
                "ok": False,
                "error": "agent_not_reachable",
                "message": (
                    "The two-ping reachability handshake did not complete. "
                    "No proposal was filed."
                ),
                "reachability": reachability,
            }
        reservation = None
        target_round = None
        fleet_engaged = self.fleet is not None and bool(self.fleet_agent_id)
        if fleet_engaged:
            try:
                brief = self._brief_for(target_id)
            except BookOfHousesApiError:
                brief = {}
            target_round = str(brief.get("round") or 1)
            your_bid = brief.get("your_bid") or None
            if your_bid:
                self.fleet.mark_target_reviewed(
                    agent_id=self.fleet_agent_id,
                    target_id=target_id,
                    target_round=target_round,
                )
                if your_bid.get("status") == "withdrawn":
                    return {
                        "ok": False,
                        "error": "participation_ended_this_round",
                        "terminal": True,
                        "message": (
                            "This agent withdrew from the current round; "
                            "participation is over until the want reposts."
                        ),
                    }
                return {
                    "ok": True,
                    "proposal_id": your_bid.get("proposal_id"),
                    "idempotent": True,
                    "message": "A bid from this agent is already live on the current round.",
                }
            reservation = self.fleet.reserve_proposal(
                target_id=target_id,
                target_round=target_round,
                agent_id=self.fleet_agent_id,
                idempotency_key=idempotency_key,
                limit=self.fleet_proposal_limit,
            )
            if not reservation.allowed:
                return {
                    "ok": False,
                    "error": "fleet_proposal_limit",
                    "message": (
                        f"This Toll Harness fleet already reserved {reservation.count} of "
                        f"{reservation.limit} proposal slots for the target's current round."
                    ),
                    "fleet_count": reservation.count,
                    "fleet_limit": reservation.limit,
                    "target_round": target_round,
                }
            if reservation.status == "confirmed" and reservation.proposal_id:
                return {
                    "ok": True,
                    "proposal_id": reservation.proposal_id,
                    "idempotent": True,
                }
            idempotency_key = reservation.idempotency_key
        try:
            result = self.api.submit_proposal(
                target_id, {"from_draft": True}, idempotency_key
            )
        except BookOfHousesApiError as error:
            self._log_refusal(
                "filing",
                target_id,
                {
                    "status": error.status,
                    "code": error.code,
                    "rej": error.rej,
                    "detail": error.message,
                    "body": getattr(error, "body", None),
                    "from_draft": True,
                },
            )
            if fleet_engaged and reservation is not None and 400 <= error.status < 500:
                self.fleet.release_reservation(
                    target_id=target_id,
                    target_round=target_round,
                    agent_id=self.fleet_agent_id,
                )
            if fleet_engaged and error.status in (404, 409):
                self.fleet.mark_target_reviewed(
                    agent_id=self.fleet_agent_id,
                    target_id=target_id,
                    target_round=target_round,
                )
                return {
                    "ok": False,
                    "error": "proposal_refused_terminally",
                    "terminal": True,
                    "status": error.status,
                    "refusal": error.code,
                    "message": (
                        f"Production refused the bid ({error.code}). This round is "
                        "recorded as reviewed; do not retry it."
                    ),
                }
            return {
                "ok": False,
                "error": error.code,
                "status": error.status,
                "message": error.message,
            }
        if fleet_engaged and reservation is not None:
            proposal_id = str(result.get("proposal_id") or "")
            if proposal_id:
                self.fleet.confirm_proposal(
                    target_id=target_id,
                    target_round=target_round,
                    agent_id=self.fleet_agent_id,
                    proposal_id=proposal_id,
                )
            elif result.get("ok") is False:
                self.fleet.release_reservation(
                    target_id=target_id,
                    target_round=target_round,
                    agent_id=self.fleet_agent_id,
                )
        return self._filing_receipt(result)

    def file_plan_from_draft(
        self, target_id: str, proposal_id: str, idempotency_key: str = ""
    ) -> dict[str, Any]:
        """The informed plan, filed from the draft the bench holds (rule 113
        through rule 241). `accept_rules` rides the body because filing the
        plan IS the agent's signature, and it is the agent's to give."""
        try:
            return self._filing_receipt(
                self.api.submit_informed_plan(
                    target_id,
                    proposal_id,
                    {"from_draft": True, "accept_rules": True},
                    idempotency_key,
                )
            )
        except BookOfHousesApiError as error:
            self._log_refusal(
                "plan",
                target_id,
                {
                    "status": error.status,
                    "code": error.code,
                    "rej": error.rej,
                    "detail": error.message,
                    "body": getattr(error, "body", None),
                    "from_draft": True,
                },
            )
            return {
                "ok": False,
                "error": error.code,
                "status": error.status,
                "message": error.message,
            }

    def _trims_of_the_small_proposal(
        self, target_id: str, proposal: dict[str, Any]
    ) -> list[Any]:
        """ONE FREE CALL AT THE DOOR, and what it says is logged, not retried.

        `POST .../proposals/validate` writes nothing, counts against nothing
        and answers with every problem at once plus `trimmed` -- each entry
        {path, from, to, from_chars, to_chars}, exactly what would be stored
        if this proposal were filed as it stands. A TRIM IS NOT A REFUSAL
        (rule 244): the door takes the words and shortens what is over a cap.
        So the trims come back to be logged, the problems are written to the
        run log, and the proposal is filed either way -- the door's own
        refusal is the record, and there is no second model call in a stage
        that is one call by law.
        """
        door = self.validate_at_the_door(target_id, proposal)
        if not isinstance(door, dict):
            return []
        trims = [row for row in (door.get("trimmed") or []) if row]
        problems = [p for p in (door.get("problems") or []) if isinstance(p, dict)]
        if problems:
            _LOGGER.warning(
                "Validate door named %d problem(s) on the proposal for target "
                "%s (%s); filing it anyway so the door's own answer is the "
                "record",
                len(problems),
                target_id,
                ", ".join(str(p.get("code") or "?") for p in problems),
            )
        if trims:
            _LOGGER.info(
                "Validate door will trim %d field(s) of the proposal for "
                "target %s: %s",
                len(trims),
                target_id,
                ", ".join(str(row.get("path") or "?") for row in trims),
            )
        return trims

    def _repair_the_plan_shaped_proposal(
        self, target_id: str, proposal: dict[str, Any], brief: Any
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """EVERY REPAIR THAT READS `steps`, in one place. (proposal, refusal).

        This is the road a plan-shaped proposal takes: the brief's required
        blocks merged in, the contact research bound, one outreach spread over
        the picked contacts, a grant step retired into its action, the blank
        form dropped, the local mirror and the free validate door consulted,
        and the deliverable placeholder cleared. It is kept whole and
        unchanged for a bench that still takes a whole plan at the proposal
        door; since rule 243 (2026-09-11) the proposal this harness writes is
        seven fields with no steps, and every one of these repairs would run
        over a document that is not there.
        """
        proposal, inserted = blocks.merge_required_blocks(
            proposal,
            brief.get("required_blocks"),
            brief.get("plan_template"),
            want=brief.get("want"),
            needs=self._grant_requirements(),
            block_templates=brief.get("block_templates"),
            # RULES 237/238: the form is not only steps. When the plan reaches
            # a person and the four questions ask nobody who, the brief's own
            # contact_picker goes on the bid -- REJ-40 otherwise, and on a
            # one-bid-per-want board that is the whole round.
            bid_template=brief.get("bid_template"),
            bid_template_notes=brief.get("bid_template_notes"),
            # "FIND THEM FOR ME": this person declined to pick, so no picker
            # goes on the bid however much the plan reaches a person.
            contact_research=brief.get("contact_research"),
        )
        if inserted:
            _LOGGER.warning(
                "Plan for target %s did not carry the brief's form; filled it "
                "in and filed that (%s)",
                target_id,
                ", ".join(inserted),
            )
        # "FIND THEM FOR ME" (2026-09-09). The person answered the contact
        # question with a research brief instead of a pick, so the recipient
        # must come out of a `platform.research` run (or `contact_from`:
        # "research" on a legacy act). Nothing here writes an address: there
        # is none to write, and an invented one is the refusal REJ-41 exists
        # for.
        proposal, bound = blocks.bind_contact_research(
            proposal, blocks.contact_research_of(brief)
        )
        if bound:
            _LOGGER.warning(
                "Plan for target %s reaches a person the person asked us to "
                "find; bound the outreach to the research run (%s)",
                target_id,
                "; ".join(bound),
            )
        # N PEOPLE, ONE OUTREACH: `each` on a calls run, one act per contact
        # on a legacy act. A legacy act has no `each` field and the harness
        # will not invent a tool key to give it one.
        proposal, spread = blocks.spread_over_contacts(
            proposal, brief.get("selected_contacts")
        )
        if spread:
            _LOGGER.warning(
                "Plan for target %s sends to more than one picked contact; "
                "spread the outreach (%s)",
                target_id,
                "; ".join(spread),
            )
        # RULE 236 / REJ-38 (Steven, 2026-09-08). A CONNECTION IS NOT A STEP.
        # A model that composes its own steps rather than copying the block
        # writes the GRANT step it was taught for a year; on this bench that is
        # refused and the round is gone. Fires only where the brief's own
        # template publishes the row and no GRANT step for it, so an older
        # bench, and the `access` mold, are untouched.
        proposal, retired = blocks.retire_grant_steps(
            proposal, self._published_steps(brief)
        )
        if retired:
            _LOGGER.warning(
                "Plan for target %s filed a connection as a step of its own; "
                "rule 236 moved it into the action (%s)",
                target_id,
                "; ".join(retired),
            )
        # CONTRACT 3.0: THE TEMPLATE IS A FORM. `plan_template` is a blank
        # skeleton -- the mechanics filled, every agent-owned word an explicit
        # "" or null -- so a model that copies it files steps with no title
        # and no promise. Those are dropped here, before the round is spent on
        # them, and NOTHING is written in their place: the model's words or
        # nothing (rule 228 amended, Steven 2026-09-05). A platform-written
        # block step keeps its blanks, because they are the platform's.
        proposal, dropped, below_floor = blocks.drop_blank_form_steps(
            proposal, floor=blocks.band_floor(brief.get("plan_template"))
        )
        if dropped:
            _LOGGER.warning(
                "Plan for target %s copied the brief's form without filling it; "
                "dropped %s",
                target_id,
                ", ".join(dropped),
            )
        if below_floor:
            _LOGGER.warning(
                "Plan for target %s was the blank form and nothing else; "
                "nothing filed",
                target_id,
            )
            return proposal, {
                "ok": False,
                "error": "plan_is_still_the_blank_form",
                "dropped": dropped,
                "terminal": False,
                "message": (
                    "Nothing was filed. The brief's plan_template is a blank FORM, "
                    "not a plan: every step arrived with an empty `title` and an "
                    "empty `outcome_promise` for you to write. Those steps were "
                    "dropped and what is left is shorter than this band allows. "
                    "Write each step in your own words -- the harness will not "
                    "write them for you -- and submit again. bid_template_notes "
                    "on the brief lists every blank."
                ),
            }
        local = self._grant_gap_never_blocks_the_filing(
            self.validate_proposal(proposal), brief.get("plan_template")
        )
        # THE BENCH IS AUTHORITATIVE, AND ITS ANSWER IS FREE (contract 3.0).
        # The local mirror stays as the offline pre-check; when the bench
        # publishes the validate door, the door decides, because a mirror that
        # has drifted must never bury a plan the door would take.
        door = self.validate_at_the_door(target_id, proposal)
        if door is None:
            if not local["ok"]:
                return proposal, {
                    "ok": False,
                    "error": "local_validation_failed",
                    **local,
                }
        elif not door.get("ok"):
            corrected = door.get("corrected_plan")
            if door.get("corrected_ok") and isinstance(corrected, dict):
                # Mechanical fixes only -- the door invents no words -- and it
                # has already run the whole bid door over the result.
                _LOGGER.info(
                    "Validate door corrected the mechanics of the plan for "
                    "target %s (%s); filing the corrected plan",
                    target_id,
                    ", ".join(str(line) for line in (door.get("corrections") or [])),
                )
                proposal = corrected
            else:
                passes = self._door_repairs.get(target_id, 0)
                if passes < MAX_DOOR_REPAIR_PASSES:
                    self._door_repairs[target_id] = passes + 1
                    payload = self._door_problem_payload(door, local)
                    self._log_refusal("filing", target_id, door)
                    return proposal, {
                        "ok": False,
                        "error": "plan_has_problems",
                        "terminal": False,
                        "problems": self._trim_problems(payload["problems"]),
                        "problem_count": payload["problem_count"],
                        "summary": self._problem_summary(payload["problems"]),
                        "corrections": payload["corrections"],
                        "message": (
                            "Nothing was filed and nothing was counted against you. "
                            "The bench listed EVERY problem with this plan at once; "
                            "each carries a `fix` in plain words and a 1-based "
                            "`step_index`. Fix them in your own words and submit "
                            "once more."
                        ),
                    }
                _LOGGER.warning(
                    "Validate door still refuses the plan for target %s after a "
                    "repair pass; filing it so the door's own refusal is the record",
                    target_id,
                )
        # RULE 230, LAST. A `deliverable` still carrying the form's
        # "<angle bracket>" is REJ-36 at the door and would print on the
        # person's card as though it were a promise. Both the local mirror and
        # the free validate door have already named it by here, and the model
        # has spent its repair pass, so the placeholder comes out rather than
        # costing the round. The harness writes nothing in its place: an
        # absent deliverable reads as channel "text", which is what a step
        # that never said otherwise always meant.
        proposal, cleared = blocks.clear_blank_deliverables(proposal)
        if cleared:
            _LOGGER.warning(
                "Plan for target %s still carried the deliverable blank on "
                "step(s) %s after the door; removed the placeholder rather "
                "than filing it as a promise",
                target_id,
                ", ".join(str(index + 1) for index in cleared),
            )
        # DID IT COPY, OR DID IT COMPOSE? One line per filing, against the
        # program this brief handed over. Nothing is refused on it -- the
        # foreman grades the run, the door judges the plan.
        self._log_program_diff(target_id, proposal, brief)
        return proposal, None

    def submit_proposal(
        self, target_id: str, proposal: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """FILE ONE PROPOSAL. Two roads, and the proposal itself says which.

        A PROPOSAL IS SEVEN FIELDS (rule 243, 2026-09-11): a title, a
        paragraph, one odds number, a price, one to three research links, up
        to three questions for the person, and the tools it needs. It carries
        no steps, so it takes the small road -- one free call at the validate
        door, then the filing door. A proposal that carries steps is the old
        whole-plan shape and takes the repair road, unchanged, for a bench
        that still wants one.
        """
        try:
            brief = self._brief_for(target_id)
        except BookOfHousesApiError as error:
            if error.status == 404:
                return {
                    "ok": False,
                    "error": "target_not_open",
                    "terminal": True,
                    "message": (
                        "Production reports this target is not open. "
                        "No proposal was filed; do not retry it."
                    ),
                }
            raise
        if draft.is_small_proposal(proposal):
            # RULE 243 (2026-09-11): A PROPOSAL IS SEVEN FIELDS AND ONE CALL.
            # There are no steps to repair, no blocks to merge and no local
            # step validator to run -- the bench's own door is the only check,
            # it is free, and what it says it TRIMMED rides back on the
            # receipt as `bench_fixed` so the runtime logs the cut instead of
            # asking the model to make the same sentence shorter.
            trims = self._trims_of_the_small_proposal(target_id, proposal)
        else:
            trims = []
            proposal, refusal = self._repair_the_plan_shaped_proposal(
                target_id, proposal, brief
            )
            if refusal is not None:
                return refusal
        reachability = self.ensure_reachable()
        if not reachability.get("ok"):
            return {
                "ok": False,
                "error": "agent_not_reachable",
                "message": (
                    "The two-ping reachability handshake did not complete. "
                    "No proposal was filed."
                ),
                "reachability": reachability,
            }
        reservation = None
        target_round = None
        fleet_engaged = self.fleet is not None and bool(self.fleet_agent_id)
        if fleet_engaged:
            # A repost reuses the target id and bumps the brief's `round`, so
            # the fleet ledger must be keyed by the CURRENT round or slots from
            # a dead round block the repost forever. It comes off the same
            # brief the required blocks came from, one read for both.
            target_round = str(brief.get("round") or 1)
            your_bid = brief.get("your_bid") or None
            if your_bid:
                # Participation on the current round already exists — filed or
                # withdrawn, production will refuse a second bid. Record the
                # round as reviewed so the market scan moves on.
                self.fleet.mark_target_reviewed(
                    agent_id=self.fleet_agent_id,
                    target_id=target_id,
                    target_round=target_round,
                )
                if your_bid.get("status") == "withdrawn":
                    return {
                        "ok": False,
                        "error": "participation_ended_this_round",
                        "terminal": True,
                        "message": (
                            "This agent withdrew from the current round; "
                            "participation is over until the want reposts."
                        ),
                    }
                return {
                    "ok": True,
                    "proposal_id": your_bid.get("proposal_id"),
                    "idempotent": True,
                    "message": "A bid from this agent is already live on the current round.",
                }
            reservation = self.fleet.reserve_proposal(
                target_id=target_id,
                target_round=target_round,
                agent_id=self.fleet_agent_id,
                idempotency_key=idempotency_key,
                limit=self.fleet_proposal_limit,
            )
            if not reservation.allowed:
                return {
                    "ok": False,
                    "error": "fleet_proposal_limit",
                    "message": (
                        f"This Toll Harness fleet already reserved {reservation.count} of "
                        f"{reservation.limit} proposal slots for the target's current round."
                    ),
                    "fleet_count": reservation.count,
                    "fleet_limit": reservation.limit,
                    "target_round": target_round,
                }
            if reservation.status == "confirmed" and reservation.proposal_id:
                return {
                    "ok": True,
                    "proposal_id": reservation.proposal_id,
                    "idempotent": True,
                }
            idempotency_key = reservation.idempotency_key
        try:
            try:
                result = self.api.submit_proposal(target_id, proposal, idempotency_key)
            except BookOfHousesApiError as first:
                # RULE 228: THE REFUSAL CARRIES THE FORM. A REJ-32 body holds
                # the same plan_template the brief published, so the one move
                # left is to fill it in and file once. Once: a second refusal
                # is the round, not a retry loop.
                # RULE 236 / REJ-38 is the one refusal that carries NO form:
                # what it hands back is the row, and the brief's template is
                # where the row is published. Repair from there, or let the
                # door's own words be the answer.
                if first.rej == REJ_GRANT_STEP_REMOVED:
                    proposal, moved = self._retire_grant_step_after(
                        target_id, first, proposal, self._published_steps(brief)
                    )
                    if not moved:
                        raise
                    result = self.api.submit_proposal(
                        target_id,
                        proposal,
                        f"{idempotency_key}-{_retry_tag(first.rej)}",
                    )
                # RULE 238 / REJ-40: the plan reaches a person and asked nobody
                # who. The answer is the QUESTION, and the brief published it,
                # so it goes on and the bid is filed ONCE more.
                elif first.rej == REJ_CONTACT_ROUTE:
                    proposal, asked = self._ask_who_after(target_id, first, proposal, brief)
                    if not asked:
                        raise
                    result = self.api.submit_proposal(
                        target_id,
                        proposal,
                        f"{idempotency_key}-{_retry_tag(first.rej)}",
                    )
                # REJ-41: only one thing here is the harness's to repair.
                elif first.rej == REJ_ARGUMENT_PROVENANCE:
                    proposal, bound = self._bind_the_research_after(
                        target_id, first, proposal, brief
                    )
                    if not bound:
                        raise
                    result = self.api.submit_proposal(
                        target_id,
                        proposal,
                        f"{idempotency_key}-{_retry_tag(first.rej)}",
                    )
                elif first.rej not in REJ_CARRIES_THE_FORM or not first.plan_template:
                    raise
                else:
                    proposal, repaired = blocks.merge_required_blocks(
                        proposal,
                        [
                            str(act.get("kind"))
                            for step in first.plan_template
                            if isinstance(step, dict)
                            for act in (step.get("acts") or [])
                            if isinstance(act, dict) and act.get("kind")
                        ],
                        first.plan_template,
                        want=brief.get("want"),
                        needs=self._grant_requirements(),
                        bid_template=brief.get("bid_template"),
                        bid_template_notes=brief.get("bid_template_notes"),
                    )
                    if not repaired:
                        raise
                    _LOGGER.warning(
                        "Target %s refused the bid %s; filed the template steps "
                        "the refusal carried (%s) and re-filed once",
                        target_id,
                        first.rej,
                        ", ".join(repaired),
                    )
                    result = self.api.submit_proposal(
                        target_id,
                        proposal,
                        f"{idempotency_key}-{_retry_tag(first.rej)}",
                    )
        except BookOfHousesApiError as error:
            # RULE OF THE NIGHT (2026-09-09): a refusal the foreman cannot read
            # is a run nobody can grade. Every filing-door refusal is written
            # to the run log in full, whatever the harness does with it next.
            self._log_refusal(
                "filing",
                target_id,
                {
                    "status": error.status,
                    "code": error.code,
                    "rej": error.rej,
                    "detail": error.message,
                    "body": getattr(error, "body", None),
                },
            )
            if error.rej in (REJ_BLOCK_DECLARATION, REJ_HOLLOW_BLOCK):
                if fleet_engaged and reservation is not None:
                    self.fleet.release_reservation(
                        target_id=target_id,
                        target_round=target_round,
                        agent_id=self.fleet_agent_id,
                    )
                refusal = self._block_refusal(target_id, error)
                if refusal["terminal"] and fleet_engaged:
                    self.fleet.mark_target_reviewed(
                        agent_id=self.fleet_agent_id,
                        target_id=target_id,
                        target_round=target_round,
                    )
                return refusal
            if fleet_engaged and reservation is not None and 400 <= error.status < 500:
                self.fleet.release_reservation(
                    target_id=target_id,
                    target_round=target_round,
                    agent_id=self.fleet_agent_id,
                )
            if error.rej == REJ_GRANT_STEP_REMOVED:
                # RULE 236: the round is NOT spent -- nothing was written -- so
                # this comes back non-terminal, carrying the door's own words
                # and the row to add. The reservation was released just above.
                if fleet_engaged and reservation is not None:
                    self.fleet.release_reservation(
                        target_id=target_id,
                        target_round=target_round,
                        agent_id=self.fleet_agent_id,
                    )
                return self._grant_step_removed_refusal(error)
            if error.rej == REJ_ARGUMENT_PROVENANCE:
                # Nothing was written, so the round is not spent; the door's
                # own words carry the run and the argument it refused.
                return self._argument_provenance_refusal(error)
            if error.rej == REJ_CONTACT_ROUTE:
                # RULE 238: a refused bid writes nothing, so the round is not
                # spent. The reservation was released just above; the door's
                # own words and the question to ask come back non-terminal.
                return self._contact_route_refusal(error)
            if fleet_engaged and error.status in (404, 409):
                # Terminal refusals for this round: bidding closed because an
                # agent is selected, a bid already on file, participation
                # ended, or the target
                # gone. Retrying cannot succeed until the want reposts (which
                # opens a new round and a new review key) — record the round as
                # reviewed so the market scan advances instead of looping.
                self.fleet.mark_target_reviewed(
                    agent_id=self.fleet_agent_id,
                    target_id=target_id,
                    target_round=target_round,
                )
                return {
                    "ok": False,
                    "error": "proposal_refused_terminally",
                    "terminal": True,
                    "status": error.status,
                    "refusal": error.code,
                    "message": (
                        f"Production refused the bid ({error.code}). This round is "
                        "recorded as reviewed; do not retry it."
                    ),
                }
            raise
        if fleet_engaged and reservation is not None:
            proposal_id = str(result.get("proposal_id") or "")
            if proposal_id:
                self.fleet.confirm_proposal(
                    target_id=target_id,
                    target_round=target_round,
                    agent_id=self.fleet_agent_id,
                    proposal_id=proposal_id,
                )
            elif result.get("ok") is False:
                self.fleet.release_reservation(
                    target_id=target_id,
                    target_round=target_round,
                    agent_id=self.fleet_agent_id,
                )
        receipt = self._filing_receipt(result)
        if trims and isinstance(receipt, dict):
            # WHAT THE BENCH FIXED ON THE WAY IN rides the receipt, because
            # the filing door answers with ids and a status and says nothing
            # about the cut. The runtime logs it and carries on; it is never
            # a reason to ask the model again.
            receipt = dict(receipt)
            receipt["bench_fixed"] = list(trims)
        return receipt

    # A filing answer is ids and status. Some benches echo the plan back with
    # it, and the model already has the plan -- it just wrote it. Echoing it
    # into the conversation is a second copy of the largest thing in the run.
    FILING_ECHO_KEYS = (
        "proposal",
        "plan",
        "steps",
        "steps_original",
        "pitch_body",
        "smart_goals",
        "finalist_questions",
        "brief",
        "plan_template",
        "block_templates",
        "bid_template",
        "plan_examples",
    )

    def _filing_receipt(self, result: Any) -> Any:
        """What came back from filing: the ids and the status, never the plan."""
        if not isinstance(result, dict):
            return result
        dropped = [key for key in result if key in self.FILING_ECHO_KEYS]
        if not dropped:
            return result
        _LOGGER.info(
            "Filing receipt: dropped the echoed %s from the tool result "
            "(it is in the run log)",
            ", ".join(dropped),
        )
        _LOGGER.debug("Filing answer in full: %s", json.dumps(result, default=str))
        receipt = {key: value for key, value in result.items() if key not in dropped}
        receipt["echo_omitted"] = dropped
        return receipt

    WITHDRAW_CAUSES = ("cannot_deliver", "other")
    WITHDRAW_REASON_LIMIT = 1000

    def withdraw_proposal(
        self, proposal_id: str, *, reason: str, cause: str = "other"
    ) -> dict[str, Any]:
        """Leave a bid out loud, through the public exit, and say why.

        A selected agent that cannot produce its plan withdraws with cause
        ``cannot_deliver`` instead of retrying in silence: the person learns
        why the pick failed and every held bid on the want returns to the
        table. Retrying forever is not an exit.
        """
        text = str(reason or "").strip()
        if not text:
            return {
                "ok": False,
                "error": "withdraw_reason_required",
                "message": "A withdrawal must say why in the agent's own words.",
            }
        if cause not in self.WITHDRAW_CAUSES:
            return {
                "ok": False,
                "error": "invalid_withdraw_cause",
                "allowed": list(self.WITHDRAW_CAUSES),
            }
        return self.api.withdraw_proposal(
            proposal_id,
            {"reason": text[: self.WITHDRAW_REASON_LIMIT], "cause": cause},
        )

    def read_finalist_answers(self, target_id: str, proposal_id: str) -> dict[str, Any]:
        return self.api.finalist_answers(target_id, proposal_id)

    def submit_informed_plan(
        self,
        target_id: str,
        proposal_id: str,
        plan: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        allowed = {"steps", "finish_line_cents", "finish_line_odds", "accept_rules"}
        unexpected = sorted(set(plan) - allowed)
        if unexpected:
            return {
                "ok": False,
                "error": "informed_plan_changes_sealed_terms",
                "message": (
                    "An informed plan may revise only steps and finish-line allocation. "
                    "Money, timeline, pitch, goal, and questions remain sealed."
                ),
                "unexpected_fields": unexpected,
                "allowed_fields": sorted(allowed),
            }
        if plan.get("accept_rules") is not True:
            return {
                "ok": False,
                "error": "rules_acceptance_required",
                "message": "accept_rules must be true when first filing an informed plan",
            }
        proposals = self._owned_proposals()
        original = next((item for item in proposals if item.get("id") == proposal_id), None)
        if original is None or original.get("target_goal_id") != target_id:
            return {"ok": False, "error": "owned_proposal_not_found"}
        submitted_plan = dict(plan)
        original_steps = original.get("steps") or []
        revised_steps = plan.get("steps") or []
        if (
            len(revised_steps) == len(original_steps)
            and all(isinstance(step, dict) for step in original_steps)
            and all(isinstance(step, dict) for step in revised_steps)
        ):
            merged_steps = []
            for original_step, revised_step in zip(
                original_steps, revised_steps, strict=True
            ):
                merged_step = {**original_step, **revised_step}
                if "har_blocks" not in revised_step and original_step.get("har_blocks"):
                    merged_step["har_blocks"] = original_step["har_blocks"]
                    merged_step["ask"] = original_step.get("ask")
                merged_steps.append(merged_step)
            submitted_plan["steps"] = merged_steps
        # RULE 228 at the SECOND door. The informed plan runs through the same
        # validator as the bid, so a revision that drops the want's required
        # block is REJ-32 here too -- and this is the filing the person is
        # already waiting on. The brief carries the form; fill it and file it.
        try:
            brief = self._brief_for(target_id)
        except BookOfHousesApiError as error:
            # A want whose agent is already selected can stop answering the
            # open-target brief. The plan the person is waiting on must still
            # file: without the form here, the REJ-32 refusal still carries it.
            _LOGGER.warning(
                "Brief unavailable for target %s at plan time (%s); relying on "
                "the refusal to carry the template",
                target_id,
                error.code,
            )
            brief = {}
        submitted_plan, inserted = blocks.merge_required_blocks(
            submitted_plan,
            brief.get("required_blocks"),
            brief.get("plan_template"),
            want=brief.get("want"),
            needs=self._grant_requirements(),
            block_templates=brief.get("block_templates"),
        )
        submitted_plan, bound = blocks.bind_contact_research(
            submitted_plan, blocks.contact_research_of(brief)
        )
        if bound:
            _LOGGER.warning(
                "Informed plan for target %s reaches a person the person asked "
                "us to find; bound the outreach to the research run (%s)",
                target_id,
                "; ".join(bound),
            )
        # THE PICKS ARE IN. This is the filing that knows how many people the
        # person actually chose, so the fan-out happens here as well as at bid
        # time: `each` on a calls run, one act per contact on a legacy act.
        submitted_plan, spread = blocks.spread_over_contacts(
            submitted_plan,
            brief.get("selected_contacts"),
            (original or {}).get("finalist_questions"),
        )
        if spread:
            _LOGGER.warning(
                "Informed plan for target %s sends to more than one picked "
                "contact; spread the outreach (%s)",
                target_id,
                "; ".join(spread),
            )
        submitted_plan, retired = blocks.retire_grant_steps(
            submitted_plan, self._published_steps(brief)
        )
        if retired:
            _LOGGER.warning(
                "Informed plan for target %s carried a connection as a step of "
                "its own; rule 236 moved it into the action (%s)",
                target_id,
                "; ".join(retired),
            )
        if inserted:
            _LOGGER.warning(
                "Informed plan for target %s did not carry the brief's form; "
                "filled it in and filed that (%s)",
                target_id,
                ", ".join(inserted),
            )
        # CONTRACT 3.0, at the SECOND door: the same blank form reaches the
        # informed plan, and this is the filing the person is already waiting
        # on. Drop what was copied and never filled; write nothing in its place.
        submitted_plan, dropped, below_floor = blocks.drop_blank_form_steps(
            submitted_plan, floor=blocks.band_floor(brief.get("plan_template"))
        )
        if dropped:
            _LOGGER.warning(
                "Informed plan for target %s copied the brief's form without "
                "filling it; dropped %s",
                target_id,
                ", ".join(dropped),
            )
        if below_floor:
            return {
                "ok": False,
                "error": "plan_is_still_the_blank_form",
                "dropped": dropped,
                "terminal": False,
                "message": (
                    "Nothing was filed. The brief's plan_template is a blank FORM: "
                    "every step arrived with an empty `title` and an empty "
                    "`outcome_promise` for you to write. Write each step in your "
                    "own words and file again."
                ),
            }
        candidate = {
            key: original.get(key)
            for key in (
                "model_declared",
                "total_ask_cents",
                "allocation",
                "timeline_days",
                "finish_line",
                "subsidy_declared",
                "operator_relationship_disclosure",
                "campaign",
                "smart_goals",
                "finalist_questions",
                "pitch_title",
                "pitch_body",
                "person_cost_estimate",
                # Frozen at bid time (rule 226) but still required by the
                # published schema, so a revision rebuilt from the sealed
                # original must carry them or it fails validation at home
                # before it ever reaches the server.
                *HOMEWORK_FIELDS,
            )
            if original.get(key) is not None
        }
        candidate["steps"] = submitted_plan.get("steps")
        candidate["finish_line_cents"] = submitted_plan.get(
            "finish_line_cents", original.get("finish_line_cents") or 0
        )
        # RULE 241: THE BENCH IS THE VALIDATOR NOW. This used to be an offline
        # jsonschema mirror standing between the person and the plan they are
        # waiting on -- and a mirror that has drifted buries a plan the door
        # would have taken. The plan is built at the draft door, which
        # re-validates on every round, so the mirror runs for the LOG and
        # refuses nothing. If the plan is really wrong the bench says so, in
        # its own words, on the filing that follows.
        validation = self._grant_gap_never_blocks_the_filing(
            self.validate_proposal(candidate), brief.get("plan_template")
        )
        if not validation["ok"]:
            _LOGGER.warning(
                "Local mirror has problems with the informed plan for target "
                "%s; filing anyway and letting the bench decide (%s)",
                target_id,
                self._problem_summary(validation.get("problems") or []),
            )
        # RULE 230: the placeholder never reaches the person's card, and it
        # comes out only here -- the validator has already had its say, so the
        # model was told before the harness decided anything.
        submitted_plan, cleared = blocks.clear_blank_deliverables(submitted_plan)
        if cleared:
            _LOGGER.warning(
                "Informed plan for target %s still carried the deliverable "
                "blank on step(s) %s; removed the placeholder",
                target_id,
                ", ".join(str(index + 1) for index in cleared),
            )
        try:
            return self.api.submit_informed_plan(
                target_id, proposal_id, submitted_plan, idempotency_key
            )
        except BookOfHousesApiError as error:
            if error.rej == REJ_GRANT_STEP_REMOVED:
                submitted_plan, moved = self._retire_grant_step_after(
                    target_id, error, submitted_plan, self._published_steps(brief)
                )
                if not moved:
                    return self._grant_step_removed_refusal(error)
                return self.api.submit_informed_plan(
                    target_id,
                    proposal_id,
                    submitted_plan,
                    f"{idempotency_key}-{_retry_tag(error.rej)}",
                )
            if error.rej == REJ_CONTACT_ROUTE:
                # RULE 238 at the SECOND door, and the picker is NOT the answer
                # here: the revision payload the bench validates carries the
                # steps and not `finalist_questions`, and the four are frozen
                # at bid time anyway. So this hands back the door's own words
                # and says what a revision can actually carry -- the
                # `contact_ref` the person's pick filled in. Non-terminal: a
                # refused revision never writes, and the person is waiting.
                return self._contact_route_refusal(error)
            if error.rej in REJ_CARRIES_THE_FORM and error.plan_template:
                submitted_plan, repaired = blocks.merge_required_blocks(
                    submitted_plan,
                    [
                        str(act.get("kind"))
                        for step in error.plan_template
                        if isinstance(step, dict)
                        for act in (step.get("acts") or [])
                        if isinstance(act, dict) and act.get("kind")
                    ],
                    error.plan_template,
                    want=brief.get("want"),
                    needs=self._grant_requirements(),
                )
                if repaired:
                    _LOGGER.warning(
                        "Informed plan for target %s refused %s; filed the "
                        "template the refusal carried (%s) and re-filed once",
                        target_id,
                        error.rej,
                        ", ".join(repaired),
                    )
                    return self.api.submit_informed_plan(
                        target_id, proposal_id, submitted_plan,
                        f"{idempotency_key}-{_retry_tag(error.rej)}",
                    )
            if error.rej in (REJ_BLOCK_DECLARATION, REJ_HOLLOW_BLOCK):
                return self._block_refusal(target_id, error)
            raise

    def current_step(self, deal_id: str) -> dict[str, Any]:
        result = self.api.current_step(deal_id)
        step = result.get("current_step") or {}
        deal = result.get("deal") or {}
        thread = result.get("step_thread") or {}
        access = result.get("access") or {}
        material = access.get("material_change") or {}
        swap = access.get("equivalent_swap") or {}
        payload = {
            "ok": result.get("ok", True),
            "deal": {
                key: deal.get(key)
                for key in (
                    "id",
                    "proposal_id",
                    "target_goal_id",
                    "status",
                    "timeline_days",
                    "is_free",
                )
            },
            "current_step": {
                key: step.get(key)
                for key in (
                    "id",
                    "number",
                    "title",
                    "state",
                    "ask",
                    "outcome_promise",
                    "outcome_filed_at",
                    "declared_odds_at_bid",
                    "declared_odds_restated",
                    "declared_odds_drift",
                    "har_blocks",
                    "har_responses",
                    # RULE 230: what this step's SIGNED plan promised to hand
                    # back ({channel, family, types}), null when it promised
                    # nothing. A key the server adds and this whitelist drops
                    # does not exist -- that is exactly how person_sees_control
                    # went missing for weeks.
                    "deliverable",
                    # The file receipts already attached to this step. ALWAYS
                    # PRESENT, including the empty list: nothing delivered and
                    # no visibility must be tellable apart.
                    "file_receipts",
                )
            },
            # Open-ask visibility (server contract 2026-08-28): False while a
            # person-held ask is not yet open (the person sees NO control);
            # open_ask_move then spells out the one move that opens it. These
            # were stripped by this whitelist until v0.15.0 -- the reason the
            # server's hint never reached railed models.
            "person_sees_control": result.get("person_sees_control"),
            "open_ask_move": result.get("open_ask_move"),
            "pulse_cadence": result.get("pulse_cadence"),
            "latest_work_pulse": result.get("latest_work_pulse"),
            "step_thread": {
                "unread_from_person": thread.get("unread_from_person", 0),
                "unanswered_elsewhere": thread.get("unanswered_elsewhere") or [],
                "messages": thread.get("messages") or [],
                "post_reply": thread.get("post_reply"),
            },
            "released_materials": result.get("released_materials") or [],
            "released_materials_count": result.get("released_materials_count", 0),
            "access": {
                "grants": access.get("grants") or [],
                "your_homework": access.get("your_homework"),
                "equivalent_swap": {
                    key: swap.get(key) for key in ("endpoint", "when")
                },
                "material_change": {
                    key: material.get(key)
                    for key in ("endpoint", "consequence", "materiality_tests", "exceptions")
                },
            },
            "world_file_missing": result.get("world_file_missing", False),
            "world_file_url": result.get("world_file_url"),
            "tip_invited": result.get("tip_invited"),
            # r216 (server contract 2.26): the declared wait on the outside
            # world, null when there is none. This whitelist is why
            # person_sees_control never reached railed models until v0.15.0 --
            # a key the server adds and the harness drops does not exist.
            "waiting_outside": result.get("waiting_outside"),
            # The thing an email_reply wait is waiting FOR. It rides this call
            # and the check-in 201; dropping it here would make the agent poll
            # for the one payload it must act on.
            "inbound_replies": result.get("inbound_replies") or [],
            # r220 (server contract 2.30): the replies you OWE AN ANSWER. While
            # one stands the bench refuses your outcome, any act that is not
            # the answer, and a declared wait (reply_owed). Each entry carries
            # the exact propose_act body that pays it.
            "owed_replies": result.get("owed_replies") or [],
            # r220 second half: every act on this step and where it stands.
            # A sent_back act carries the person's own words in `note` and is
            # DEAD -- this whitelist is exactly why that reason never reached
            # a railed model and one idled for hours.
            "acts": result.get("acts") or [],
            # The acts this step's PLAN declared, each with the door, an
            # example body and the one move that is yours on it. A raw agent
            # spent a whole run guessing REST shapes for a door that rode this
            # payload; this whitelist dropped it until contract 2.44, which is
            # the same bug in the other direction.
            "declared_acts": result.get("declared_acts") or [],
            # contract 2.29: the same rows, email-only, in the older shape.
            "drafts_sent_back": result.get("drafts_sent_back") or [],
        }
        # RULE 230, always present including zero. The file receipts ride the
        # step where the server puts them; read both places rather than making
        # the model poll a route for the one payload it must act on.
        receipts = payload["current_step"].get("file_receipts")
        if not isinstance(receipts, list):
            receipts = result.get("file_receipts")
        payload["current_step"]["file_receipts"] = receipts if isinstance(receipts, list) else []
        known = self._step_receipts.get(str(step.get("id") or ""), [])
        if known and not payload["current_step"]["file_receipts"]:
            payload["current_step"]["file_receipts"] = list(known)
        if payload["current_step"].get("deliverable") is None:
            payload["current_step"]["deliverable"] = result.get("deliverable")
        self._remember_platform_blocks(step.get("id"), payload)
        self._remember_deliverable(step.get("id"), payload)
        self._fit_the_step(payload)
        return payload

    def _fit_the_step(self, payload: dict[str, Any]) -> None:
        """Cap the lists on a step that grow for the life of the deal.

        A step thread, the acts filed on a step and the materials released to
        a deal all get longer and never shorter, and every one of them is
        re-read into the conversation on every cycle. The newest rows are kept
        -- the server sends them oldest first -- and the TRUE COUNT is always
        published beside them, because "nothing there" and "not shown" must be
        tellable apart. `owed_replies` is never capped: it is the list of
        things the bench will refuse the next filing over.
        """
        thread = payload.get("step_thread")
        if isinstance(thread, dict):
            messages = thread.get("messages")
            if isinstance(messages, list):
                thread["messages_total"] = len(messages)
                thread["messages_omitted"] = max(
                    0, len(messages) - STEP_THREAD_MESSAGE_LIMIT
                )
                if thread["messages_omitted"]:
                    thread["messages"] = messages[-STEP_THREAD_MESSAGE_LIMIT:]
                    thread["messages_note"] = (
                        f"The newest {STEP_THREAD_MESSAGE_LIMIT} messages on this "
                        f"step; {thread['messages_omitted']} older ones are not "
                        "shown. Nothing is owed an answer that is not in "
                        "`unread_from_person` or `unanswered_elsewhere`."
                    )
        for key, cap in (
            ("acts", STEP_ACT_LIMIT),
            ("declared_acts", STEP_ACT_LIMIT),
            ("drafts_sent_back", STEP_ACT_LIMIT),
            ("released_materials", RELEASED_MATERIAL_LIMIT),
        ):
            rows = payload.get(key)
            if isinstance(rows, list) and len(rows) > cap:
                payload[f"{key}_total"] = len(rows)
                payload[key] = rows[-cap:]
                _LOGGER.info(
                    "current_step: %d of %d %s rows handed over", cap, len(rows), key
                )

    def _remember_deliverable(self, step_id: Any, payload: dict[str, Any]) -> None:
        """Record this step's signed promise and the files already on it.

        RULE 230: a step whose signed `deliverable.channel` is `file` cannot
        close until a receipt of the promised type is attached. Remembering it
        here is what lets `file_outcome` say so BEFORE the filing is spent --
        the server's `deliverable_missing` costs the person a round.
        """
        step_id = str(step_id or "")
        if not step_id:
            return
        step = payload.get("current_step") or {}
        deliverable = step.get("deliverable")
        if isinstance(deliverable, dict) and deliverable:
            self._step_deliverables[step_id] = deliverable
        server_receipts = [
            item for item in (step.get("file_receipts") or []) if isinstance(item, dict)
        ]
        if server_receipts:
            known = {
                str(item.get("receipt_id") or "")
                for item in self._step_receipts.get(step_id, [])
            }
            merged = list(self._step_receipts.get(step_id, []))
            merged.extend(
                item
                for item in server_receipts
                if str(item.get("receipt_id") or "") not in known
            )
            self._step_receipts[step_id] = merged

    def _remember_platform_blocks(
        self, step_id: Any, payload: dict[str, Any]
    ) -> None:
        """Record which declared blocks the platform is running on this step.

        RULE 229: on a block step the platform files the act when the step
        opens and files the outcome when the act executes. The harness has to
        know that without asking, or it files a duplicate act (409) and an
        outcome in words that are not its own. A declared kind is the
        platform's while an act of that kind is standing or already executed;
        a failed or denied one is the work coming back (rule 225) and the step
        is the agent's again.
        """
        step_id = str(step_id or "")
        if not step_id:
            return
        self._last_step_id = step_id
        live: dict[str, str] = {}
        for act in payload.get("acts") or []:
            if not isinstance(act, dict):
                continue
            kind = str(act.get("kind") or "").strip().lower()
            state = str(act.get("state") or "").strip().lower()
            if kind and state in LIVE_ACT_STATES:
                live[kind] = state
        declared = {
            str(entry.get("kind") or "").strip().lower()
            for entry in payload.get("declared_acts") or []
            if isinstance(entry, dict)
        }
        block_kinds = self._block_kinds()
        owned = {
            kind: state
            for kind, state in live.items()
            if kind in block_kinds and (not declared or kind in declared)
        }
        if owned:
            self._platform_blocks[step_id] = {"kinds": owned}
        else:
            self._platform_blocks.pop(step_id, None)

    def reply_step_message(
        self,
        deal_id: str,
        step_id: str,
        reply: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.api.post_step_message(deal_id, step_id, reply, idempotency_key)

    def post_check_in(
        self, deal_id: str, pulse: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        allowed = {"changed", "now", "next", "progress_percent", "blocker"}
        if set(pulse) - allowed:
            return {"ok": False, "error": "invalid_work_pulse_fields"}
        if pulse.get("progress_percent") not in {0, 25, 50, 75, 100}:
            return {"ok": False, "error": "invalid_work_pulse_progress"}
        try:
            result = self.api.post_check_in(deal_id, pulse, idempotency_key)
        except BookOfHousesApiError as error:
            if error.status == 422 and error.code == "ask_not_open":
                # The walk refused the pulse: this step's ask is person-held
                # and unopened, and the pulse reported no progress and no
                # blocker. The unblocking move is to file the outcome (or
                # pulse with real progress / an honest blocker).
                return {
                    "ok": False,
                    "error": "ask_not_open",
                    "move": error.message,
                }
            raise
        work_pulse = result.get("work_pulse") or {}
        thread = result.get("step_thread") or {}
        return {
            "ok": result.get("ok", True),
            "work_pulse": {
                key: work_pulse.get(key)
                for key in ("id", "step_id", "progress_percent", "posted_at", "next_due_at")
            },
            "pulse_cadence": result.get("pulse_cadence"),
            "unread_from_person": thread.get("unread_from_person", 0),
            "unanswered_elsewhere": thread.get("unanswered_elsewhere") or [],
            # r216: this check-in just ended any declared wait (the server ends
            # it, cause `agent`), so this reads null -- and the replies that
            # arrived while it stood ride the same 201.
            "waiting_outside": result.get("waiting_outside"),
            "inbound_replies": result.get("inbound_replies") or [],
            # r220: the debts and the acts ride the check-in 201 too, so an
            # agent that pulses and never reads current_step still learns it
            # owes somebody an answer and that a draft came back.
            "owed_replies": result.get("owed_replies") or [],
            "acts": result.get("acts") or [],
            "drafts_sent_back": result.get("drafts_sent_back") or [],
        }

    def wait_outside(
        self,
        deal_id: str,
        step_id: str,
        wait: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """WAIT (rule 216): waiting on the outside world is a state, not
        silence. You emailed someone off the platform and cannot go on until
        they answer -- say so. The person's card stops saying "agent working",
        and while the wait stands you take no check-in overdue marks and the
        deal cannot end out of time. Pass end=True to end it yourself."""
        allowed = {"on", "who", "what", "until", "end"}
        unexpected = sorted(set(wait) - allowed)
        if unexpected:
            return {"ok": False, "error": "invalid_wait_fields",
                    "unexpected_fields": unexpected}
        if wait.get("end"):
            return self.api.declare_outside_wait(
                deal_id, step_id, {"end": True}, idempotency_key)
        kind = str(wait.get("on") or "").strip().lower()
        if kind not in {"email_reply", "third_party", "provider"}:
            return {"ok": False, "error": "unknown_wait_kind",
                    "kinds": ["email_reply", "third_party", "provider"]}
        for field in ("who", "what"):
            if not str(wait.get(field) or "").strip():
                return {"ok": False, "error": "missing_wait_field", "field": field}
        payload = {"on": kind, "who": str(wait["who"])[:80],
                   "what": str(wait["what"])[:280]}
        if wait.get("until"):
            payload["until"] = str(wait["until"])
        return self.api.declare_outside_wait(
            deal_id, step_id, payload, idempotency_key)

    def propose_act(
        self, deal_id: str, step_id: str, act: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """ACT (rules 212 and 219): you propose, the platform executes. ONE
        door for every kind. kind 'email' -- the exact email on the step you
        are working, which the person approves word for word and Book of
        Houses sends from your platform mailbox. kind 'calendar_event' -- the
        exact event, on a step whose deal already holds a calendar grant,
        which the person approves and Book of Houses puts on their calendar."""
        allowed = {"kind", "to", "subject", "body_text", "purpose",
                   "in_reply_to",
                   "summary", "start", "end", "description", "location",
                   "attendees",
                   # rule 223: the meeting kind's intent fields
                   "with", "with_name", "duration_min", "window", "title",
                   "offer_count", "message"}
        # RULE 229: HANDS OFF A BLOCK THE PLATFORM IS RUNNING. It filed the
        # act itself when the step opened; a second copy is a duplicate the
        # bench refuses 409, and the person sees two Allow cards for one
        # meeting. An ANSWER to an owed reply is never that (rule 220), so it
        # goes through.
        owned = self._platform_owned(step_id)
        kind_asked = str(act.get("kind") or "email").strip().lower()
        if (
            owned
            and kind_asked in owned["kinds"]
            and not str(act.get("in_reply_to") or "").strip()
        ):
            return {
                "ok": False,
                "error": "platform_owned_block",
                "kind": kind_asked,
                "state": owned["kinds"][kind_asked],
                "message": (
                    f"This step's {kind_asked} block is the platform's to file "
                    "and to close (rule 229): it filed the act when the step "
                    "opened and it files the step's outcome when the act "
                    "executes. File nothing here. Watch current_step, answer "
                    "the person's messages, and file a changed act only after "
                    "a deny or a failure."
                ),
            }
        unexpected = sorted(set(act) - allowed)
        if unexpected:
            return {"ok": False, "error": "invalid_act_fields", "unexpected_fields": unexpected}
        kind = str(act.get("kind") or "email").strip().lower()
        if kind not in ("email", "calendar_event", "meeting"):
            return {"ok": False, "error": "unknown_act_kind",
                    "kinds": ["email", "calendar_event", "meeting"]}
        if kind == "meeting":
            # RULE 223: intent only. You say who, how long and roughly when;
            # the platform reads the person's calendar, offers the invitee the
            # times, books the pick and carries change and cancel. You never
            # touch a slot, a time or an email body.
            if not str(act.get("with") or "").strip():
                return {"ok": False, "error": "missing_act_field", "field": "with"}
            payload = {"kind": kind, "with": str(act["with"]).strip()}
            for field in ("with_name", "title", "description", "location"):
                if act.get(field):
                    payload[field] = str(act[field])
            if act.get("message"):
                payload["message"] = str(act["message"])[:4000]
            if act.get("window"):
                payload["window"] = (act["window"] if isinstance(act["window"], dict)
                                     else str(act["window"]))
            for field in ("duration_min", "offer_count"):
                if act.get(field) is not None:
                    payload[field] = act[field]
            if act.get("purpose"):
                payload["purpose"] = str(act["purpose"])[:120]
            return self.api.propose_act(deal_id, step_id, payload, idempotency_key)
        if kind == "calendar_event":
            for field in ("summary", "start", "end"):
                if not act.get(field):
                    return {"ok": False, "error": "missing_act_field", "field": field}
            payload: dict[str, Any] = {
                "kind": kind, "summary": str(act["summary"])[:400],
                "start": act["start"], "end": act["end"]}
            for field in ("description", "location"):
                if act.get(field):
                    payload[field] = str(act[field])
            if isinstance(act.get("attendees"), list) and act["attendees"]:
                payload["attendees"] = act["attendees"]
            if act.get("purpose"):
                payload["purpose"] = str(act["purpose"])[:120]
            return self.api.propose_act(deal_id, step_id, payload, idempotency_key)
        answering = str(act.get("in_reply_to") or "").strip()
        if answering:
            # RULE 220: an ANSWER. The bench fills the recipient and the
            # subject from the thread -- they are the thread's, not ours -- so
            # only the words are required here.
            if not str(act.get("body_text") or "").strip():
                return {"ok": False, "error": "missing_act_field",
                        "field": "body_text"}
            payload = {"kind": kind, "in_reply_to": answering,
                       "body_text": act["body_text"]}
            if act.get("purpose"):
                payload["purpose"] = str(act["purpose"])[:120]
            return self.api.propose_act(deal_id, step_id, payload,
                                        idempotency_key)
        for field in ("to", "subject", "body_text"):
            if not str(act.get(field) or "").strip():
                return {"ok": False, "error": "missing_act_field", "field": field}
        payload = {"kind": kind, "to": act["to"], "subject": act["subject"],
                   "body_text": act["body_text"]}
        if act.get("purpose"):
            payload["purpose"] = str(act["purpose"])[:120]
        return self.api.propose_act(deal_id, step_id, payload, idempotency_key)

    def dismiss_reply(
        self, deal_id: str, step_id: str, reply_id: str,
        dismissal: dict[str, Any], idempotency_key: str,
    ) -> dict[str, Any]:
        """RULE 220: a reply from an outside person is owed an answer, and
        until you give it the bench refuses everything else on that step
        (reply_owed). Answer it with propose_act carrying in_reply_to. Use
        THIS only for a message that is not a question -- spam, a bounce, an
        out-of-office -- and say why in ONE plain sentence: the person reads
        it on the step thread beside the reply."""
        allowed = {"reason"}
        unexpected = sorted(set(dismissal) - allowed)
        if unexpected:
            return {"ok": False, "error": "invalid_dismissal_fields",
                    "unexpected_fields": unexpected}
        reason = str(dismissal.get("reason") or "").strip()
        if not reason:
            return {"ok": False, "error": "missing_dismissal_field",
                    "field": "reason"}
        if not str(reply_id or "").strip():
            return {"ok": False, "error": "missing_reply_id"}
        return self.api.dismiss_reply(
            deal_id, step_id, str(reply_id).strip(), {"reason": reason[:280]},
            idempotency_key)

    def withdraw_act_declaration(
        self, deal_id: str, step_id: str, withdrawal: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """RULE 218: a step that declared an act does not close without it.
        If the act is no longer part of the step, take the declaration back
        here and say why in one plain sentence -- the person reads it on the
        step thread beside the plan that promised it."""
        allowed = {"kind", "reason"}
        unexpected = sorted(set(withdrawal) - allowed)
        if unexpected:
            return {"ok": False, "error": "invalid_withdrawal_fields",
                    "unexpected_fields": unexpected}
        kind = str(withdrawal.get("kind") or "email").strip().lower()
        if kind != "email":
            return {"ok": False, "error": "unknown_act_kind", "kinds": ["email"]}
        reason = str(withdrawal.get("reason") or "").strip()
        if not reason:
            return {"ok": False, "error": "missing_withdrawal_field",
                    "field": "reason"}
        return self.api.withdraw_act_declaration(
            deal_id, step_id, {"kind": kind, "reason": reason[:280]},
            idempotency_key)

    def file_outcome(
        self, target_id: str, outcome: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        # RULE 229: the platform files a block step's outcome, from the
        # receipt's own words, and that ledger row reads actor: platform. An
        # agent-written outcome on the same step is a second telling of the
        # platform's story, and the person approves whichever landed first.
        step_ref = str(outcome.get("step_ref") or "").strip() or self._last_step_id
        owned = self._platform_owned(step_ref) if step_ref else None
        if owned:
            return {
                "ok": False,
                "error": "platform_owned_block",
                "kinds": sorted(owned["kinds"]),
                "message": (
                    f"The {', '.join(sorted(owned['kinds']))} block on this "
                    "step files its own outcome when the act executes (rule "
                    "229), and the person's APPROVE opens on the platform's "
                    "receipt words. Do not file one. If the act failed or was "
                    "denied, the step is yours again: file a changed act."
                ),
            }
        # RULE 230 (2026-09-05): `file_url` is the agent-hosted lane. The
        # platform fetches the link ONCE, sniffs the bytes, hashes them and
        # drops them; `claim_url` rides along for a here.now page the person
        # keeps within the day. `filename` names the file on the card.
        allowed = {"note", "text", "document", "step_ref", "file_url", "claim_url", "filename"}
        unexpected = sorted(set(outcome) - allowed)
        if unexpected:
            return {
                "ok": False,
                "error": "invalid_outcome_fields",
                "unexpected_fields": unexpected,
            }
        note = str(outcome.get("note") or "").strip()
        if not note or len(note) > 280:
            return {"ok": False, "error": "invalid_delivery_note"}
        content_fields = [
            name for name in ("text", "document", "file_url") if outcome.get(name)
        ]
        if len(content_fields) != 1:
            return {
                "ok": False,
                "error": "exactly_one_outcome_content_required",
                "allowed": ["text", "document", "file_url"],
            }
        if outcome.get("claim_url") and not outcome.get("file_url"):
            return {
                "ok": False,
                "error": "claim_url_without_file_url",
                "message": (
                    "claim_url is the person's link to KEEP a hosted file, so it "
                    "only rides a file_url delivery."
                ),
            }
        # RULE 230: A STEP THAT PROMISED A FILE DOES NOT CLOSE ON WORDS. What
        # forced it: three `document` outcomes on production listed
        # "stan_animation.mp4" in their text sections and no file was ever
        # uploaded. Said here, before the filing is spent, because the
        # server's `deliverable_missing` costs the person a review round.
        blocked = self._file_step_owes_a_file(step_ref, outcome)
        if blocked:
            return blocked
        # RULE 233: A STEP THAT PROMISED CARDS DOES NOT CLOSE ON HEADINGS.
        # What forced it: a document whose blocks were four field names as
        # headings with nothing under them, filed twice on production.
        blocked = self._text_step_owes_cards(step_ref, outcome)
        if blocked:
            return blocked
        try:
            return self.api.file_outcome(target_id, outcome, idempotency_key)
        except BookOfHousesApiError as error:
            if error.code not in FILE_DOOR_REFUSALS:
                raise
            # VERBATIM. The platform is the scanner; its sentence is the one
            # that tells the model what to do next -- and so are its `field`,
            # `reason` and `fix`, which ride at the top level where a model
            # reading a tool result will actually see them.
            refusal: dict[str, Any] = {
                "ok": False,
                "error": error.code,
                "status": error.status,
                "message": error.message,
                "terminal": error.code in FILE_DOOR_TERMINAL,
            }
            body = error.body if isinstance(error.body, dict) else {}
            for key in FILE_DOOR_BODY_KEYS:
                if body.get(key) is not None and key not in refusal:
                    refusal[key] = body[key]
            refusal.setdefault("detail", error.body)
            return refusal

    def _file_step_owes_a_file(
        self, step_ref: str | None, outcome: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The local half of rule 230: a promised file, and nothing attached.

        Only fires when the step's SIGNED deliverable says `channel: file` and
        this filing carries no `file_url` and no receipt is known on the step.
        A step whose promise we never read is left to the door: a mirror must
        never bury a delivery the platform would take.
        """
        if outcome.get("file_url"):
            return None
        step_id = str(step_ref or "").strip()
        if not step_id:
            return None
        deliverable = self._step_deliverables.get(step_id)
        if not blocks.promised_file_types(deliverable) and not (
            isinstance(deliverable, dict)
            and str(deliverable.get("channel") or "").strip().lower() == "file"
        ):
            return None
        if self._step_receipts.get(step_id):
            return None
        warnings = self._deliverable_warnings.get(step_id, 0)
        if warnings >= MAX_DELIVERABLE_WARNINGS:
            return None
        self._deliverable_warnings[step_id] = warnings + 1
        promised = blocks.promise_words(deliverable)
        return {
            "ok": False,
            "error": "deliverable_missing",
            "deliverable": deliverable,
            "terminal": False,
            "message": (
                f"This step promised {promised}; nothing attached. A text "
                "section that lists a filename closes nothing. Deliver the "
                "bytes first -- toll_bench.deliver_file for a file in this "
                "run's folder, or toll_bench.deliver_hosted_file for a link "
                "the platform fetches once -- then file this outcome. If you "
                "cannot make that kind of file, say so on the step thread "
                "rather than filing words in its place."
            ),
        }

    def _text_step_owes_cards(
        self, step_ref: str | None, outcome: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The local half of rule 233: a promised shape, and the boxes empty.

        Only fires when the step's SIGNED deliverable is `text` and names
        `fields`. A blank box on a card in THIS document is refused every
        time -- the bench refuses it by name whatever else is on the step. A
        document with no cards, or too few, is named ONCE per step and then
        left to the door: an earlier receipt on the same step may already
        carry the cards, `current-step` does not publish receipts, and a
        mirror must never bury a filing the platform would take.
        """
        step_id = str(step_ref or "").strip()
        if not step_id:
            return None
        deliverable = self._step_deliverables.get(step_id)
        shortfall = blocks.cards_shortfall(deliverable, outcome)
        if shortfall is None:
            return None
        if not shortfall.get("certain"):
            warnings = self._shape_warnings.get(step_id, 0)
            if warnings >= MAX_DELIVERABLE_WARNINGS:
                return None
            self._shape_warnings[step_id] = warnings + 1
        result = {key: value for key, value in shortfall.items() if key != "certain"}
        result["message"] = (
            result["message"]
            + " A heading with nothing under it hands back nothing: the "
            "platform reads no word of the work, it counts empty boxes. "
            "File the outcome again with a cards block, one item per thing "
            "and every named field filled (the exact shape is in how). If "
            "you cannot fill them, say so on the step thread rather than "
            "filing a shell."
        )
        result.update({"ok": False, "deliverable": deliverable, "terminal": False})
        return result

    # ------------------------------------------------------------------
    # RULE 230, THE TWO DELIVERY DOORS.
    # ------------------------------------------------------------------

    def deliver_file(
        self,
        deal_id: str,
        *,
        filename: str,
        content: bytes,
        title: str,
        step_ref: str | None = None,
    ) -> dict[str, Any]:
        """Hand a file from this run's folder to the platform (the D1 lane).

        The bytes go up as multipart to the deal's artifact route. The
        platform sniffs them, hashes them and attaches a file receipt to the
        step that is agent-working. It does NOT hand the ball over: only the
        filed OUTCOME does that (one-ball law).
        """
        name = str(filename or "").strip() or "delivery"
        title_words = str(title or "").strip()
        if not title_words:
            return {
                "ok": False,
                "error": "missing_title",
                "message": (
                    "title is the item's real name as the person's card wears "
                    "it, for example 'Stan animation'. 80 characters at most."
                ),
            }
        if len(title_words) > 80:
            return {
                "ok": False,
                "error": "title_too_long",
                "message": "title is at most 80 characters as the card wears it.",
            }
        if not content:
            return {
                "ok": False,
                "error": "empty_file",
                "message": f"{name} is empty. There is nothing to deliver.",
            }
        if len(content) > ARTIFACT_MAX_BYTES:
            megabytes = len(content) / (1024 * 1024)
            return {
                "ok": False,
                "error": "file_too_large",
                "size_bytes": len(content),
                "limit_bytes": ARTIFACT_MAX_BYTES,
                "message": (
                    f"{name} is {megabytes:.1f} MB and the platform lane takes "
                    "50 MB per file (100 MB per want, shared with what the "
                    "person uploaded). Host it yourself and hand back the link "
                    "with toll_bench.deliver_hosted_file, which the platform "
                    "fetches once and checks the same way."
                ),
            }
        found = sniffer.sniff(content[: sniffer.SNIFF_BYTES], filename=name)
        step_id = str(step_ref or "").strip() or self._last_step_id or ""
        digest = hashlib.sha256(content).hexdigest()
        idempotency_key = f"artifact-{step_id or 'step'}-{digest[:32]}"
        try:
            response = self.api.upload_deal_artifact(
                deal_id,
                filename=name,
                content=content,
                media_type=str(found.get("media_type") or sniffer.UNKNOWN_MEDIA_TYPE),
                title=title_words,
                step_ref=step_id or None,
                idempotency_key=idempotency_key,
            )
        except BookOfHousesApiError as error:
            if error.code not in FILE_DOOR_REFUSALS:
                raise
            return {
                "ok": False,
                "error": error.code,
                "status": error.status,
                "message": error.message,
                "detail": error.body,
                "terminal": False,
                "sniffed": found,
            }
        receipt = {
            "receipt_id": response.get("receipt_id"),
            "sha256": response.get("sha256") or digest,
            "size_bytes": response.get("size_bytes", len(content)),
            "filename": response.get("filename") or name,
            "filed_at": response.get("filed_at"),
            # The platform's own reading of the bytes, beside the harness's.
            "sniffed_type": response.get("sniffed_type"),
            "family": response.get("family"),
        }
        if step_id:
            self._step_receipts.setdefault(step_id, []).append(receipt)
        return {
            "ok": True,
            **receipt,
            "sniffed": found,
            "step_ref": step_id or None,
            "message": (
                "The file is on the person's card for this step. It does not "
                "hand the ball over: file this step's outcome when the work is "
                "done."
            ),
        }

    def deliver_hosted_file(
        self, target_id: str, delivery: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """Hand back a file you host yourself, through the platform's scanner.

        The platform fetches `file_url` once, sniffs the bytes against what
        the step promised, records size and fingerprint, and drops the bytes.
        The person's download streams from your address through the platform,
        re-checking the fingerprint on the way, so a swapped or deleted file
        fails with an honest message instead of serving junk. `claim_url` is
        the here.now keep-it link, which is the person's job within the day.
        """
        allowed = {"note", "file_url", "claim_url", "filename", "step_ref"}
        unexpected = sorted(set(delivery) - allowed)
        if unexpected:
            return {
                "ok": False,
                "error": "invalid_outcome_fields",
                "unexpected_fields": unexpected,
                "allowed_fields": sorted(allowed),
            }
        if not str(delivery.get("file_url") or "").strip():
            return {
                "ok": False,
                "error": "missing_file_url",
                "message": (
                    "file_url is the live address the platform fetches the "
                    "file from, once."
                ),
            }
        outcome = {key: value for key, value in delivery.items() if value}
        return self.file_outcome(target_id, outcome, idempotency_key)

    # ------------------------------------------------------------------
    # THE OUTSIDE ACT (Steven, 2026-09-05) -- THE EVIDENCE DOOR.
    # The platform executes what it has hands for: an email, a meeting, a
    # post, a record, a calendar event. Everything else -- a phone call, a
    # purchase, a visit, a form on somebody else's site -- is ONE generic
    # block. The agent declares at bid time what it will do itself, in its own
    # name, with its own tools; the person taps Allow; the agent goes and does
    # it; then it files the evidence here, and the platform closes the step
    # (rule 229) and asks the witness the declaration named. One door, one
    # filing, no outcome of the agent's own.
    # ------------------------------------------------------------------

    def file_evidence(
        self,
        deal_id: str,
        step_id: str,
        *,
        summary: str,
        links: list[str] | None = None,
        receipt_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """File what you actually did on an approved `outside` act.

        Checked here before a call is spent on it: the summary the person
        reads, at most five http(s) links, at most five ids of file receipts
        already delivered on this deal. Everything else is the door's to say
        -- whether this step declared an outside block, whether the person has
        tapped Allow, whether it is already done -- and its sentence comes
        back verbatim.
        """
        words = str(summary or "").strip()
        if len(words) < EVIDENCE_SUMMARY_MIN or len(words) > EVIDENCE_SUMMARY_MAX:
            return {
                "ok": False,
                "error": "invalid_evidence",
                "field": "summary",
                "message": (
                    "summary is what you did, in your own plain words, "
                    f"{EVIDENCE_SUMMARY_MIN} to {EVIDENCE_SUMMARY_MAX} "
                    "characters: who you dealt with, what happened, and how "
                    "it ended. The person reads this and nothing else."
                ),
            }
        given_links = list(links or [])
        if len(given_links) > EVIDENCE_MAX_LINKS:
            return {
                "ok": False,
                "error": "invalid_evidence",
                "field": "links",
                "message": (
                    f"{len(given_links)} links; at most "
                    f"{EVIDENCE_MAX_LINKS} ride the evidence. Keep the ones "
                    "that show the thing happened."
                ),
            }
        checked_links: list[str] = []
        for item in given_links:
            address = str(item or "").strip()
            if not address.lower().startswith(("http://", "https://")):
                return {
                    "ok": False,
                    "error": "invalid_evidence",
                    "field": "links",
                    "message": (
                        f"{address or 'an empty link'} is not a web address. "
                        "Every link is a full http:// or https:// URL the "
                        "person can open."
                    ),
                }
            checked_links.append(address)
        given_receipts = list(receipt_ids or [])
        if len(given_receipts) > EVIDENCE_MAX_RECEIPTS:
            return {
                "ok": False,
                "error": "invalid_evidence",
                "field": "receipt_ids",
                "message": (
                    f"{len(given_receipts)} receipt ids; at most "
                    f"{EVIDENCE_MAX_RECEIPTS} ride the evidence."
                ),
            }
        checked_receipts: list[str] = []
        for item in given_receipts:
            receipt = str(item or "").strip()
            if not receipt:
                return {
                    "ok": False,
                    "error": "invalid_evidence",
                    "field": "receipt_ids",
                    "message": (
                        "A receipt id is the `receipt_id` a "
                        "toll_bench.deliver_file on this deal answered with. "
                        "An empty one names nothing."
                    ),
                }
            checked_receipts.append(receipt)
        payload: dict[str, Any] = {"summary": words}
        if checked_links:
            payload["links"] = checked_links
        if checked_receipts:
            payload["receipt_ids"] = checked_receipts
        step = str(step_id or "").strip()
        digest = hashlib.sha256(words.encode("utf-8")).hexdigest()[:32]
        idempotency_key = f"evidence-{step or 'step'}-{digest}"
        try:
            return self.api.file_evidence(deal_id, step, payload, idempotency_key)
        except BookOfHousesApiError as error:
            if error.code not in EVIDENCE_DOOR_REFUSALS:
                raise
            return {
                "ok": False,
                "error": error.code,
                "status": error.status,
                "message": error.message,
                "detail": error.body,
                "terminal": False,
            }
