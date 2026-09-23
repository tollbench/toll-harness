"""The captured plan-door replies, and the scripted door the loop talks to.

Every reply under `tests/fixtures/` came off the bench's own code (see the
README there). A test that wants "an email step with an empty subject" loads
the real answer rather than writing one, so a test can only pass against a
shape the server actually produces.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def reply(name: str) -> dict[str, Any]:
    """One captured plan-door answer, fresh each call."""
    payload = json.loads((FIXTURES / f"plan_reply_{name}.json").read_text())
    return copy.deepcopy(payload["answer"])


def status(name: str) -> int:
    return int(json.loads((FIXTURES / f"plan_reply_{name}.json").read_text())["status"])


def a_row(
    path: str = "",
    *,
    step: Any = None,
    slot: Any = None,
    problem: str = "empty",
    who_fixes: str = "agent",
    say: str = "there is something to do here",
    accepted: Any = (),
    codes: Any = (),
    keys: Any = None,
    key: str = "",
) -> dict[str, Any]:
    """One plan-door row in the ONE shape, for a scripted door.

    Every key is always present, exactly as the bench sends it. A test that
    leaves one out would be testing a reply no server produces. `keys` is the
    one the bench adds only on an unknown-key row off a tool argument, and
    `key` is its own name for the fault -- left off here so the fallback key
    is what a hand-written row is counted under.
    """
    row = {
        "step": step,
        "slot": slot,
        "problem": problem,
        "who_fixes": who_fixes,
        "say": say,
        "path": path or None,
        "accepted": list(accepted),
        "codes": list(codes),
    }
    if keys is not None:
        row["keys"] = list(keys)
    if key:
        row["key"] = key
    return row


def row(answer: dict[str, Any], **match: Any) -> dict[str, Any]:
    """The first problem row matching every key given."""
    for entry in answer.get("problems") or []:
        if all(entry.get(key) == value for key, value in match.items()):
            return entry
    raise AssertionError(f"no row matching {match} in {answer.get('problems')}")


def only(answer: dict[str, Any], *rows: dict[str, Any]) -> dict[str, Any]:
    """The same answer with exactly these rows, and `next_fix` re-derived.

    `next_fix` is the first agent row with a path, which is how the door picks
    it (`draft_door.first_agent_row`).
    """
    answer = copy.deepcopy(answer)
    answer["problems"] = [copy.deepcopy(entry) for entry in rows]
    mine = [entry for entry in answer["problems"] if entry.get("who_fixes") == "agent"]
    with_path = [entry for entry in mine if entry.get("path")]
    answer["next_fix"] = (with_path or mine or [None])[0]
    answer["ready"] = not answer["problems"]
    answer["remaining"] = len(answer["problems"])
    return answer


def holes_written(answer: dict[str, Any]) -> dict[str, Any]:
    """The same reply with every hole the AGENT owed already written.

    A plan whose step has no subject and no body is two things at once: a step
    with holes, and a step with rows about other faults. A test about the
    second says so with this, so that the loop's first move -- write what the
    form says is missing -- is out of its way. `filled` is one of the bench's
    own three states, so nothing here is a shape the server cannot send.
    """
    for entry in answer.get("form_steps") or []:
        for slot in entry.get("slots") or []:
            if slot.get("filler") == "agent" and slot.get("state") == "needed":
                slot["state"] = "filled"
                slot["source"] = "literal"
    return answer


class Door:
    """The plan door, scripted: one answer per call, and the calls recorded."""

    fleet = None

    def __init__(self, *answers: Any):
        self.answers = list(answers)
        self.patches: list[list[dict[str, Any]]] = []
        self.drops: list[int] = []
        self.inserts: list[tuple[int, dict[str, Any]]] = []

    def _next(self) -> dict[str, Any]:
        return self.answers.pop(0) if self.answers else {"ok": True, "ready": True}

    # NEVER WRITTEN BY THE AGENT, UNDER ANY NAME. Checked HERE, so that every
    # end-to-end test in this suite checks it: who a message goes to and who
    # it comes from are the platform's, wired from the step where the person
    # picks out of their own contact book. An address an agent writes is a
    # person it invented.
    NEVER_WRITTEN = ("to", "from", "cc", "bcc")

    def patch_draft(self, target_id, patches, *, kind="bid"):
        for entry in patches:
            tail = str(entry.get("path") or "").rsplit(".", 1)[-1].lower()
            assert tail not in self.NEVER_WRITTEN, (
                "the loop sent a patch to {} -- who a message goes to and who "
                "it is from are never the agent's to write".format(entry.get("path"))
            )
        self.patches.append(copy.deepcopy(list(patches)))
        return self._next()

    def drop_draft_step(self, target_id, step, *, kind="plan"):
        self.drops.append(int(step))
        return self._next()

    def insert_draft_step(self, target_id, before, step, *, kind="plan"):
        self.inserts.append((int(before), dict(step)))
        return self._next()


class SpyDoor(Door):
    """A door that records what it is sent and refuses NOTHING.

    `Door` asserts on an address itself, which is a good belt for every test
    that does not care -- and useless for a test ABOUT the guard, because the
    door's own refusal would pass whether or not the harness has one. This one
    keeps its hands down so the test can look at what really went on the wire.
    """

    def patch_draft(self, target_id, patches, *, kind="bid"):
        self.patches.append(copy.deepcopy(list(patches)))
        return self._next()


class OldDoor(Door):
    """A bench that publishes no insert call at all."""

    insert_draft_step = None
