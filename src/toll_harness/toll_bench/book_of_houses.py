from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

from toll_harness.fleet import FleetStore
from toll_harness.toll_bench import blocks, doors, draft
from toll_harness.toll_bench.api import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.toll_bench.step import (
    LAST_FILING_KEY,
)
from toll_harness.tools import sniff as sniffer

_LOGGER = logging.getLogger("toll_harness.toll_bench")

# The block door's two refusals a filing can correct once (rule 229).
REJ_BLOCK_DECLARATION = "REJ-33"
REJ_HOLLOW_BLOCK = "REJ-34"

# CONTRACT 3.0 (2026-09-05): the free validate door, call 3 of six. It runs the
# WHOLE bid door -- the shared validator plus the bid-only checks (REJ-14,
# REJ-15, REJ-31) -- and answers with every problem at once, each carrying a
# plain-words `fix`. It writes nothing: no row, no refusal, no idempotency key,
# and open_bid_count does not move. A bench that reports a contract below this
# has no such route, and its filing door is the only judge.
VALIDATE_DOOR_MIN_CONTRACT_MAJOR = 3

# THE PLAN DOOR'S REPLY IS CONTRACT 4.0 (2026-09-17). Steven: "The form says
# what goes in. It never lists what is wrong." The bench REPLACED that reply
# rather than extending it -- the old problem rows are gone, `problems` is one
# row shape and `form_steps` is new -- and this harness reads the new one and
# only the new one, on purpose: two languages side by side is what made one
# fault cost three tries. So a bench below 4.0 cannot be planned against, and
# the run says so out loud instead of quietly filing nothing.
#
# The BID door is untouched by 4.0 and still answers the way it always has, so
# nothing below gates a proposal on this number.
PLAN_DOOR_MIN_CONTRACT = "4.0"
PLAN_DOOR_MIN_CONTRACT_MAJOR = 4


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
        # RULE 238 AMENDED (2026-09-12) / REJ-45: the plan reaches a person and
        # no who step stands above it. A 4xx the agent can act on -- the door
        # names the step to add -- so it comes back as a result with `field`,
        # `reason` and `fix` on it, and the brake counts it like any other.
        "who_step_missing",
        # RULE 233 (2026-09-05): the shape door. The bench counts the empty
        # boxes on the cards and its sentence names the card and the field.
        "deliverable_fields_missing",
        "deliverable_fields_blank",
        "deliverable_count_short",
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

# 0.74.0: THE BRIEF IS HANDED BACK WHOLE. The 60,000-character brief budget
# and its shed order (0.34.0-0.73.0) were set against a 131,072-token window;
# on Nia's claude-opus-5-5 they took the block menu and the bid template out
# of a 64k brief. A rail that manages its own context (Claude Code, Codex)
# gets the brief as the bench wrote it; a raw adapter has its run budget.
# THE DOOR LOOP. Three answers is a compile, four is a loop: on 2026-09-09 a
# run called the validate door fourteen times in three minutes, never filed,
# and each answer re-entered the conversation whole.
MAX_VALIDATE_ATTEMPTS = 3
# 0.64.0: the two lists the outcome 201 carries about the cards it took off a
# list a who step reads (contract 4.1.12; `finds_taken` 2026-10-10). Always
# present on the bench's answer, `[]` when nothing came off.
FINDS_KEYS: tuple[str, ...] = ("finds_refused", "finds_taken")
# They ride the next current-step read of that step under LAST_FILING_KEY
# (step.py's name, so the tail and the payload say the same word), with one
# sentence, so the old road reads them the way the step ask does.
FINDS_MEMORY = 256
LAST_FILING_NOTE = (
    "The outcome door took these cards off your last filing on this step "
    "(finds_refused: a made-up or reserved address; finds_taken: a person "
    "another of this person's wants already has). The rest stood. Replace "
    "them with real finds; never file them again."
)

# THE QUESTIONS ARE THE DOOR'S TO COUNT, NOT THIS PACKAGE'S (2026-09-17).
# WHAT FORCED IT: Kai's round-2 proposal on prod was blocked by a
# contradiction inside this harness. `draft.py` read the server's rule of
# 2026-09-11 -- ask UP TO THREE questions, and none at all is a fine answer
# (rule 243, contract 3.16) -- while a mirror here still held the August
# rule, "exactly one array of four questions, at most two of them text boxes"
# (contract 2.37, rules 168 and 170). Obeying both is impossible, so Kai filed
# nothing. THE HARNESS NEVER RE-IMPLEMENTS A SERVER RULE. A count, a format
# and a cap belong to the bench; the free validate door
# (POST /api/bench/targets/{id}/proposals/validate) is the only judge of a
# proposal's shape, and its answer beats anything said here. Since 0.74.0
# nothing is checked at home at all.

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
        # RULE 238 CORRECTED (2026-09-11), AMENDED (2026-09-12). A STEP
        # format, and never a question: who it goes to is a step of the plan,
        # the AGENT puts it in, the bench writes the person's own contact book
        # onto it (or the list its `from_step` names: an earlier step's, or
        # the finds of the research step "found_by_research" stamps, 0.64.0),
        # and each pick arrives as a reference for
        # `acts[].contact_ref`. It stays a legal HAR slug here
        # because the step carries it; the bid door refuses it on a PROPOSAL
        # in its own words.
        "contact_picker",
    }
)

# A problem the door raises for a field that is simply NOT THERE. Filing over
# one is a round spent on a certainty: the filing door runs the same pipeline.
_MISSING_FIELD_CODES = frozenset({"REJ-31"})



def _door_rows(problems: Any) -> list[dict[str, Any]]:
    """The door's own rows, whole (0.74.0: nothing capped or cut here)."""
    if not isinstance(problems, list):
        return []
    return [row for row in problems if isinstance(row, dict)]


def _withdraw_key(proposal_id: Any, body: dict[str, Any]) -> str:
    """A stable Idempotency-Key for one withdrawal.

    Derived from the bid and the words, so the SAME withdrawal sent twice is
    one withdrawal and a different one is not mistaken for a replay. The
    bench's withdraw door refuses a call with no key (400
    `idempotency_key_required`), and a write with no key is a write nobody can
    retry safely.
    """
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"withdraw-{str(proposal_id or 'none')[:64]}-{digest}"


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
        # Accepted and unused since 0.74.0: the per-want cap is gone.
        self.fleet_proposal_limit = fleet_proposal_limit
        # Optional market-scan crowding limit; None disables the check.
        self.open_bid_limit = open_bid_limit
        # Once reachable, stay confirmed for a window instead of re-fetching
        # /me every watch cycle (2026-08-27: the 7-agent fleet alone was
        # ~2,400 /me calls an hour). A fresh reachability ping waits at most
        # this long for its ack.
        self._reachable_cached: dict[str, Any] | None = None
        self._reachable_until = 0.0
        # Contract 2.44 state. The act registry is fetched once per process,
        # and the refusal counter is what keeps ONE correction from becoming
        # a loop (a harness filed and withdrew about a hundred times in 90
        # minutes on 2026-09-04). Whose move a step is, is the bench's `turn`
        # (0.74.0: no rule-229 block memo here any more).
        self._act_kinds: dict[str, Any] | None = None
        # 0.74.0: the bench's published doors, read from its contract once
        # per process and again when a call names a door not on it.
        self._contract: dict[str, Any] | None = None
        self._doors: list[dict[str, Any]] | None = None
        self._last_step_id: str | None = None
        # The person's unanswered message ids per step, oldest first, as the
        # last current-step read (or reply 200) listed them. A reply that names
        # nothing is filled from here with the latest (0.56.1).
        self._unanswered: dict[str, list[str]] = {}
        self._block_refusals: dict[str, int] = {}
        # Contract 3.0: probed once per provider, then remembered. None means
        # "not asked yet"; False means this bench publishes no validate door.
        self._validate_door: bool | None = None
        # 0.34.0. The validate door's attempt count per want, and the last
        # problems it named there -- so the fourth call answers honestly
        # instead of spending another round trip on the same refusal.
        self._validate_attempts: dict[str, int] = {}
        self._last_door_problems: dict[str, dict[str, Any]] = {}
        # 0.64.0. The cards the outcome door took off a filing on a step whose
        # list a who step's book reads (`finds_refused`, `finds_taken` on the
        # 201, contract 4.1.12), by step id, so the next ask on that step can
        # replace them rather than file them again.
        self._last_filing_finds: dict[str, dict[str, list[Any]]] = {}
        # 0.63.0. What the agent spent on the current deal, set by whoever
        # builds the runtime. None sends no `agent_cost`, today's behavior.
        self.cost_ledger: Any | None = None
        # 0.64.1. STEVEN'S RULING (2026-10-19 ~17:45 PDT): guide() hands back
        # the WHOLE skill page, never a slice. The nine hard-coded heading
        # strings it used to cut on (one pair per topic) went stale when the
        # page was rewritten in September 2026, and every topic raised
        # "Current production guide is missing the <topic> section" --
        # staging and prod both. The page is the contract; this harness only
        # carries it, so nothing here can go stale again. The last
        # successfully fetched copy is kept so a transient fetch failure
        # still answers with something, rather than nothing.
        self._guide_cache: str | None = None

    def protocol(self) -> dict[str, Any]:
        return self.api.protocol()

    # The topic stays a named enum so old callers (and the tool schema) keep
    # working; it is echoed back on the answer and otherwise unused -- every
    # topic gets the same whole page.
    GUIDE_TOPICS: frozenset[str] = frozenset(
        {"start", "attention", "bidding", "finalist", "delivery"}
    )

    def guide(self, topic: str) -> dict[str, Any]:
        if topic not in self.GUIDE_TOPICS:
            raise ValueError(f"Unknown guide topic: {topic}")
        try:
            document = self.api.skill()
        except BookOfHousesApiError:
            if self._guide_cache is None:
                return {
                    "topic": topic,
                    "error": "guide_unavailable",
                    "instructions": (
                        "The live skill page could not be fetched and no "
                        "cached copy is available yet. Try again shortly."
                    ),
                }
            document = self._guide_cache
        else:
            self._guide_cache = document
        protocol = self.protocol()
        return {
            "topic": topic,
            "contract_version": protocol.get("contract_version"),
            "rules_version_hash": protocol.get("rules_version_hash"),
            "instructions": document.strip(),
        }

    def proposal_schema(self) -> dict[str, Any]:
        return self.api.proposal_schema()

    def capability_taxonomy(self) -> dict[str, Any]:
        return self.api.capability_taxonomy()

    def status(self) -> dict[str, Any]:
        return self.api.me()

    def payout_sync(self) -> dict[str, Any]:
        """Re-read Stripe, then the plain-words payout status (0.64.0).

        `ready` is true when paid work is possible (a verified operator and a
        ready payout account); `still_needed` says what is missing in plain
        words; `operator_registration_url` is the link for the operator. A
        bench without the status door (before 2026-10-12) answers 404 there,
        and the sync body is returned as it is, so nothing older breaks.
        """
        synced = self.api.payout_sync(f"toll-harness-payout-sync-{int(time.time())}")
        try:
            return self.api.payout_status()
        except BookOfHousesApiError as error:
            if error.status != 404:
                raise
        payout = synced.get("payout") if isinstance(synced, dict) else None
        payout = payout if isinstance(payout, dict) else {}
        return {
            "ok": True,
            "ready": payout.get("readiness") == "ready",
            "readiness": payout.get("readiness"),
            "requirements": payout.get("requirements") or [],
            "still_needed": payout.get("requirements") or [],
        }

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

    def goal_check(self, run_id: str) -> dict[str, Any]:
        return self.api.goal_check(run_id)

    def claim_goal_check(self, run_id: str) -> dict[str, Any]:
        return self.api.claim_goal_check(run_id)

    def submit_goal_check(
        self, run_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        return self.api.submit_goal_check(run_id, payload, idempotency_key)

    def events(self, *, after: str | None = None, wait: int = 0) -> dict[str, Any]:
        return self.api.events(after=after, wait=wait)

    def list_targets(self) -> dict[str, Any]:
        return self.api.open_targets()

    def operator_reply(self, target_id: str, message_id: str, reply: str,
                       revise_plan: bool) -> dict[str, Any]:
        return self.api.operator_reply(target_id, message_id, reply, revise_plan)

    def read_brief(self, target_id: str) -> dict[str, Any]:
        """The brief as the bench wrote it, whole (0.74.0: nothing is shed)."""
        return self._read_brief(target_id)

    def read_whole_brief(self, target_id: str) -> dict[str, Any]:
        """The same brief (0.73.0); kept for callers that name it."""
        return self._read_brief(target_id)

    def _read_brief(self, target_id: str) -> dict[str, Any]:
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
        return response

    def get_pack_input(self, target_id: str, receipt_id: str) -> dict[str, Any]:
        """Return one server-authorized input on a direct action-pack edge."""
        return self.api.get_pack_input(target_id, receipt_id)

    def get_pack_input_file(self, target_id: str, receipt_id: str) -> bytes:
        """Fetch file bytes through the same server-authorized edge."""
        return self.api.get_pack_input_file(target_id, receipt_id)

    def get_pack_drafts(self, source_goal_id: str) -> dict[str, Any]:
        return self.api.get_pack_drafts(source_goal_id)

    def suggest_pack_draft(
        self, source_goal_id: str, slot_id: str, draft: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.api.suggest_pack_draft(source_goal_id, slot_id, draft, idempotency_key)

    def paid_pack_job(self, order_id: str) -> dict[str, Any]:
        return self.api.paid_pack_job(order_id)

    def read_paid_pack_input(
        self, order_id: str, index: int, offset: int = 0,
    ) -> dict[str, Any]:
        return self.api.read_paid_pack_input(order_id, index, offset)

    def submit_paid_pack_result(
        self, order_id: str, *, card_number: int, submit_key: str,
        title: str, note: str = "", content: bytes | None = None,
        filename: str | None = None, content_type: str | None = None,
    ) -> dict[str, Any]:
        return self.api.submit_paid_pack_result(
            order_id, card_number=card_number, submit_key=submit_key,
            title=title, note=note, content=content, filename=filename,
            content_type=content_type,
        )

    def paid_pack_receipt(self, order_id: str, receipt_id: str) -> dict[str, Any]:
        return self.api.paid_pack_receipt(order_id, receipt_id)

    def paid_pack_receipt_file(self, order_id: str, receipt_id: str) -> bytes:
        return self.api.paid_pack_receipt_file(order_id, receipt_id)

    def paid_pack_accept(self, order_id: str, receipt_id: str) -> dict[str, Any]:
        return self.api.paid_pack_accept(order_id, receipt_id)

    def paid_pack_reject(self, order_id: str, receipt_id: str, reason: str) -> dict[str, Any]:
        return self.api.paid_pack_reject(order_id, receipt_id, reason)

    def paid_pack_handoff(self, order_id: str, receipt_id: str) -> dict[str, Any]:
        return self.api.paid_pack_handoff(order_id, receipt_id)

    def list_act_kinds(self) -> dict[str, Any]:
        """The act registry (contract 2.44). Each kind publishes `wanted_when`
        (which wants need it), `declaration` (the bid-time fields, no context
        needed) and `template` (the step, ready to file). Read it before
        declaring a block: the fields are the kind's, not the harness's."""
        if self._act_kinds is None:
            self._act_kinds = self.api.act_kinds()
        return self._act_kinds

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
        brief = response.get("brief") if isinstance(response, dict) else None
        if not isinstance(brief, dict):
            # Contract 4.1.60: a closed want answers 200, brief null, and says
            # why on `bidding`; carried under a key no door reads.
            bidding = response.get("bidding") if isinstance(response, dict) else None
            return {"_bidding": bidding} if isinstance(bidding, dict) else {}
        return brief

    def _door_is_published(self) -> bool:
        """Whether this bench publishes the free validate door. Probed once.

        The protocol call already rides every run and names the contract, so
        the probe costs nothing extra and needs no guesswork: contract 3.0 is
        where the door appears. A bench that answers 404 to the route anyway
        (an odd deployment, a proxy) flips this to False on the first call and
        the filing door is the only judge for the rest of the run.
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

    def plan_door_speaks_one_language(self) -> bool:
        """Whether this bench's PLAN door answers in the contract 4.0 shape.

        Read off the protocol call that already rides every run. This harness
        reads the new reply and nothing else -- no shim, no second language --
        so a bench below 4.0 is a bench it cannot plan against, and saying so
        is better than a loop that files nothing and never explains why.

        An unreadable protocol reads as "yes": a probe that cannot answer must
        never be the thing that stops a plan the door would have taken.
        """
        try:
            version = str((self.protocol() or {}).get("contract_version") or "")
            return int(version.split(".")[0]) >= PLAN_DOOR_MIN_CONTRACT_MAJOR
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Contract version unreadable; assuming the plan door "
                            "speaks contract %s", PLAN_DOOR_MIN_CONTRACT)
            return True

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
                    "This bench publishes no proposals/validate route; the "
                    "filing door is the only judge for the rest of this run"
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
        """What the model reads back: every problem at once, with its fix.
        `local` is accepted and ignored (0.74.0): there is no local mirror."""
        problems = [p for p in (door.get("problems") or []) if isinstance(p, dict)]
        return {
            "problems": problems,
            "problem_count": len(problems),
            "corrections": door.get("corrections") or [],
        }

    @staticmethod
    def _door_refused(error: BookOfHousesApiError) -> dict[str, Any]:
        """A refused filing, handed back in the door's own words (0.74.0).

        Nothing was written, so the round is not spent: non-terminal. The
        bench's own keys on the refusal body (`field`, `reason`, `fix`, ...)
        ride at the top, where a model reading a tool result looks. The
        harness adds no sentence of its own about the bench's rules.
        """
        refusal: dict[str, Any] = {
            "ok": False,
            "error": error.code,
            "rej": error.rej,
            "status": error.status,
            "detail": error.message,
            "terminal": False,
            "message": (
                "Nothing was filed. The bench refused this filing; `detail` "
                "and `fix` are its own words. Change what they name and file "
                "once more."
            ),
        }
        body = error.body if isinstance(error.body, dict) else {}
        for key in FILE_DOOR_BODY_KEYS:
            if body.get(key) is not None and key not in refusal:
                refusal[key] = body[key]
        return refusal

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

    # ONE SIZE CAP, ON THIS LIST ONLY (0.35.5, kept by the 0.74.0 cut). On
    # 2026-09-09 one `list_proposals` call handed a GLM run 213,096 characters
    # (~53k tokens): 79 bids, each with its whole plan. Every row now goes as
    # the bench sent it, and only when the whole answer runs past the cap do
    # the oldest rows with no move on them drop out, counted and said.
    LIST_PROPOSALS_CHARS = 48_000

    def list_proposals(self) -> dict[str, Any]:
        rows = [row for row in (self.api.proposals() or []) if isinstance(row, dict)]
        payload: dict[str, Any] = {"ok": True, "proposals": rows, "omitted": 0}
        if self._measure(payload) <= self.LIST_PROPOSALS_CHARS:
            return payload
        # The rows with a move on them always stay; the rest go oldest first.
        candidates = sorted(
            (i for i, row in enumerate(rows) if not row.get("your_move")),
            key=lambda i: str(rows[i].get("filed_at") or ""),
        )
        dropped: set[int] = set()
        for index in candidates:
            if self._measure({"proposals": [r for i, r in enumerate(rows)
                                            if i not in dropped]}) <= self.LIST_PROPOSALS_CHARS:
                break
            dropped.add(index)
        payload["proposals"] = [r for i, r in enumerate(rows) if i not in dropped]
        payload["omitted"] = len(dropped)
        payload["note"] = (
            f"{len(dropped)} older bid(s) with no move on them are not listed, to "
            "keep this answer small; the bench still holds them."
        )
        return payload

    @staticmethod
    def _measure(value: Any) -> int:
        try:
            return len(json.dumps(value, separators=(",", ":"), default=str))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return len(str(value))

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
        target_id there is no door to ask, and a bench with no validate door
        (below contract 3.0) leaves its filing door as the only judge: the
        harness keeps no copy of the bench's rules (0.74.0).

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
        if not target_id:
            return {
                "ok": False,
                "error": "target_id_required",
                "problems": [],
                "problem_count": 0,
                "message": (
                    "Nothing was checked. The bench is the only judge of a "
                    "proposal: pass this want's target_id and its free validate "
                    "door answers with every problem at once."
                ),
            }
        key = str(target_id)
        attempts = self._validate_attempts.get(key, 0)
        if enforce_cap and attempts >= MAX_VALIDATE_ATTEMPTS:
            return self._door_loop_is_over(key)
        door = self.validate_at_the_door(target_id, proposal)
        if door is None:
            return {
                "ok": True,
                "checked": False,
                "problems": [],
                "problem_count": 0,
                "source": "no_validate_door",
                "message": (
                    "This bench publishes no free validate door, so nothing was "
                    "checked here. Its filing door is the judge: file, and its "
                    "refusal, if any, comes back in its own words."
                ),
            }
        if enforce_cap:
            attempts += 1
            self._validate_attempts[key] = attempts
        problems = self._door_problem_payload(door)["problems"]
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
            "problems": _door_rows(problems),
            "problem_count": len(problems),
            "summary": summary,
            # TRIMS ARE NOT PROBLEMS (contract 4.0.12): a headline over 40
            # characters or a research note over its cap is cut at a word at
            # filing, and this is what would be stored. ALWAYS PRESENT.
            "trimmed": _door_rows(door.get("trimmed")),
            "corrected_ok": bool(door.get("corrected_ok")),
            "corrections": list(door.get("corrections") or []),
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
            "problems": _door_rows(problems),
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

    def drop_draft_step(
        self, target_id: str, step: int, *, kind: str = "plan"
    ) -> dict[str, Any]:
        """ONE STEP OUT, through the door's own drop instruction.

        THE SECOND EXIT ON A STEP-LEVEL PROBLEM (0.38.3). A step whose whole
        work the bench refuses -- one that only restates the person's own
        contact pick, say -- is answered by replacing it or by taking it out,
        and rewording one line of it cannot clear it. `{"kind": ..., "drop":
        {"step": N}}` on the PATCH door: the bench removes the step, renumbers
        what is left, and answers with its next problem. One round, like a
        patch.

        A bench that publishes no drop instruction answers the ordinary way
        and the loop carries on from whatever it says. `step` counts from ONE.
        """
        return self._draft_answer(
            self.api.drop_proposal_draft_step, target_id, int(step), kind
        )

    def insert_draft_step(
        self, target_id: str, before: int, step: dict[str, Any], *, kind: str = "plan"
    ) -> dict[str, Any]:
        """ONE STEP IN, through the door's own insert instruction (0.39.0).

        THE THIRD EXIT ON A STEP-LEVEL PROBLEM. `missing_who` is the one that
        forced it: a plan that reaches a person needs a who step above it, the
        who step is the agent's to put in, and the door hands back the exact
        call. `{"kind": ..., "insert": {"before": N, "step": {...}}}` on the
        PATCH door: the bench re-expands the plan, slides every pointer at or
        past `before` up by one, and answers with its next problem. `before`
        counts from ONE, like drop. Spends one round, like a patch.

        A 422 `insert_not_possible` comes back as its BODY, like every other
        door refusal here, and the loop reads its reason rather than retrying.
        """
        return self._draft_answer(
            self.api.insert_proposal_draft_step,
            target_id,
            int(before),
            dict(step or {}),
            kind,
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
        reachability handshake and this agent's own bid on the round.
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

    def decline_target(
        self,
        target_id: str,
        reason: str,
        idempotency_key: str = "",
        missing_tools: Any = None,
    ) -> dict[str, Any]:
        """A PASS IS AN ANSWER (contract 4.1.55): `{"decline": "<one line>"}`
        on the proposal door answers 200 {declined: true}, and the want leaves
        this agent's open list. A bench without the door refuses it; that
        answer comes back in its own words and the caller keeps the local
        record.

        `missing_tools` (contract 4.1.65) rides only when it names something,
        never as an empty list: it is how a missing tool is flagged for
        building."""
        if isinstance(missing_tools, str):
            missing_tools = [missing_tools]
        tools = [str(tool).strip() for tool in (missing_tools or []) if str(tool).strip()]
        body: dict[str, Any] = {"decline": str(reason)}
        if tools:
            body["missing_tools"] = tools
        seed = f"{target_id}|{reason}" + ("|" + "|".join(tools) if tools else "")
        key = idempotency_key or "decline-" + hashlib.sha256(seed.encode()).hexdigest()[:24]
        try:
            return self.api.submit_proposal(target_id, body, key)
        except BookOfHousesApiError as error:
            if 400 <= int(error.status or 0) < 500:
                return self._door_refused(error)
            raise

    def _the_small_proposal_at_the_door(
        self, target_id: str, proposal: dict[str, Any], brief: Any
    ) -> tuple[dict[str, Any], list[Any], dict[str, Any] | None]:
        """THE FREE DOOR, ONE FIX ROUND, THEN FILE. (proposal, trims, refusal).

        `POST .../proposals/validate` writes nothing, counts against nothing
        and answers with every problem at once plus `trimmed` -- each entry
        {path, from, to, from_chars, to_chars}, exactly what would be stored
        if this proposal were filed as it stands. A TRIM IS NOT A REFUSAL
        (rule 244): the door takes the words and shortens what is over a cap,
        so the trims are logged and never retried.

        A PROBLEM IS DIFFERENT, and the old policy here was wrong. It filed
        every proposal anyway "so the door's own answer is the record" -- but
        the validate door and the filing door run ONE pipeline, so a problem
        here is a refusal there, every time. On a one-bid-per-want board that
        spent the round to learn what we had just been told for free.

        So: the problems get ONE fix round of the fixes this package can
        actually make -- a question shape, an off-list tool, a paragraph over
        the cap (REJ-21: the door refuses it and never cuts it, so a length
        problem is ours to change, never one to "file anyway") -- and the door
        is asked again for free. The bench's own `corrected_plan` is taken when
        it says `corrected_ok`, because that correction is the door's. What is
        left after that is filed anyway, EXCEPT a required field nobody can
        fill in from here: a bid the door has already refused for a missing
        `research_links` is a round spent on a certainty.
        """
        door = self.validate_at_the_door(target_id, proposal)
        if not isinstance(door, dict):
            return proposal, [], None
        trims = [row for row in (door.get("trimmed") or []) if row]
        problems = [p for p in (door.get("problems") or []) if isinstance(p, dict)]
        if problems:
            _LOGGER.warning(
                "Validate door named %d problem(s) on the proposal for target "
                "%s (%s); one fix round before filing",
                len(problems),
                target_id,
                ", ".join(str(row.get("code") or "?") for row in problems),
            )
            proposal, problems = self._one_fix_round(
                target_id, proposal, brief, door, problems
            )
        if trims:
            _LOGGER.info(
                "Validate door will trim %d field(s) of the proposal for "
                "target %s: %s",
                len(trims),
                target_id,
                ", ".join(str(row.get("path") or "?") for row in trims),
            )
        if problems:
            # FILE ONLY WHAT PASSES (H3, 0.74.0). The validate door and the
            # filing door run one pipeline, so a problem left here is a
            # refusal there; nothing is filed "anyway". The rows go back in
            # the door's own words, and the draft loop asks once to repair.
            _LOGGER.warning(
                "Target %s: the free door still names %d problem(s) (%s); "
                "nothing filed",
                target_id,
                len(problems),
                ", ".join(str(row.get("code") or "?") for row in problems),
            )
            return proposal, trims, {
                "ok": False,
                "error": draft.REFUSED_AT_VALIDATE,
                "terminal": False,
                "problems": problems,
                "bench_fixed": door.get("bench_fixed") or [],
                "message": (
                    "Nothing was filed. The free validate door -- the same "
                    "pipeline the filing door runs -- named what to change; its "
                    "own words are on `problems`."
                ),
            }
        return proposal, trims, None

    def _one_fix_round(
        self,
        target_id: str,
        proposal: dict[str, Any],
        brief: Any,
        door: dict[str, Any],
        problems: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """The door's own correction, else ours, checked once for free."""
        if door.get("corrected_ok") and isinstance(door.get("corrected_plan"), dict):
            _LOGGER.warning(
                "Target %s: the door handed back a corrected proposal that "
                "passes it unchanged; filing that",
                target_id,
            )
            return door["corrected_plan"], []
        fixed, mended = draft.mend_the_small_proposal(proposal, brief)
        if not mended:
            return proposal, problems
        _LOGGER.warning(
            "Target %s: mended the proposal at home (%s); asking the free "
            "door once more",
            target_id,
            "; ".join(mended),
        )
        second = self.validate_at_the_door(target_id, fixed)
        if not isinstance(second, dict):
            return fixed, []
        left = [row for row in (second.get("problems") or []) if isinstance(row, dict)]
        if not left:
            _LOGGER.info("Target %s: the mended proposal passes the door", target_id)
        return fixed, left

    def submit_proposal(
        self, target_id: str, proposal: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """FILE ONE PROPOSAL. Two roads, and the proposal itself says which.

        A PROPOSAL IS EIGHT FIELDS (rule 243, 2026-09-11; contract 4.0.12
        added the headline): a title, a short headline for the person's card,
        a paragraph, one odds number, a price, one to three research links,
        up to three questions for the person, and the tools it needs. It carries
        no steps, so it takes the small road -- one free call at the validate
        door, then the filing door. A proposal that carries steps (the old
        whole-plan shape) is filed exactly as written: nothing here repairs a
        plan, and the bench's answer comes back in its own words (0.74.0).
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
        bidding = brief.get("_bidding") if isinstance(brief, dict) else None
        if isinstance(bidding, dict) and str(bidding.get("state") or "open") != "open":
            # Contract 4.1.60: a closed want's brief answers 200 with its reason.
            return {
                "ok": False,
                "error": "target_not_open",
                "terminal": True,
                "bidding": bidding,
                "message": str(bidding.get("message") or "This want is not open to this "
                               "agent. No proposal was filed; do not retry it."),
            }
        if draft.is_small_proposal(proposal):
            # RULE 243 (2026-09-11): A PROPOSAL IS SEVEN FIELDS AND ONE CALL.
            # There are no steps to repair and no blocks to merge -- but the
            # question shapes, the want's tool list and the paragraph cap ARE
            # this package's to get right, and since 0.38.2 the free door gets one fix round
            # before anything is filed.
            proposal, _mended = draft.mend_the_small_proposal(proposal, brief)
            if _mended:
                _LOGGER.info(
                    "Target %s: proposal mended before the door (%s)",
                    target_id,
                    "; ".join(_mended),
                )
            proposal, trims, refusal = self._the_small_proposal_at_the_door(
                target_id, proposal, brief
            )
            if refusal is not None:
                return refusal
        else:
            trims = []
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
        try:
            # Filed once, as written. A refusal is the door's answer, in its
            # own words, handled below; nothing is repaired or re-filed here.
            result = self.api.submit_proposal(target_id, proposal, idempotency_key)
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
                refusal = self._block_refusal(target_id, error)
                if refusal["terminal"] and fleet_engaged:
                    self.fleet.mark_target_reviewed(
                        agent_id=self.fleet_agent_id,
                        target_id=target_id,
                        target_round=target_round,
                    )
                return refusal
            if error.rej and 400 <= error.status < 500 and error.status not in (404, 409):
                # A refused bid writes nothing, so the round is not spent. The
                # door's own words come back non-terminal, and nothing is
                # repaired at home.
                return self._door_refused(error)
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

    def sync_default_approach(self, configured: Any) -> dict[str, Any]:
        """THE BENCH HOLDS THE DEFAULT AGENT.YAML SETS (0.57.0). Never raises.

        An agent registered before its default existed (every lab agent) can
        only set it at `POST /api/bench/me/approach` (contract 4.1.3). So at
        worker start: when agent.yaml sets a default and `GET /me` carries an
        `approach` that differs, the yaml's is sent once, as written; the
        bench judges its words (0.74.0). Nothing is ever cleared from here,
        and a bench whose /me has no `approach` key (one before 4.1.3) is left
        alone.
        """
        wanted = (
            {str(dial): str(word).strip() for dial, word in configured.items()
             if isinstance(word, str) and word.strip()}
            if isinstance(configured, dict) else {}
        )
        if not wanted:
            return {"synced": False, "reason": "no_default"}
        try:
            me = self.api.me()
        except Exception as error:  # noqa: BLE001 - a start-up nicety never stops a worker
            return {"synced": False, "reason": "me_unreadable", "error": type(error).__name__}
        if not isinstance(me, dict) or "approach" not in me:
            return {"synced": False, "reason": "bench_has_no_default_approach"}
        held = me.get("approach")

        def folded(approach: Any) -> dict[str, str]:
            if not isinstance(approach, dict):
                return {}
            return {str(k): str(v).strip().casefold() for k, v in approach.items() if v}

        if folded(held) == folded(wanted):
            return {"synced": False, "reason": "already_set", "approach": wanted}
        try:
            answer = self.api.set_approach(wanted)
        except BookOfHousesApiError as error:
            _LOGGER.warning(
                "Default approach: the bench refused %s from agent.yaml (%s %s: %s)",
                wanted,
                error.status,
                error.code,
                error.message,
            )
            return {
                "synced": False,
                "reason": "refused",
                "status": error.status,
                "message": error.message,
            }
        except Exception as error:  # noqa: BLE001
            return {"synced": False, "reason": "unreachable", "error": type(error).__name__}
        stored = (answer or {}).get("approach") if isinstance(answer, dict) else None
        _LOGGER.info(
            "Default approach: the bench held %s; set it to %s from agent.yaml",
            held,
            stored or wanted,
        )
        return {"synced": True, "approach": stored or wanted, "was": held}

    WITHDRAW_CAUSES = ("cannot_deliver", "other")
    WITHDRAW_REASON_LIMIT = 1000

    def withdraw_proposal(
        self,
        proposal_id: str,
        *,
        reason: str,
        cause: str = "other",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Leave a bid out loud, through the public exit, and say why.

        A selected agent that TRULY cannot produce its plan withdraws with
        cause ``cannot_deliver`` instead of retrying in silence: the person
        learns why the pick failed and every held bid on the want returns to
        the table.

        NOBODY WITHDRAWS ON THE HARNESS'S OWN OPINION (0.38.4). This is a
        thing the model asks for, or a person does. What forced the line: a
        stalled plan obligation used to withdraw automatically after N
        identical failures, and on 2026-09-11 the failure was a BENCH-side bug
        on a step the bench had stamped itself -- so the harness tried to
        withdraw the person's chosen agent over a mistake that was not the
        agent's. It only failed because the call carried no idempotency key.

        The key is DERIVED when none is given: the same withdrawal sent twice
        is one withdrawal, and the bench replays it in the same shape.
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
        body = {"reason": text[: self.WITHDRAW_REASON_LIMIT], "cause": cause}
        key = str(idempotency_key or "").strip() or _withdraw_key(proposal_id, body)
        return self.api.withdraw_proposal(proposal_id, body, key)

    def read_finalist_answers(self, target_id: str, proposal_id: str) -> dict[str, Any]:
        return self.api.finalist_answers(target_id, proposal_id)

    def submit_informed_plan(
        self,
        target_id: str,
        proposal_id: str,
        plan: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """File the informed plan exactly as the model wrote it (0.74.0).

        This is the road for a bench with no draft door; a 4.x bench takes the
        plan at the draft door (`file_plan_from_draft`). It used to merge the
        brief's blocks in, bind research, spread contacts, retire grant steps,
        drop blank form steps and run a local mirror first -- a second copy of
        the bench's rules that buried correct plans more than once. Now the
        plan goes to the door as written and the door's answer comes back in
        its own words. A refused plan writes nothing, so it is non-terminal.
        """
        try:
            return self.api.submit_informed_plan(
                target_id, proposal_id, plan, idempotency_key
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
                },
            )
            if error.rej in (REJ_BLOCK_DECLARATION, REJ_HOLLOW_BLOCK):
                return self._block_refusal(target_id, error)
            if error.rej and 400 <= error.status < 500 and error.status not in (404, 409):
                return self._door_refused(error)
            raise

    def current_step(self, deal_id: str, whole: bool = False) -> dict[str, Any]:
        """The bench's current-step answer, WHOLE (0.74.0, audit item 11).

        No whitelist: nine CHANGELOG entries were "the whitelist dropped a key
        the bench added" (person_sees_control, owed_replies, earlier_steps,
        repeats, control, ...), so every key the bench sends now reaches the
        model as sent. `whole=True` is the bench's own `?full=1` answer -- every
        earlier step's hand-back whole, the pulse history and the thread --
        for a run that needs more than the small default.

        Two relay notes are kept, both the bench's own words carried from one
        call to the next: the ids of the person's unanswered messages (a reply
        names what it answers, 0.56.1), and the cards the outcome door took off
        the last filing on this step (`your_last_filing`, 0.64.0).
        """
        result = (self.api.current_step(deal_id, full=True) if whole
                  else self.api.current_step(deal_id))
        payload: dict[str, Any] = dict(result) if isinstance(result, dict) else {}
        step = payload.get("current_step") if isinstance(payload.get("current_step"), dict) else {}
        step_id = str(step.get("id") or "")
        if step_id:
            self._last_step_id = step_id
        finds = self._last_filing_finds.get(step_id)
        if finds:
            payload[LAST_FILING_KEY] = {
                **{key: list(rows) for key, rows in finds.items()},
                "note": LAST_FILING_NOTE,
            }
        thread = payload.get("step_thread") if isinstance(payload.get("step_thread"), dict) else {}
        self._remember_unanswered(step_id, thread.get("unanswered_messages") or [])
        return payload

    def _remember_unanswered(self, step_id: str, rows: Any) -> None:
        if not step_id or not isinstance(rows, list):
            return
        self._unanswered[step_id] = [
            str(row.get("id")) for row in rows
            if isinstance(row, dict) and row.get("id")
        ]

    def reply_step_message(
        self,
        deal_id: str,
        step_id: str,
        reply: str,
        idempotency_key: str,
        answering: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """A reply on the step thread that NAMES what it answers (0.56.1).

        WHAT FORCED IT: 2026-09-25, lab agent Rick answered the person's one
        message six times in 70 seconds. The bench pays only what a reply names
        on `answering`, the harness never sent the key, so every 200 read
        `answered: []` and the message stayed owed. When the caller names
        nothing, the latest unanswered message on this step is named, which
        answers everything before it. No unanswered message, no claim.
        """
        if not answering:
            owed = self._unanswered.get(step_id) or []
            answering = owed[-1] if owed else None
        if answering:
            result = self.api.post_step_message(
                deal_id, step_id, reply, idempotency_key, answering=answering)
        else:
            result = self.api.post_step_message(deal_id, step_id, reply, idempotency_key)
        if isinstance(result, dict):
            self._remember_unanswered(step_id, result.get("unanswered_messages"))
        return result

    def report_worker_status(
        self, deal_id: str, step_id: str, state: str, round_number: int,
        reason: str | None = None, *, cause: str | None = None,
    ) -> dict[str, Any]:
        """Say what the runtime is doing on this step. `parked` means nobody
        is working on it, and `reason` says why in one plain sentence (W29).
        The door writes its own words today and ignores the field; it is sent
        anyway, because the day the door reads it the harness is already
        saying it, and the same sentence goes on the step thread either way."""
        payload: dict[str, Any] = {"state": state, "round": round_number}
        if cause is not None:
            payload["cause"] = cause
        words = (reason or "").strip()
        if words:
            payload["reason"] = words[:280]
        return self.api._request(
            "POST", f"/api/bench/deals/{deal_id}/steps/{step_id}/worker-status",
            payload=payload, authenticated=True,
        )

    def post_check_in(
        self, deal_id: str, pulse: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """A work pulse, as written; the 201 comes back whole (0.74.0: no field
        allowlist and no answer whitelist -- the door judges the pulse, and the
        debts, acts and replies that ride the 201 reach the model as sent).
        `ask_not_open` keeps its structured answer: the door's sentence is the
        unblocking move."""
        try:
            return self.api.post_check_in(deal_id, pulse, idempotency_key)
        except BookOfHousesApiError as error:
            if error.status == 422 and error.code == "ask_not_open":
                return {"ok": False, "error": "ask_not_open", "move": error.message}
            if 400 <= int(error.status or 0) < 500:
                return self._door_refused(error)
            raise

    def wait_outside(
        self,
        deal_id: str,
        step_id: str,
        wait: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """WAIT (rule 216): waiting on the outside world is a state, not
        silence. The wait goes to the door exactly as written (0.74.0: no field
        allowlist -- the send-out slot's `service` and `usually_minutes` were
        refused here though the bench's own form carries them). Pass end=True
        to end it yourself. A refusal comes back in the door's own words."""
        body = {"end": True} if wait.get("end") else dict(wait)
        try:
            return self.api.declare_outside_wait(deal_id, step_id, body, idempotency_key)
        except BookOfHousesApiError as error:
            if 400 <= int(error.status or 0) < 500:
                return self._door_refused(error)
            raise

    def _published_doors(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._doors is None or refresh:
            contract = self.api.read_contract()
            self._contract = contract if isinstance(contract, dict) else {}
            self._doors = doors.published_doors(self._contract)
        return self._doors

    def bench_doors(self, word: str = "", path: str = "", method: str = "") -> dict[str, Any]:
        """The bench's own list of doors, or one door whole (0.74.0)."""
        try:
            listed = self._published_doors()
        except BookOfHousesApiError as error:
            return {"ok": False, "error": "contract_unreadable", "detail": error.message}
        if path:
            door = (doors.match_door(listed, method, path) if method else None) or next(
                (row for row in listed if row["path"] == str(path).split("?", 1)[0]), None)
            if door is None:
                return {"ok": False, "error": "door_not_published", "path": path}
            return {"ok": True, "door": doors.door_whole(door, self._contract or {})}
        rows = [doors.door_row(row) for row in doors.find_doors(listed, word)]
        return {"ok": True, "count": len(rows), "doors": rows}

    def call_door(
        self, method: str, path: str, body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None, idempotency_key: str = "",
    ) -> Any:
        """Call one door the bench publishes (0.74.0). A door that is not on
        its contract is never called; a refusal comes back in its own words."""
        method = str(method or "GET").upper()
        path = str(path or "").strip()
        if "{" in path or "}" in path:
            return {"ok": False, "error": "path_not_filled",
                    "message": "Put the real ids in place of every {...} in the path."}
        try:
            door = doors.match_door(self._published_doors(), method, path)
            if door is None:
                door = doors.match_door(self._published_doors(refresh=True), method, path)
        except BookOfHousesApiError as error:
            return {"ok": False, "error": "contract_unreadable", "detail": error.message}
        if door is None:
            return {"ok": False, "error": "door_not_published",
                    "message": f"{method} {path} is not a door this bench publishes; "
                               "toll_bench.doors lists them."}
        if method != "GET" and not idempotency_key:
            idempotency_key = "door-" + hashlib.sha256(
                json.dumps([method, path, body], sort_keys=True, default=str).encode()
            ).hexdigest()[:32]
        try:
            return self.api.call_door(method, path, body, query, idempotency_key)
        except BookOfHousesApiError as error:
            if 400 <= int(error.status or 0) < 500:
                return self._door_refused(error)
            raise

    def submit_step_form(
        self, form: dict[str, Any], body: dict[str, Any], idempotency_key: str,
        *, deal_id: str = "", step_id: str = "",
    ) -> dict[str, Any]:
        """ANY FORM THE BENCH PUBLISHES ON `submission.actions`, posted to its
        own endpoint with its own method (0.74.0). The bench publishes sixteen
        step forms -- send_out, extend_send_out, end_send_out, fail_step,
        skip_repeat_item, end_wait and the rest -- and this is the one door all
        of them go through, so a form the bench adds tomorrow works the day it
        ships. Only a path on this bench's own API is ever called. A refusal
        comes back in the door's own words."""
        endpoint = str(form.get("endpoint") or form.get("call") or "").strip()
        method = str(form.get("method") or "POST").strip().upper()
        if " " in endpoint:
            head, _, rest = endpoint.partition(" ")
            if head.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                method, endpoint = head.upper(), rest.strip()
        endpoint = endpoint.replace("{deal_id}", str(deal_id)).replace("{step_id}", str(step_id))
        if not endpoint.startswith("/api/bench/"):
            return {"ok": False, "error": "form_has_no_bench_endpoint",
                    "message": "This form names no /api/bench/ endpoint to post to."}
        try:
            return self.api.post_form(method, endpoint, body, idempotency_key)
        except BookOfHousesApiError as error:
            if 400 <= int(error.status or 0) < 500:
                return self._door_refused(error)
            raise

    def refile_source_correction(
        self, deal_id: str, step_id: str, correction: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """One explicitly returned internal draft; no generic owned-block bypass."""
        from .corrections import matches_current

        # Validate the full authenticated snapshot, before model-view whitelisting
        # and history trimming can omit an older exact returned item.
        current = self.api.current_step(deal_id)
        if not matches_current(current, deal_id, step_id, correction):
            return {"ok": False, "error": "source_correction_changed",
                    "message": "Read attention again; this exact correction is no longer ready."}
        return self._file_act(deal_id, step_id,
                              {"kind": "calls", "source_correction": dict(correction)},
                              idempotency_key)

    def propose_act(
        self, deal_id: str, step_id: str, act: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """ACT (rules 212 and 219): you propose, the platform executes. ONE
        door for every kind, and the kinds are the bench's
        (`list_act_kinds`). On a loop step the act names its item with
        `repeat_item`, exactly as current_step says.

        THE BENCH IS THE ONE DOOR (0.56.4, finished in 0.74.0). The act goes to
        the bench exactly as filed, every kind: no key refused, no field
        shaped or cut here, and no rule-229 block check (the bench refuses a
        duplicate 409 and its `turn` says whose move a step is). A refusal
        comes back in the door's own words (`_act_refusal`). `kind` defaults
        to email, as it always has."""
        kind = str(act.get("kind") or "email").strip().lower()
        return self._file_act(deal_id, step_id, dict(act, kind=kind), idempotency_key)

    def _file_act(
        self, deal_id: str, step_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        try:
            return self.api.propose_act(deal_id, step_id, payload, idempotency_key)
        except BookOfHousesApiError as refusal:
            if not 400 <= int(refusal.status or 0) < 500:
                raise
            return self._act_refusal(refusal)

    @staticmethod
    def _act_refusal(refusal: BookOfHousesApiError) -> dict[str, Any]:
        """The act door's answer, VERBATIM, where a model reading a tool
        result sees it: `error` is the door's word, `message` its sentence,
        and every other key it sent (kinds, fields, the reply it names, the
        open items) rides at the top level untouched."""
        body = refusal.body if isinstance(refusal.body, dict) else {}
        out: dict[str, Any] = {key: value for key, value in body.items()
                               if key not in ("ok", "error", "code")}
        sentence = body.get("error") if isinstance(body.get("error"), str) else ""
        out.update(ok=False, status=refusal.status, error=refusal.code)
        if sentence and not out.get("message"):
            out["message"] = sentence
        elif sentence and out.get("message") != sentence:
            out["refusal"] = sentence
        out.setdefault("message", refusal.message)
        return out

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
        handoff = outcome.get("pack_handoff")
        if handoff is not None:
            required = {"summary", "labels", "artifacts"}
            if (not isinstance(handoff, dict) or not required <= set(handoff)
                    or set(handoff) - required - {"draft_suggestions"}):
                return {"ok": False, "error": "invalid_pack_handoff"}
            summary, labels, artifacts = (
                handoff["summary"], handoff["labels"], handoff["artifacts"]
            )
            if (not isinstance(summary, str) or not summary.strip()
                    or not isinstance(labels, list)
                    or any(not isinstance(label, str) for label in labels)
                    or not isinstance(artifacts, list) or len(artifacts) > 8):
                return {"ok": False, "error": "invalid_pack_handoff"}
            try:
                for receipt in artifacts:
                    if not isinstance(receipt, str):
                        return {"ok": False, "error": "invalid_pack_handoff"}
                    uuid.UUID(receipt)
            except (TypeError, ValueError, AttributeError):
                return {"ok": False, "error": "invalid_pack_handoff"}
            suggestions = handoff.get("draft_suggestions")
            if suggestions is not None:
                if not isinstance(suggestions, list) or len(suggestions) > 8:
                    return {"ok": False, "error": "invalid_pack_handoff"}
                try:
                    for suggestion in suggestions:
                        if (not isinstance(suggestion, dict)
                                or set(suggestion) != {"slot_id", "title", "brief", "artifacts"}
                                or not isinstance(suggestion["title"], str)
                                or not isinstance(suggestion["brief"], str)
                                or not isinstance(suggestion["artifacts"], list)
                                or len(suggestion["artifacts"]) > 8):
                            return {"ok": False, "error": "invalid_pack_handoff"}
                        uuid.UUID(suggestion["slot_id"])
                        for receipt_id in suggestion["artifacts"]:
                            uuid.UUID(receipt_id)
                except (TypeError, ValueError, AttributeError):
                    return {"ok": False, "error": "invalid_pack_handoff"}
            # A resolved card has no live step. Its handoff is metadata for the
            # next selected agent, not a second delivery or a cost filing.
            if set(outcome) <= {"pack_handoff", "note"}:
                try:
                    return self.api.file_outcome(
                        target_id, {"pack_handoff": handoff}, idempotency_key
                    )
                except BookOfHousesApiError as error:
                    return {"ok": False, "error": error.code, "status": error.status,
                            "message": error.message, "detail": error.body}
        step_ref = str(outcome.get("step_ref") or "").strip() or self._last_step_id
        # RULE 230 (2026-09-05): `file_url` is the agent-hosted lane. The
        # platform fetches the link ONCE, sniffs the bytes, hashes them and
        # drops them; `claim_url` rides along for a here.now page the person
        # keeps within the day. `filename` names the file on the card.
        # WIN LADDER (bench contract 4.1.0): `proof` rides the same filing.
        # `last_round` (bench contract 4.1.42; 0.66.0): the round that
        # finishes a step done in rounds. Refused here until 0.66.0, which is
        # how Dr. Moose's last painting round could never be handed in.
        # `url` (0.75.0, 2026-11-05): the link a step promised (deliverable
        # channel `link`). WHAT FORCED IT: the bench's outcome form REQUIRES
        # `url` on a link step and this list refused it before it left the
        # machine, so Milo looped on a done calendar step: with `url` refused
        # here, without it refused by the form. The bench's link gate judges
        # the address.
        allowed = {"note", "text", "document", "step_ref", "url", "file_url", "claim_url",
                   "filename", "proof", "last_round", "pack_handoff"}
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
            name for name in ("text", "document", "url", "file_url") if outcome.get(name)
        ]
        if len(content_fields) != 1:
            return {
                "ok": False,
                "error": "exactly_one_outcome_content_required",
                "allowed": ["text", "document", "url", "file_url"],
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
        # RULES 230 AND 233 (a promised file, a promised set of cards) are
        # the bench's to judge: `deliverable_missing` and the shape refusals
        # come back below in its own words (0.74.0).
        try:
            result = self._post_the_outcome(target_id, outcome, idempotency_key)
            self._remember_finds(step_ref, result)
            return result
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

    def _post_the_outcome(
        self, target_id: str, outcome: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """The outcome door, carrying what the agent spent on this deal since
        its last filing as the optional `agent_cost` slot (contract 4.1.20,
        0.63.0). No ledger, nothing counted, or a ledger on another want sends
        the outcome exactly as before: the field is absent, never zero.

        The slot is a SLOT, so it never costs the person a filing: a 422
        `agent_cost_slot` files again without it and keeps the tab for the next
        hand-back. A tab is cleared only when the bench recorded it (the 201
        echoes `agent_cost`; null means this bench could not record it yet).
        """
        ledger = self.cost_ledger
        slot = ledger.slot(target_id) if ledger is not None else None
        if slot is None:
            return self.api.file_outcome(target_id, outcome, idempotency_key)
        try:
            result = self.api.file_outcome(
                target_id, {**outcome, "agent_cost": slot}, idempotency_key
            )
        except BookOfHousesApiError as error:
            if error.code != "agent_cost_slot":
                raise
            _LOGGER.warning(
                "the bench refused agent_cost (%s); filing without it", error.message
            )
            return self.api.file_outcome(target_id, outcome, idempotency_key)
        recorded = not (
            isinstance(result, dict) and "agent_cost" in result and result["agent_cost"] is None
        )
        if recorded:
            ledger.paid()
        else:
            _LOGGER.info("the bench filed the outcome but did not record agent_cost yet")
        return result

    def _remember_finds(self, step_ref: str | None, result: Any) -> None:
        """Keep what the outcome 201 says came off this step's list (0.64.0).

        On a step whose list a who step's book reads -- the research step a
        `found_by_research` who step stamps, or a step a `from_step: N` reads
        -- the door takes a made-up address off the list (`finds_refused`)
        and marks a person another of this person's wants already has
        (`finds_taken`), and the real finds stand (contract 4.1.12). The
        filing landed, so nothing is refused; the next read of the same step
        carries both lists under `your_last_filing` so a card can be
        replaced rather than filed again. A filing with nothing off clears it.
        """
        step_id = str(step_ref or "").strip()
        if not step_id or not isinstance(result, dict):
            return
        finds = {
            key: list(result[key])
            for key in FINDS_KEYS
            if isinstance(result.get(key), list) and result[key]
        }
        self._last_filing_finds.pop(step_id, None)
        if not finds:
            return
        self._last_filing_finds[step_id] = finds
        while len(self._last_filing_finds) > FINDS_MEMORY:
            self._last_filing_finds.pop(next(iter(self._last_filing_finds)))
        _LOGGER.info(
            "outcome on step %s filed; the door took off %s",
            step_id,
            ", ".join(f"{len(rows)} {key}" for key, rows in finds.items()),
        )

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
        allowed = {"note", "file_url", "claim_url", "filename", "step_ref", "last_round"}
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
        outcome = {key: value for key, value in delivery.items()
                   if value or isinstance(value, bool)}
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
