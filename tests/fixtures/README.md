# Real plan-door replies, bench contract 4.0

Every file here is a REAL answer from the Book of Houses plan draft door,
captured in process against the server source on the staging box
(2026-09-17), at commit `062aa56ce` plus one change to
`app/blueprints/bench/draft_routes.py` that was still uncommitted in that
shared worktree when these were taken -- it stamps `key` on the two rows that
file writes by hand (the outline ask and the edit refusal), which is why those
two carry one here.

None of it is hand-written. Nothing was left in the staging database: the goal,
proposal and draft each capture created were deleted again afterwards and
counted to zero (the global table counts move on their own -- the staging site
is live -- so the proof is per row, not per table).

Each file is `{"status": <http status the route answered>, "answer": {...}}`.

## What the row shape gained since the first capture

* `key` on EVERY row: the bench's own name for one fault, and what it counts
  tries under. The harness reads it and does not rebuild it.
* `accepted` on more rows, including the empty-slot rows.
* An `empty` row for a slot the agent has not filled: `subject` and `body` on
  the email step below are now problems and not just `state: "needed"` entries
  in the slot table.
* `keys` on the unknown-key rows that come off a TOOL ARGUMENT. The structural
  unknown-key rows (a key on the step, in a wait condition, in an ask) carry
  `accepted` and no `keys`, so a reader has to handle both; there is no capture
  of the tool-argument kind here, because the normalizer takes an argument the
  send does not have off the form before the check sees it.

| file | how it was produced |
|---|---|
| `plan_reply_outline_round.json` | `PUT .../proposals/draft {"kind": "plan"}` on a picked proposal with no steps. The door asks for the form: `next_fix.codes == ["FORM"]`, `problems == []`, `form_steps == []`. |
| `plan_reply_clean.json` | The same PUT carrying a one-step form that stands up. `problems == []`, `ready` true, `next_fix` null. |
| `plan_reply_email_step.json` | A two-step form whose second step sends mail with an empty subject and body and no who step above it. Built from the shape in `app/tests/fixtures/marcia_intro_plan.json`, which is itself a CONSTRUCTED Gmail plan -- Marcia's real production draft is not on this box and nothing here replays it. Six rows now, two of them the empty `subject` and `body`, and the step's own slot table with `action: "email.send"`. |
| `plan_reply_unknown_fields.json` | A form carrying `room_number` on step 1 and `observation` / `condition` inside step 2's wait condition. Two `unknown_field` rows, on two different steps. |
| `plan_reply_two_unknown_one_step.json` | One step carrying BOTH `room_number` and a wait condition with `observation`. Two `unknown_field` rows on ONE step, at two different paths (`form.steps.0` and `form.steps.0.wait`), plus an unrelated row on the same step. |
| `plan_reply_platform_only.json` | `draft_door.form_answer` with the validators answering one `REJ-22`: a single `who_fixes: "platform"` row, `next_fix` null. |
| `plan_reply_edit_refused.json` | `PATCH {"kind": "plan", "drop": {"step": 9}}` against a one-step plan. The 422 edit refusal in the same row shape. |
| `plan_reply_paused.json` | The 409 an agent gets while the door is paused, with `_brake` written on the draft's own document. `error: "draft_paused"`, `paused_until` an ISO instant, `paused_reason: "platform_fault"` -- our own bug, not the agent's -- and the whole body still in the one language, `form_steps` and all. |

## The removal patch was checked against the real door

The no-model fix for an `unknown_field` row (`draft.keys_to_take_off`) was run
against the bench's own PATCH route in process: both rows cleared, `applied`
named the path each time and nothing was left in the staging database. The step
kept every field the form reads and the wait kept its `days` and its
condition's `source` and `after_step`; only the keys the form has no room for
came off.
