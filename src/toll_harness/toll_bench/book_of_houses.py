from __future__ import annotations

import time
from typing import Any

from toll_harness.email.book_of_houses import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.fleet import FleetStore


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
        ]
        smart_goals = proposal.get("smart_goals")
        if not isinstance(smart_goals, list) or len(smart_goals) != 1:
            problems.append({"path": "smart_goals", "message": "must contain exactly one goal"})
        questions = proposal.get("finalist_questions")
        if (
            not isinstance(questions, list)
            or len(questions) != 1
            or not isinstance(questions[0], list)
            or len(questions[0]) != 4
        ):
            problems.append(
                {
                    "path": "finalist_questions",
                    "message": "must contain exactly one array of four questions",
                }
            )
        for field in ("pitch_title", "pitch_body"):
            if not str(proposal.get(field) or "").strip():
                problems.append({"path": field, "message": "is required and cannot be blank"})
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
        return {
            "ok": not problems,
            "problems": problems,
            "note": (
                "Local validation uses the current production JSON schema plus required pitch, "
                "goal, and finalist-question checks. Production remains authoritative at submit."
            ),
        }

    def submit_proposal(
        self, target_id: str, proposal: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        validation = self.validate_proposal(proposal)
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
            # a dead round block the repost forever. Read it from the live
            # brief; the same read also catches a target that already closed.
            try:
                brief_response = self.api.target_brief(target_id)
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
            brief = brief_response.get("brief") or {}
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
            result = self.api.submit_proposal(target_id, proposal, idempotency_key)
        except BookOfHousesApiError as error:
            if fleet_engaged and reservation is not None and 400 <= error.status < 500:
                self.fleet.release_reservation(
                    target_id=target_id,
                    target_round=target_round,
                    agent_id=self.fleet_agent_id,
                )
            if fleet_engaged and error.status in (404, 409):
                # Terminal refusals for this round: bidding closed on finalists,
                # a bid already on file, participation ended, or the target
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
            )
            if original.get(key) is not None
        }
        candidate["steps"] = submitted_plan.get("steps")
        candidate["finish_line_cents"] = submitted_plan.get(
            "finish_line_cents", original.get("finish_line_cents") or 0
        )
        validation = self.validate_proposal(candidate)
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
        return self.api.submit_informed_plan(
            target_id, proposal_id, submitted_plan, idempotency_key
        )

    def current_step(self, deal_id: str) -> dict[str, Any]:
        result = self.api.current_step(deal_id)
        step = result.get("current_step") or {}
        deal = result.get("deal") or {}
        thread = result.get("step_thread") or {}
        access = result.get("access") or {}
        material = access.get("material_change") or {}
        swap = access.get("equivalent_swap") or {}
        return {
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
        }

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
        }

    def file_outcome(
        self, target_id: str, outcome: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
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
