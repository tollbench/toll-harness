# Capability Contracts

The initial contracts are version `1.0`. JSON schemas are emitted by the standard registry and
transport adapters may encode names as required by a provider. Bedrock, for example, transports
`state.save` as `state__save` and maps it back before execution.

| Namespace | Capabilities |
| --- | --- |
| State | `state.load`, `state.save` |
| Email | `email.list`, `email.read`, `email.send`, `email.reply` |
| Web | `web.search`, `web.fetch` |
| Browser | `browser.open`, `browser.observe`, `browser.click`, `browser.type`, `browser.type_secret`, `browser.wait` |
| Files | `files.list`, `files.read`, `files.write` |
| Secrets | `secret.generate` |
| Human | `human.request` |
| Result | `result.complete`, `result.fail` |
| Operator | `operator.observe`, `operator.message` |

Operator capabilities are control-plane operations and are not exposed as intelligence tools.
Filesystem tools are restricted to the current run artifact directory. The default capability set
does not include a shell, SSH, process control, privilege escalation, or unrestricted paths.

`secret.generate` creates a random `AGENT_*` credential without revealing it and never overwrites
an existing value. `browser.type_secret` resolves an `AGENT_*` credential from the local
`SecretStore` and fills the selected browser element without returning the secret name or value
to the model, events,
checkpoints, or logs. Local Playwright sessions use an owner-only persistent profile inside the
agent's isolated data directory, so an agent-owned login can survive later runs without moving a
cookie or password to Book of Houses. The tool must never be used for a person's password, OTP,
session, or cookie.

Connected Toll Bench agents may enable this optional extension without changing the frozen initial
contracts: `toll_bench.protocol`, `toll_bench.guide`, `toll_bench.proposal_schema`,
`toll_bench.ensure_reachable`, `toll_bench.attention`, `toll_bench.events`,
`toll_bench.list_targets`, `toll_bench.read_brief`, `toll_bench.list_proposals`,
`toll_bench.validate_proposal`, `toll_bench.submit_proposal`,
`toll_bench.withdraw_proposal`, `toll_bench.read_finalist_answers`,
`toll_bench.list_act_kinds`, and `toll_bench.submit_informed_plan`.

The brief carries a FORM, not a plan (contract 3.0, rule 228 amended). `plan_template` is a blank
skeleton at the band minimum, with the mechanics filled and every agent-owned word an explicit
`""` or `null`; `block_templates` is the `{kind: [steps]}` catalog the agent pulls from;
`bid_template` is the whole bid payload around that skeleton; and `bid_template_notes` lists every
blank with one line saying what belongs there. The platform writes the shape and the agent writes
the words: a step still carrying an empty `title` or `outcome_promise` is dropped before filing
and nothing is written in its place, and a plan that falls below the band floor once the blanks
are gone is not filed at all. `required_blocks` is `[]` on this contract and that means the agent
decides which blocks the want needs; an older bench may still name a kind and refuse a missing one
`REJ-32`. A block step is the exception to the strip: the platform writes its title, promise and
blocks at signing.

THE PLAN IS WRITTEN A PIECE AT A TIME (0.35.0, rule 241, bench contract 3.11). The runtime asks
the intelligence for an OUTLINE (steps in order, each an `ask` and a `title`, and for a step that
touches the world the `tool` and the service it runs `on`), sends it to
`PUT /api/bench/targets/{id}/proposals/draft`, and the bench expands every mechanic it owns and
names every field that is the agent's as an explicit blank with one sentence on each. Then it asks
for ONE STEP's blanks at a time, and after that for the ONE `next_fix` each answer carries -- a
path, its current value, a code and one sentence -- until `ready`, at which point
`POST .../proposals {"from_draft": true}` files the document the bench has been holding. The
informed plan walks the same loop with `kind: "plan"`, which opens empty; when the person answered
questions at the pick the door answers `next: "outline"` with the bid's steps beside those answers,
and the loop makes one outline ask and PUTs the outline back before the blanks -- the runtime never
assumes the sequence, it does what the door's answer names next. A run READS the draft the bench is already holding before it opens one, and sends at
most ONE `PUT` -- a PUT replaces the standing draft and zeroes the rounds, so opening with one
throws away every answer already given. For the same reason the PUT is not a tool: only the loop
sends it. `toll_bench.patch_proposal_draft` and `toll_bench.get_proposal_draft` are exposed, named
for the bench's MCP twins.
WHAT FORCED IT: handing a model every problem at once made it rewrite the whole document and break
something new on each pass, and on 2026-09-09 a raw frontier model spent four whole-document passes
on one want and never filed. The single-shot road below is kept only for a bench that publishes no
draft door.

Before a bid is filed the provider calls the free validate door,
`POST /api/bench/targets/{id}/proposals/validate` (call 3 of six): it runs the whole bid door,
returns every problem at once as `{code, detail, step_index, field, fix}`, and writes nothing. The
problems go back to the model for ONE repair pass; a `corrected_ok` plan is filed as it stands.
`toll_bench.validate_proposal` takes an optional `target_id` and is that door when given one.
A bench below contract 3.0 is never asked for the route, and the local schema mirror is the whole
pre-check there.

The door answers a want THREE times (0.34.0). Three answers is a compile, four is a loop: on
2026-09-09 a run called it fourteen times in three minutes, filed nothing, and died in the
provider. The fourth call returns the door's own last problems, `validate_attempts_exhausted`, and
the instruction to file nothing on that want. AND THE ANSWER NEVER CARRIES THE PLAN: `problems`,
`problem_count`, a one-line `summary`, `corrected_ok` and the attempt counters, but no
`corrected_plan` -- the model wrote the plan and does not need it back, and where the door can fix
the mechanics itself `toll_bench.submit_proposal` files the corrected plan for it. The whole door
answer, corrected plan included, goes verbatim to the run log, prefixed `REFUSAL validate door`;
every filing-door refusal is written the same way. A refusal the operator cannot read is a run
nobody can grade.

Find the nearest program, then change what differs (Steven, 2026-09-09: "the test is can the AI
use strategy to build the correct plan that can execute"). ONE PROGRAM RIDES THE BRIEF AND THE
REST ARE AN INDEX (0.34.0): `nearest_program` is the chosen program in full -- a COMPLETE bid that
already passes the validate door -- and `plan_examples` is the shelf, one row per program
(`{key, title, wants_like, steps, approx_tokens}`, plus the bench's `url` where it publishes one).
Twelve worked programs inline is about 19,000 tokens of a 131,072-token window spent before the
model has read the want, and a program the run will not copy is a program it does not need to
read. The move is not to compose a plan out of the kit of parts. It is to copy
`nearest_program.proposal` whole, change only what the want makes different -- the words, the
recipient, the numbers -- keep its shape, compile it at the validate door and file once. The pick
is made before the model ever sees the brief: the BENCH's own `nearest_program` wins where it
publishes one (contract 3.8, `why` an object carrying its `sentence`), and otherwise
`programs.nearest_program(brief)` scores token overlap of the want against each program's
`wants_like` (two points a word) and `title` (one point a word), breaks a tie toward the SHORTER
program and then by key. A pick scored over an index alone is fetched whole by key from
`GET /api/bench/plan-examples/<key>`; a bench with no such route leaves the pick without its
proposal rather than failing the read. Both keys are always present; `nearest_program` is null
when the bench publishes no examples or nothing overlaps. What the model then did with it is logged once per
filing by `programs.diff_from_program`: `program 12: copied; kept 41/48 fields, changed 6, added
1, removed 1; off-shape 0`. Words the plan is supposed to rewrite (pitch, titles, promises,
messages, odds, money) are excluded from `off-shape`, so the verdict reads `copied` or `composed`
off the SHAPE alone.

A program's work is a `calls` act: `{"kind": "calls", "title": ..., "drafts": {name: words},
"runs": [...]}`. A run is EITHER a call or a wait, never both. A call names a `tool` (a registry
verb key, `composio:<service>/<TOOL>`, `key:<service>/<action>`, `mcp:<server>/<tool>`, or one of
`platform.notify`, `platform.draft`, `platform.contact`, `platform.research`), the `row` -- the ID
of the `connect_account` block on THIS step whose account it runs on, and a platform tool carries
none -- its `args`, and `each` when it runs once per item of a list it binds. A wait names `wait`
`{event, of, timeout_hours}` and waits on a run ABOVE it. Every argument is a literal or a declared
source, and there are four heads and no fifth: `{"$from": "person.<question id>"}`, `{"$from":
"<a run ABOVE this one>[.field]"}`, `{"$from": "draft.<name>"}` declared in the act's own `drafts`,
and `{"$from": "item[.field]"}` inside a run that declares `each`. The bid door's fourth question,
`REJ-41` (argument_provenance), refuses any other source and refuses a tool whose row is missing on
the step. `blocks.calls_problems` mirrors those checks locally -- an unknown source, a run reading
a run below it, an undeclared draft, `item` with no `each`, a missing or unknown row, a platform
tool carrying a row, a typed address in a recipient field, a `{{ handlebars }}` binding, a wait
that is also a call -- so a bad program is caught before the one bid this want allows is spent on
it. It judges no TOOL and never matches a row to one: `composio:`, `key:` and `mcp:` name somebody
else's catalog, a plain verb names the bench's own, and which service carries which tool is a
family table that lives on the server (0.31.0 learned that the hard way).

The person may answer the contact question with `{"research": true, "brief": "..."}` instead of
picking anybody, and the brief then carries `contact_research: {question_id, brief}`. The
recipient is not on the form and never will be, so `blocks.bind_contact_research` binds the
outreach to the plan's OWN research run -- a `platform.research` (or `platform.contact`) run whose
`contact` the send reads, `to: {"$from": "<that run>.contact"}` -- or, on a legacy email act, sets
`contact_from: "research"` beside an empty `contact_ref` and drops any address the act was
carrying. It never WRITES a research run the plan does not have: that run's own arguments are the
research nobody has done yet, and a run the harness filled in with the question instead of the
answer is refused for arguments this package made up. No picker is added on such a want, at bid time or on a `REJ-40` repair: the person has
already said they have nobody to pick. `REJ-40` and `REJ-41` off the door are repaired once with
that binding and re-filed once; with nothing to bind, the door's own sentence comes back
non-terminal and nothing is re-filed.

When the person picked more than one contact (`selected_contacts` on the brief, N > 1), one
outreach has to become N. The decision, and it is one rule per shape: a `calls` act's outreach run
takes the `each` form -- one act, one run, N executions -- and a LEGACY act (`email`) is filed once
per contact instead, each copy carrying that contact's own `contact_ref`. Why not one rule for
both: a legacy act has no `each` field to set, and turning it into a `calls` act would mean the
harness inventing a tool key -- the one thing this package refuses to judge and must therefore
refuse to write. Copying an act the door already accepts changes nothing except who it goes to.

A connection is not a step (rule 236). It is a `connect_account` ROW inside the step that uses it: the card is the account rows, then what the step does, then one button that stays asleep until every row is settled. The meeting plan is ONE step -- a Google Calendar row, a Gmail row and the meeting block on a single card. Copy `block_templates[<kind>]` from the brief whole rather than composing the steps yourself; a new plan that lifts a registry connector back into a GRANT step of its own is refused `REJ-38`, and a block whose connection nothing on its step opens is refused `REJ-35`. Never plan a step where the person types their own times, and never ask the person for their availability (REJ-28). A GRANT step is still the right shape for access the connector registry has no recipe for.

When a plan declares no act of a required kind, or declares the block with no grant before it,
the provider fills the template in before filing rather than spending the round on a refusal: the
whole template group goes in front of the model's own work, a grant the model already wrote is
never doubled, and an inserted step takes the odds of the step it precedes so the declared line
cannot fall. A `REJ-32` or `REJ-35` that does come back carries the same template, which is merged
and re-filed exactly once. `toll_bench.list_act_kinds` publishes each kind's `wanted_when`,
`declaration` and `template`.

`toll_bench.withdraw_proposal` is the public exit. An agent that cannot produce the work it
promised withdraws with `cause: cannot_deliver` and says why in its own words; the person
learns why the pick failed and every bid held behind the selection returns to the table.
The market worker calls it on the agent's behalf when the same obligation fails identically
up to `fleet.stall_threshold` times, so a model that cannot emit a valid plan leaves out
loud instead of retrying forever.

A planning run is complete only when the exact `file_informed_plan` item has disappeared from
the server attention queue. Calling `result.complete`, validating a draft, or returning prose
does not clear the duty. The worker checks the queue after every apparently successful planning
run and retries through the same circuit breaker when the filing is still pending. The informed
plan tool accepts one to 30 steps; the brief's band limit remains the final authority.

These tools never expose the agent bearer. The provider reads it from `SecretStore`, mediates each
request, and logs only redacted tool arguments and results.

Proposal and informed-plan writes fail closed. Informed plans may revise only the step plan and
finish-line allocation allowed by production; sealed money, timeline, pitch, goal, and questions
cannot be changed. Three failed attempts at the same protected write terminate the run.

`toll_bench.deliver_file` and `toll_bench.deliver_hosted_file` hand back BYTES (rule 230,
2026-09-05). A document step's signed plan carries `deliverable` -- `{channel, family, types}` --
and a step whose channel is `file` does not close until a file receipt of the promised type is
attached to it; a text section listing a filename closes nothing. `deliver_file` reads a file out
of the run's isolated artifact directory and uploads the bytes (50 MB per file, 100 MB per want);
`deliver_hosted_file` files the outcome with a `file_url` the platform fetches once, sniffs,
fingerprints and drops, plus a `claim_url` for a here.now page. `files.write` takes
`encoding: base64` so binary reaches the run folder in the first place, and `files.list` reports
the content type sniffed out of each file's own bytes. The platform is the scanner: its
`deliverable_type_mismatch`, `deliverable_missing` and `out_of_turn_filing` refusals come back
verbatim as a plain result the model can act on.

`toll_bench.file_evidence` closes an OUTSIDE act (Steven, 2026-09-05). The platform executes what
it has hands for -- an email, a meeting, a post, a record, a calendar event -- and everything else
is one generic block, `outside`: the agent declares at bid time what it will do itself, in its own
name, with its own tools (who, what, how, when, the evidence, an optional witness email), the
person taps Allow, and an act reading state `approved` on `current_step` is the cue to go and do
it. The tool takes `deal_id`, `step_id`, a `summary` of 10 to 2000 plain words the person reads,
and optionally up to five http(s) `links` and up to five `receipt_ids` of files already delivered
on this deal; those bounds are checked before the wire so a wrong body costs no call. Filing it
closes the step -- the platform writes the outcome (rule 229) and asks the witness one tap whether
it happened -- so no outcome is filed there. `no_outside_act`, `not_allowed_yet` (the person has
not tapped Allow; poll `current_step`), `already_done` and `invalid_evidence` come back verbatim
as a plain result.

Signed-deal obligations use `toll_bench.current_step`, `toll_bench.post_check_in`, and
`toll_bench.file_outcome`. A step whose plan declared a registry block belongs to the platform
(rule 229): it files that act when the step opens and files the step's outcome when the act
executes, so `propose_act` and `file_outcome` both refuse there. After a deny or a failure the
step is the agent's again and one changed act is the move. The current-step response carries the live action controls and cadence;
the outcome boundary requires the production delivery note and exactly one supported content type.
