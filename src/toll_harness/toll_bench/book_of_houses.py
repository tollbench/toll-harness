from __future__ import annotations

import logging
import time
from typing import Any

from toll_harness.email.book_of_houses import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.fleet import FleetStore
from toll_harness.toll_bench import blocks

_LOGGER = logging.getLogger("toll_harness.toll_bench")

# The refusal codes the block door speaks (contract 2.44, rules 228 and 229).
REJ_REQUIRED_BLOCK = "REJ-32"
REJ_BLOCK_DECLARATION = "REJ-33"
REJ_HOLLOW_BLOCK = "REJ-34"
# RULE 230 (contract 2.46): a block whose account no GRANT step in the plan
# opens. Like REJ-32 it CARRIES THE FORM, so it is repaired and filed once.
REJ_BLOCK_GRANT = blocks.REJ_BLOCK_GRANT
REJ_CARRIES_THE_FORM = (REJ_REQUIRED_BLOCK, REJ_BLOCK_GRANT)


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
FINALIST_QUESTIONS_REQUIRED = 4

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
        return self.api.target_brief(target_id)

    def list_act_kinds(self) -> dict[str, Any]:
        """The act registry (contract 2.44). Each kind publishes `wanted_when`
        (which wants need it), `declaration` (the bid-time fields, no context
        needed) and `template` (the step, ready to file). Read it before
        declaring a block: the fields are the kind's, not the harness's."""
        if self._act_kinds is None:
            self._act_kinds = self.api.act_kinds()
        return self._act_kinds

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
        the door will refuse. When there is no template to insert -- an older
        server, or a brief that has closed behind a selection -- refusing at
        home would only bury the plan the person is waiting on. File it, and
        let the refusal carry the form.
        """
        if validation.get("ok"):
            return validation
        template = plan_template if isinstance(plan_template, list) else []
        if any(isinstance(step, dict) and blocks.grant_provider(step) for step in template):
            return validation
        remaining = [
            problem
            for problem in validation.get("problems") or []
            if problem.get("rej") != REJ_BLOCK_GRANT
        ]
        if remaining:
            return {**validation, "problems": remaining}
        return {**validation, "ok": True, "problems": []}

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

    def list_proposals(self) -> dict[str, Any]:
        return {"ok": True, "proposals": self.api.proposals()}

    def validate_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
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
                "message": error.message,
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
        # RULE 230 / REJ-35 (contract 2.46): a meeting block reads and writes
        # the person's calendar, so a GRANT step connecting it comes FIRST.
        # Steven, 2026-09-05: "they are supposed to connect my calendar IN the
        # plan." The repair inserts it; this is the mirror that names it.
        problems.extend(blocks.grant_problems(steps))
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

    def submit_proposal(
        self, target_id: str, proposal: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        # RULE 228 (contract 2.44). The brief NAMES the blocks this want cannot
        # be delivered without, and publishes the step to file for each one. A
        # plan missing one is REJ-32 -- and a refused filing on a one-bid-per
        # -target board is the whole round. So the form is filled here, from
        # the model's own plan, BEFORE the door sees it. What forced it: three
        # agents bid a meeting want, none declared a meeting act, and none of
        # them got a meeting booked.
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
        proposal, inserted = blocks.merge_required_blocks(
            proposal,
            brief.get("required_blocks"),
            brief.get("plan_template"),
            want=brief.get("want"),
        )
        if inserted:
            _LOGGER.warning(
                "Plan for target %s did not carry the brief's form; filled it "
                "in and filed that (%s)",
                target_id,
                ", ".join(inserted),
            )
        validation = self._grant_gap_never_blocks_the_filing(
            self.validate_proposal(proposal), brief.get("plan_template")
        )
        if not validation["ok"]:
            return {"ok": False, "error": "local_validation_failed", **validation}
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
                if first.rej not in REJ_CARRIES_THE_FORM or not first.plan_template:
                    raise
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
                    target_id, proposal, f"{idempotency_key}-{_retry_tag(first.rej)}"
                )
        except BookOfHousesApiError as error:
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
        return result

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
        proposals = self.list_proposals().get("proposals") or []
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
        )
        if inserted:
            _LOGGER.warning(
                "Informed plan for target %s did not carry the brief's form; "
                "filled it in and filed that (%s)",
                target_id,
                ", ".join(inserted),
            )
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
        validation = self._grant_gap_never_blocks_the_filing(
            self.validate_proposal(candidate), brief.get("plan_template")
        )
        if not validation["ok"]:
            return {
                "ok": False,
                "error": "informed_plan_validation_failed",
                "problems": validation["problems"],
                "sealed_terms": {
                    "total_ask_cents": original.get("total_ask_cents"),
                    "timeline_days": original.get("timeline_days"),
                    "allocation": original.get("allocation"),
                },
            }
        try:
            return self.api.submit_informed_plan(
                target_id, proposal_id, submitted_plan, idempotency_key
            )
        except BookOfHousesApiError as error:
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
        self._remember_platform_blocks(step.get("id"), payload)
        return payload

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
        allowed = {"note", "text", "document", "step_ref"}
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
        content_fields = [name for name in ("text", "document") if outcome.get(name)]
        if len(content_fields) != 1:
            return {
                "ok": False,
                "error": "exactly_one_outcome_content_required",
                "allowed": ["text", "document"],
            }
        return self.api.file_outcome(target_id, outcome, idempotency_key)
