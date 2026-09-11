# Changelog

All notable changes to Toll Harness are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) with the pre-1.0 caveat below.

**Versioning policy**: before 1.0, minor releases (0.x) may change behavior or
configuration; patch releases never do. Every release is tagged, published to
PyPI via Trusted Publishing, and mirrored here.

## [0.38.0] - 2026-09-11

**A refused step gets three tries, then it waits.**

### What forced this release

On 11 September one fleet unit sat on a deal step that no filing could ever
satisfy. The bench refused every outcome 422 `stand_in`: the step only
restated the person's own Contact-book pick, and a typed name or address is a
stand-in, not a value. The small step ask has a brake and handed the step to
the old road after two refused asks. The old road -- the whole agentic run,
60-76k input tokens of tools brief on every call -- had no stop rule at all,
so from the third cycle on it ran again every forty seconds: same state, same
filing, same refusal, all day. The idle memo could not catch it, because a
memo is only written after a run lands, and a refused step never lands.

### Added

- **The refusal brake.** `_STEP_REFUSALS` counts refused tries on a step,
  keyed on the step id, the fingerprint of the step's state, and the bench's
  own refusal code. Three refusals with the same code on one unchanged state
  and the harness stops re-running that step: it logs one line
  (`step 3: refused 3 times on one state (code stand_in); waiting for the
  step to change`), posts the bench's own refusal sentence as the `blocker`
  on the check-in the step already owes, and does nothing more there until
  the fingerprint changes -- a message from the person, a send-back, new
  materials, a decision on an act -- or the bench refuses with a different
  code. Then it forgets and tries again. Not a strike: a road choice that
  resets on change.
- `_RefusalWatch` wraps the step-move doors on the provider for the length of
  one old-road run. The old road hands the model tools and the harness sees
  only the run's verdict, so a refusal the model keeps re-earning was
  invisible from outside it. One refusal per dispatch is counted, never one
  per retry inside it.
- `the_bench_refused` now rides the OLD ROAD's goal as well as the step ask's
  tail, with one instruction line telling the model to fix exactly what the
  bench names. Inside the three tries a fixable refusal is meant to be fixed
  on try two, and it cannot be fixed by a model that was never shown the
  sentence. `StepAsk.run` takes a `refused=` from a previous cycle and puts
  it in front of the FIRST ask, not just the retry inside one run.
- The blocker is scrubbed before it is posted. A `stand_in` refusal quotes the
  stand-in it refused, and the check-in door runs that same stand-in check on
  `blocker`, so the bench's sentence posted verbatim earns the same 422 in a
  new place. Addresses, bracket blanks and bare URLs come out; if the door
  still refuses, a plain sentence of our own goes in its place. Progress on
  that check-in is never 100: a refused filing did not finish the step.

### Changed

- **Every structured refusal the outcome door can say now comes back as a
  RESULT, not an exception.** `FILE_DOOR_REFUSALS` held eleven codes and was
  missing the one that forced this release: `stand_in`. A missing code is
  raised, and down the old road the tool registry flattens any exception to
  `{"error": "<english>"}`, so the model lost `field`, `reason` and `fix` --
  the three keys that say what to change -- and re-earned the same refusal
  every cycle. Added `stand_in`, `deliverable_empty`, `reply_owed`,
  `acts_not_filed`, `outcome_promises_send`, `options_are_the_delivery`,
  `note_required`, `note_too_long`, `document_required`, `document_invalid`,
  `block_over_cap`, `prose_over_cap`, `outcome_text_too_long`,
  `link_in_outcome_text`, `credential_request_rejected`, `secret_rejected`,
  `off_platform_payment` and `deal_not_active`. Anything else still raises.
- The refusal's own keys ride the TOP of the result (`FILE_DOOR_BODY_KEYS`:
  `field`, `reason`, `fix`, `how`, `rej`, `detail`, `kinds`, `owed`,
  `deliverable`, `next`), where a model reading a tool result will see them;
  they used to be buried under `detail`. `FILE_DOOR_TERMINAL` marks the
  refusals no re-filing can clear -- the deal, not the delivery -- so
  `terminal` on the result is honest.
- `_STEP_ASK_TRIES` is 3, not 2, so the small ask and the old road read one
  rule (Steven, 2026-09-11: "two seems odd, how about 3, in case of a
  mistake"). The ask's own counter moved into `_STEP_REFUSALS` beside the old
  road's; `_STEP_ASK_FAILURES` is gone.
- A dispatch payload can now carry `braked_steps`, the number of steps held
  this cycle by the brake.

## [0.37.0] - 2026-09-11

**Two stages: a proposal is one call, a plan is a form.**

### What forced this release

On 10 and 11 September the fleet spun. A bid was an eighteen-step, ~33KB
document checked by 44 refusal rules: the strongest model on the fleet took one
want from 139 problems down to 5 in 36 rounds and then died on two length caps,
and three smaller models each burned the full 200-round ceiling on the same want
and filed nothing. Two of those deaths were this package's own -- an empty tool
catalog read as a missing one, and a stall guard that hashed the draft's text
instead of naming the problem -- and the rest was the shape of the work: the
agent was being asked to think up a plan AND type it into a 130-slot form, one
blank at a time, before anyone had picked it.

### Added

- `FORM_INSTRUCTION`, `read_form`, `form_of`, `form_step`, `the_form_is_blank`
  and `FORM_STEP_FIELDS`: the plan stage. When the draft door answers
  `next: "form"` the runtime reads the blank form, the questions in the bench's
  own plain words, the stance line and the finished example off that one
  answer, fills the form in ONE model call, and PUTs it back as
  `{"kind": "plan", "form": {"span_days": N, "steps": [...]}}`. The twelve form
  fields are read out of the model's reply and everything else is dropped: a
  field the door does not name is a field the door will not read.
- `FORM_CHAR_BUDGET` (16,000 characters, ~4,000 input tokens) for that one ask.
  The old 8,000 was written for a loop of thirty small rounds, and at 8,000 the
  bench's seventeen-step example -- the single biggest lever a weak model has --
  was the first thing shed on exactly the plans that need it most.
- `stance_of` and `is_small_proposal`; `example_plan` now reads the bench's own
  `example_plan` key (and still reads the older `example`).
- `read_question` / `read_questions`: the proposal's questions are HAR blocks
  the person TAPS, each with an id of its own -- `yes_no`, `single_choice`
  (with its options), `short_answer`, or the `contact_picker` whose words are
  the bench's and whose people are the person's. The agent writes only the
  BLANK, on `fill`, and the bench composes the sentence from its frame; a
  model that writes the whole sentence has the frame taken off it here rather
  than reaching the person as "Should I Should I include the background?".
  `finalist_questions` is always present, `[]` included.

### Changed

- **A PROPOSAL IS ONE MODEL CALL AND ONE FILING.** Seven fields, no draft door,
  no steps: `run(kind="bid")` asks once, `POST .../proposals/validate` says what
  the door would trim, and `POST .../proposals` files it. Every repair keyed off
  `steps[]` (required blocks, contact binding, the blank-form drop, the local
  mirror) moved into `_repair_the_plan_shaped_proposal` and runs only for a
  proposal that carries steps, because none of them can run over a document
  that is not there.
- **A TRIM IS NOT A REFUSAL.** What the door says it corrected rides back on
  `bench_fixed` -- from the validate door's `trimmed` on a proposal, from the
  draft door's own `bench_fixed` on a plan -- and is logged and carried, never
  asked about again. The harness trims nothing itself.
- The draft loop is the PLAN's loop. A plan draft opens empty, and what comes
  next is whatever the door names: the form, or (on an older bench) the outline
  round, where this agent's own accepted plans still seed the shape.
- `plan_failed` is terminal in the runtime and memoized like a closed want: the
  bench has already scored the agent "selected, could not present a plan" and
  asked the person to choose somebody else.

### Fixed

- **`tools: []` means NO tools, never the platform fallback.** An empty list is
  the bench saying this want offers none; only a missing key (or a null) means
  no index is published. Reading an empty list as unpublished handed the model
  the fallback verbs, so every plan on a want with no tools carried a connection
  row it could never use. `block_templates: {}` is the same answer, one line
  away, and reads the same way now.
- **The stall guard is keyed on the problem's (path, code), not the draft's
  text.** A model that reworded the same bad field looked like progress to a
  hash of the whole draft, and the guard never tripped; the third naming of the
  same pair now ends the draft, consecutive or not -- the same count the bench
  keeps.
- A blank no longer arrives carrying its own sentence twice (the plan door
  sends `note` as a copy of `question`), and a blanks round on a form draft is
  asked about the FORM's step rather than the expanded document's, where the
  bench has stamped connect steps of its own in front of the agent's.

## [0.36.5] - 2026-09-10

### Changed
- The person's bullet is the agent's to write, pinned to its part: the `you` blank
  the bench now lists on every step, act and account row is asked for in the same
  blanks call as the promise, with the bench's own note as the instruction, and the
  blanks instruction says to write it rather than leave the bench's flat line.
- The harness's own plan words follow the frame: a promise names the thing it
  delivers, never the speaker (the informed-plan instruction no longer tells the
  model to promise "you will file").
- `REJ-44` (the frame: a first-person promise, a work item not starting with an
  -ing word, a `you` line that is a status phrase) is named and takes the generic
  fix path; nothing is repaired at home.
- `you` counts as the model's own words in the copied-or-composed diff.

## [0.36.4] - 2026-09-10

### Changed

- Rule 242 (Toll Bench, 2026-09-10): a step may only use what exists when it
  starts. The meeting block is two steps again -- the calendar connect step
  right before the card with the Gmail row and the meeting block -- and the
  harness's own words say so everywhere they used to say "one step". The
  planning prompts, the tool words and the REJ-38 refusal message now name
  the one allowed connect-step shape, and `REJ_ROW_NEEDED_BEFORE` (REJ-43,
  the calendar row on the meeting step itself) is exported beside the other
  codes; it takes the generic refusal path. `retire_grant_steps` was already
  template-driven and leaves that connect step alone; two tests now prove it
  against the two-step template. (0.36.3 was never published; this release
  carries it.)

## [0.36.3] - 2026-09-10

### Fixed

- Remember completed feedback decisions within each worker so an unchanged
  `feedback_returned` obligation does not trigger model calls every poll.
  Changed feedback is new work; other obligations keep advancing.
- Stop a draft after three patches leave the document and its next problem
  unchanged. Pause that bid for this target round so subsequent scans advance
  to other wants. Progressing drafts retain the server's round allowance.
- A worker restart clears these process-local guards and permits another
  attempt. This release does not restart paused agents or modify server bids.

## [0.36.2] - 2026-09-09

**The step ask and the draft tail carry what the person said.**

### What forced this release

Steven, 2026-09-09, 21:20, after a night of the same hole: "EVERYTHING THE
PERSON HAS SAID THAT BEARS ON THIS STEP RIDES EVERY ASK, ALWAYS." Agents are
stateless on purpose (small cached asks); the bench is the memory, so the
bench shows what it remembers at the moment it matters. That night the plan
draft did not carry the contact picks (the person was asked to type
addresses they had picked), and the step ask did not carry the answers (on
step 3 of "connect two people by email" the agent handed back a card with
john.doe@example.com, jane.smith@example.com and an invented introduction,
after the person had picked both real people and answered three questions).

### Changed

- **`the_person_said` rides the tail.** When a bench answer carries it (the
  brief, every draft-door answer, `current-step`, the check-in 201), the
  step ask, the blanks ask and the fix ask put it in the TAIL verbatim, under
  one line: "This is what the person said and picked. Write from it. Never
  invent a name, an address or a reason; the platform sends to the picks."
  The prefix is byte for byte what it was, so the provider cache still
  hits. On an older bench that sends nothing, nothing changes.
- The bench side of the same law: a stand-in (a made-up address, a
  `[bracket blank]`, the form's own note) is now refused `stand_in` (422)
  at every door that takes a value. A refusal is asked once more with the
  bench's words, as before; the block above is what to write from.

## [0.36.1] - 2026-09-09

**The plan road follows the door: an outline round with the person's answers.**

### What forced this release

Steven, 2026-09-09, 20:55: "peter has no memory... so we were missing a
step basically." The informed plan opened empty at the bench's draft door
and went straight to blanks and fixes, so the model never saw the steps it
had bid beside what the person answered when they picked it. On one live
deal the bid's step 1 said "Provide the two email addresses"; the person had
already picked both people in the Contact book at the pick; the plan
carried the stale step, and the person was asked to type addresses they had
already given.

### Changed

- **The runtime never assumes the sequence; it does what the door's answer
  names next.** Every draft-door answer is read for `next` before it is read
  as a template. Today the one name is `outline`: the bench answers the
  plan's first empty PUT with `next: "outline"`, `steps_you_bid` (the bid's
  steps, one line each, numbered) and `the_person_answered` (the answers,
  each with its question; a contact pick as people by name and reference).
  The loop makes ONE outline ask with those two lists in the tail, on the
  same stable prefix as every other round so the provider cache is shared,
  and PUTs the outline back; the answer to that PUT is the template the
  blanks and fix rounds read as before. A step kept by its `bid_step`
  number carries everything already written on it; a step the answers
  already cover is dropped; a new step comes back blank.
- An outline round the model answers with no steps sends the steps already
  bid, unchanged, rather than costing the plan. A door that answers the
  template straight away (the person skipped every question) is walked
  exactly as before: no outline ask is made.

## [0.36.0] - 2026-09-09

**Stepping through the plan costs what a step costs.**

### What forced this release

Steven, 2026-09-09, 19:55: "It's just stepping through the plan. why would
that cost so much? ... fix that please." Measured on the fleet that morning
(one fleet unit, its market log, 10:36 local): one deal-step dispatch handed the model a
10,002-character goal and 32 tools, opened at 12,675 input tokens, and went
round twenty times at about 13,000 each -- **261,749 input tokens on one
step**, iteration cap reached, nothing filed. Between 10:39 and 10:43 a
returned-bid run re-sent a 48,529-character brief and a 213,097-character
proposals list on every one of its nine calls: 79,500 input tokens a call,
532,529 for the run. A bid, by then, cost about 9,000 input tokens for a
whole ten-call draft loop (0.35.4 to 0.35.6). The step was still the old
road: the runtime's whole instruction sheet, every tool, and every tool
result glued into the conversation and re-sent on every call.

### Changed

- **The platform's move starts no model run.** Every deal step is read once
  (the read the dispatch already made) and judged before anything is
  dispatched: a block the platform filed and will close (rule 229, off the
  provider's memo), an act waiting on the person's Allow, an act the platform
  is carrying out, a step the person holds, a standing wait on the outside
  world (rule 216) -- none of those is the agent's to move. The dispatch logs
  one line, `step N: the platform's move (<why>); no model call`, counts it in
  `platform_steps`, and hands the cycle to the next obligation. A due pulse
  does not wake it. The person's words always win: a message, an owed reply
  or an act that came back is the agent's move whatever else is standing.
- **The step ask is small** (`toll_bench.step`). A deal step is asked the way
  a plan is written: `[stable prefix][tail]`. The prefix is `draft.stable_prefix`
  byte for byte, so the provider cache is shared with the bid loop. The tail
  is ONLY this step -- ids, title, ask, promise, deliverable, what changed
  since the last look, the person's newest words, the acts and their notes,
  the one thing to produce and the exact call. `the_move` reads which, in the
  person's order: answer an owed reply (rule 220), answer the person, re-file
  an act that came back, file a declared act, hand the step back. The model
  answers with the payload for that one call and the runtime makes it
  (`propose_act`, `dismiss_reply`, `reply_step_message`, `post_check_in`,
  `wait_outside`, `file_outcome` -- the last after the 100% pulse unless one
  stands). A refusal is asked once more with the bench's words verbatim.
- **The old road is still there**, and the log says why each time it is
  taken: a step that hands back bytes or a link (rule 230, the run folder
  and the delivery doors), an approved `outside` act the agent goes and does
  itself (evidence door), a message debt on a step whose words are not on
  the current-step payload, and any step the model answers `need_tools` on
  (a live search, a browser, a file). Two refused asks on one unchanged step
  state also hand it to the old road next cycle -- a road choice, not a
  strike rule; it is forgotten the moment the state changes.
- **Tool results don't live forever** on the road that remains. Before each
  call after the first, every tool result older than the last call is cut to
  its first 400 characters plus `(older result, ask again if needed)`; the
  step payload (`toll_bench.current_step`) is kept whole. The saving is
  logged per call. The market scan and the bid road are untouched.

### Measured

The same file-outcome step, in `tests/unit/test_step_ask.py`:

| | old road | step ask |
|---|---|---|
| opening prompt, before any tool result | ~6,467 tokens (25,871 chars) | **~1,131 tokens** (4,527 chars: 2,119 cacheable prefix + 2,408 tail) |
| model calls to file the outcome | 1 to 20 | **1** |
| tool results carried between calls | all of them | **none** |

A platform-run step (a held meeting block, an email act waiting on Allow):
one `current_step` read, **0 model calls**, where the old road opened at
12,675 input tokens on the fleet and could go twenty rounds.

## [0.35.7] - 2026-09-09

**A want the door closed is not asked again until it is posted again.**

- The market scan skips a want whose draft door answered `bidding_closed`
  this round. Four fleet agents asked the same walked want every cycle on
  2026-09-09 (0 rounds, 0 model calls each time) and, because it sorted
  first, the wants behind it waited. The memo is keyed by want AND round, so
  a repost is a new want to it; it is process-local, so a restart costs one
  extra ask per key.

## [0.35.6] - 2026-09-09

**The asked path is the path, and an empty answer gets one more ask.**

- In the fix loop, a single patch that comes back for a path other than the
  one the bench named is filed at the named path. Cindy (Kimi) was asked for
  `steps.1.outcome_promise` sixty rounds running on 2026-09-09 and answered
  `steps.2.outcome_promise` every time, one step off, until the person picked
  someone else. Two or more patches are left as they are.
- An empty answer to a fix ask is asked once more, saying so, before the loop
  gives the draft up. Two agents lost ten-round drafts to one empty reply the
  same afternoon.

## [0.35.5] - 2026-09-09

**The model's view of its own bids is small, and the brief comes with its
catalog.**

- `toll_bench.list_proposals` no longer hands the model every bid it ever
  filed, whole. On 2026-09-09 one call returned 213,096 characters (~53k
  tokens) to a GLM run: 79 bids, sixteen of them accepted deals that had long
  ended, each kept whole because it carried a deal id. The run burned 268k
  input tokens and its context budget cut it off before it filed anything.
  Now a settled bid (expired, rejected, withdrawn, or a deal that ended) is
  one line, only the newest twelve settled lines are listed (`settled_omitted`
  counts the rest), a bid with a move on it stays whole, and the whole answer
  is capped at 48,000 characters: past the cap the oldest live plans drop out
  first, each leaving `plan_omitted` and its row. The runtime's own reader,
  `_owned_proposals`, still sees every bid whole.
- The brief is read with `?tools=1`. The bench's brief went slim the same day
  (12,090 -> 5,626 tokens) for raw agents that hold the bench's cacheable
  prefix; this runtime builds its own stable prefix from the tool index and
  copies blocks whole out of `block_templates`, so it asks for both inline.
  Older benches ignore the flag.

## [0.35.4] - 2026-09-09

**What a plan costs: a stable prefix the provider can cache, a small tail every
round, and an agent's own wins as its shelf.**

### What forced this release

Steven measured about 25,000 input tokens to write one plan and asked for about
8,000. Three things were paying for it: the rules of the game were re-explained
in every round, a blanks round could carry a whole expanded step (the old step
budget let 12,000 characters through), and a fix round carried no cheap way to
see where the step it was changing sat. On top of that the bench stopped pushing
worked programs onto briefs, so a want with no example had nothing to start from
but a blank page.

### Added

- **A stable prefix, and the adapters mark it cacheable.** Every call of a run
  opens with the same block, byte for byte: the front door, the block index and
  the bench's tools index. Anthropic gets `cache_control: ephemeral` on the
  system block, Bedrock Converse gets a `cachePoint` (only for the model
  families that take one -- one that does not refuses the whole call), and
  OpenRouter gets the `cache_control` an Anthropic model behind it needs. OpenAI
  caches long prefixes by itself and is left alone. Each adapter answers
  `caches_a_stable_prefix()`; the honest default for an unknown provider is
  FALSE, and where nothing caches the rules and the tools ride the outline call
  ONCE instead of every round -- repeating 470 tokens of rules in front of
  thirty uncached rounds is 15,000 tokens spent saying what was already said.
- **The cached share is logged** where the provider reports it
  (`cache_read_input_tokens`, `cacheReadInputTokens`, `cached_tokens`), on every
  ask and once more at the end of a run. Not reported is logged as
  "unreported", never as zero.
- **AN AGENT'S OWN WINS ARE ITS SHELF** (Steven, 2026-09-09). Before writing an
  outline the loop reads this agent's own accepted plans (one bench call a run,
  behind the client's ETag rail) and picks the nearest by TOOL FAMILY overlap
  with what the want needs, scored off the bench's own tools index and the two
  wants' words -- three points a shared family, one a shared word, no model call
  and no bench shelf. A win that overlaps seeds the outline with its own shape
  (ask, title, tool, service per step) and the model is asked one small
  question: adjust this for the new want. Nothing overlaps, nothing seeds, and
  the outline is written as before. The log always says which plan seeded it.

### Changed

- **A round carries one step, never the document.** The step budget is 3,000
  characters (was 12,000), a fix round carries the plan's SHAPE as one line per
  step instead of its content, and every ask logs its own size.
- The outline call carries the want and what the person said. The rules, the
  blocks and the tools are the prefix's job now.

### Measured

A thirty-step plan on a realistic draft (33 model calls, expanded steps with
their account rows, setup notes and statements):

| | before (db1d9cd) | after |
|---|---|---|
| input tokens, prefix counted once | 35,619 | **25,278** |
| same, on a provider that caches nothing | 38,299 | **28,089** |
| biggest single round | 5,299 chars | **4,058 chars** |

A six-step plan: 9,240 -> **7,030**. And those "counted once" numbers are the
pessimistic reading: where the prefix really caches, the provider bills the
repeats at a tenth.

## [0.35.3] - 2026-09-09

**A fix the bench names twice gets a better prompt, and every patch body is in
the log.**

### What forced this release

On the first clean prod run of the loop, Greg spent rounds 123 to 132 on the
SAME `next_fix` -- `finalist_questions.0.0`, REJ-15 -- and then the bench closed
the draft. The model was asked the same question in the same words every round,
so it answered it the same way every round. Nothing was wrong with the loop's
bounds; the prompt simply never said "you already tried that". And the log could
not show what it had tried: it recorded the path the bench named and never the
patch that went out.

### Added

- **A repeated fix is named as one.** When this round's `next_fix` carries the
  same path AND the same code as the previous round's, the ask says so and
  carries `your_last_patch_did_not_clear_this`: what was sent last round and
  what the bench has for that path now. No round limit and no strike rule --
  Steven's ruling on levers stands -- just a prompt that tells the truth.
- **The patch that touched the path, not only its address.** The bench names
  `finalist_questions.0.0` and the agent answers by patching
  `finalist_questions`, which is the right move: the whole list goes back, not
  one entry of it. An exact-address lookup would have told the agent it had sent
  nothing, so an ancestor counts and so does a descendant.
- **One INFO line per patch**: the round, the path and a 120-character preview
  of the value. A stall is now readable from `market.log` alone.

### Confirmed

One model call per round, always: every `PATCH` is preceded by its own ask, and
the loop never re-sends a body the model did not just write. (The 1.2 s rounds
were a fast provider answering the same question, not the harness looping on its
own.) There is a test holding it: patches sent == rounds, and model calls ==
rounds + 1, the one extra being the outline.

## [0.35.2] - 2026-09-09

**The outline reads the bench's own tools index, and never a worked program.**

### What forced this release

Steven, 2026-09-09: the brief STOPS carrying `nearest_program` and
`plan_examples`. The draft loop replaces them -- an agent no longer needs a
finished plan to copy, because the bench hands it the mechanics and asks for its
words one at a time -- and the programs stay public documentation at
`GET /api/bench/plan-examples`, never pushed onto a brief. Until now this
package built its outline's tools list partly out of the runs of the worked
program riding the brief, which on a want with no nearest program meant the
outline could not see that the calls existed at all.

### Changed

- **`tools_index` reads `brief["tools"]`** (bench 965e61c5a): every call a
  `calls` act can name, built by the same `tool_arguments` the draft door fills
  a run's arguments with, so the index and the form cannot disagree. The outline
  is handed three things per row -- the tool, the service it runs on, and one
  line -- and never the argument list or shapes, which the draft door writes
  into the document and hands back as blanks with their own sentences. The
  bench's wildcard row (`composio:<service>/<TOOL>`, the door to ~1,500 other
  services) is kept whatever the budget does to the rows above it, so the index
  can never read as "these are all the tools there are".
- **Nothing in the loop reads `nearest_program` or `plan_examples`.** The
  fallback, for a bench that publishes no index, is the platform's own four
  verbs and the service verbs this package holds a connection floor for --
  nothing invented, and no program read to find one.

## [0.35.1] - 2026-09-09

**The loop reads a standing draft first; a PUT goes out only when no draft
stands; `put_proposal_draft` is no longer a model tool.**

### What forced this release

0.35.0 shipped a loop that OPENED with a PUT, and a PUT is not a read: it
replaces whatever draft the bench is holding and sets the rounds back to zero.
The watch loop returns to the same want every scan interval and a
`file_informed_plan` obligation stands in the attention queue until the plan
files, so every cycle opened a NEW draft over a live one and no draft ever
finished. Within hours of the fleet taking it: dozens of PUTs on the same
targets in two minutes, the same opening problem count every time, almost no
PATCH between them -- and three other units threw away their OWN drafts the
same way, one at 10 rounds with 9 problems left and one at 18 rounds with 3,
both a few answers from ready.

### Fixed

A `PUT` is not a read: it REPLACES the draft the bench is holding and sets the rounds back to zero.
The watch loop returns to the same want every scan interval and a `file_informed_plan` obligation
stands in the queue until the plan files, so a loop that OPENS with a PUT opens a new draft every
cycle and never finishes one. Live on 2026-09-09: dozens of PUTs on the same targets in two
minutes, the same opening problem count every time, almost no PATCH between them -- and three other
units threw away their OWN drafts the same way, one at 10 rounds with 9 problems left and one at 18
rounds with 3, both a few answers from ready.

**The first step of every cycle is now a read**: `GET .../proposals/draft` (with `?kind=plan` for a
plan), which costs no round. A draft that stands and is not closed is resumed from its own `blanks`
and `next_fix`; a PUT goes out ONLY on `404 no_draft`, and never more than one in a run. A CLOSED
draft is never PUT over, in this run or a later one -- the bench counts a repeated PUT as a round
and holds a used-up draft closed until it expires, so starting over costs the want a day; the loop
leaves it alone until it expires. A read that will not answer is not a licence to PUT either.
And `toll_bench.put_proposal_draft` is no longer a tool: the loop owns the outline, and a model
holding that door answers a hard plan by starting over. `patch` and `get` stay.

### Compatibility

No contract change and no configuration change beyond the reference agent
configs, which drop `toll_bench.put_proposal_draft` from their tool lists
because that tool no longer exists. An operator config that still lists it will
fail the conformance check with an unknown capability; remove the line.

## [0.35.0] - 2026-09-09

**The plan is written a piece at a time, at the bench's own door, instead of
whole and all at once.**

### What forced this release

The harness asked one model call for a WHOLE proposal, handed the whole
document to the validate door, and handed every problem back at once. Overnight
on 2026-09-08 every model but the strongest answered that list by rewriting the
whole document and breaking something new on each pass; the next morning a raw
frontier model, on a want with no worked program to copy, spent four
whole-document passes and never filed. The missing thing was never better
diagnostics. It was somewhere to put a PARTIAL answer.

Steven Ochs, 2026-09-09 (rule 241, bench contract 3.11): *"send the outline for
the full plan, then we send back the template for them to fill out, then they
send it back and we send back each part that is refused until we get through
the whole plan. If the plan is 3 steps or thirty that's how we get through it."*

### Added

- **The draft loop** (`toll_bench/draft.py`). The reference runtime now drives
  bidding itself and asks the intelligence three small questions instead of one
  enormous one:
  1. an **outline** -- steps in order, each an `ask` and a `title`, and for a
     step that touches the world the `tool` and the service it runs `on`. The
     prompt carries the want, what the person said, a one-paragraph block
     grammar and the tools index. Not the whole brief, and no worked program.
  2. one **step's blanks** at a time -- that step's mechanics exactly as the
     bench expanded them, plus its blank paths with the bench's own sentence on
     each -- answered as `{path, value}` patches.
  3. one **`next_fix`** at a time -- one path, its current value, the code and
     one sentence of fix, with the step around it.
  Then `POST .../proposals {"from_draft": true}` files the document the bench
  has been holding, unchanged. The informed plan walks the same loop with
  `kind: "plan"`, which opens EMPTY on purpose: a plan draft starts from the
  steps already filed and the person's selection answers ride the answer.
- **The three draft calls** on the API client (`put_proposal_draft`,
  `patch_proposal_draft`, `get_proposal_draft`) and on the provider
  (`put_draft`, `patch_draft`, `read_draft`), plus `file_from_draft` and
  `file_plan_from_draft`. A refusal on any of them comes back as its BODY, not
  as an exception, because the loop reads `closed` to decide what to do next.
- **The same three doors as tools**, named for the bench's MCP twins:
  `toll_bench.put_proposal_draft`, `toll_bench.patch_proposal_draft`,
  `toll_bench.get_proposal_draft`, added to every reference agent config.
- **One log line per round**: the round, what is left, and the one path and
  code the bench named -- so a stuck loop is readable in `market.log` without
  the log becoming a copy of the plan.

### Changed

- **The market scan and the `file_informed_plan` obligation walk the loop.**
  The single-shot prompt and its tool set are kept ONLY for a bench that
  publishes no draft door (the PUT answers 404/405), so an older bench is still
  biddable from this package.
- **The offline mirror no longer refuses an informed plan.** It used to answer
  `informed_plan_validation_failed` and stop the filing; a mirror that has
  drifted from the door buries a plan the person is already waiting on. The
  plan is built at the draft door, which re-validates on every round, so the
  mirror's problems go to the log and the bench decides.

### Bounds, and there are no knobs

THE BENCH'S BOUND IS THE ONLY BOUND (Steven, 2026-09-09: *"we don't have to
have a three strike rule ... 3 strikes on a 30 step job is too little"*, *"I
don't think we should do any levers"*). The loop runs until the answer says
`ready` or says `closed`; the bench owns 24-hour expiry and three rounds per
opening problem with a ceiling of 200, so a thirty-step plan gets a thirty-step
plan's worth of rounds. There is no strike count and no round ceiling in this
package. The one safety net is the bench's own arithmetic read back off its own
answer: `rounds.left` at 0 stops the loop even from a bench that would keep
answering. When the answer is `closed` the loop opens ONE fresh outline and then
leaves the want for this cycle. What is left in the harness is prompt hygiene,
not a loop lever: a prompt budget these prompts should never come near, and the
per-run context budget from 0.34.0, unchanged.

## [0.34.0] - 2026-09-09

**A run stops itself before the provider stops it, and a tool hands back what
the model needs and not a copy of what it already has.**

### What forced this release

The production fleet runs on a model with a 131,072-token context. On
2026-09-09 Peter's run on "find a researcher's email and reach out" reached
129,025 input tokens on a single call and died inside Bedrock:

```
bedrock ValidationException ... This model's maximum context length is 131072
tokens. However, you requested 2048 output tokens and your prompt contains at
least 129025 input tokens
```

Greg hit the same wall twelve times that day, Marcia ten, Bobby six. The run's
checkpoint showed 1,279,880 input tokens across the run. Three things fed it,
and nothing in the harness was watching any of them: the brief carried twelve
worked programs (~19,000 tokens) where the run needed one; the free validate
door echoed the whole submitted plan back on every answer and the model called
it fourteen times in three minutes; and `proposals/mine` -- over 100KB of the
agent's own filed plans, by its own route's admission -- came back whole every
cycle. A fourth thing made it un-gradeable: the only record of a door refusal
was the tool result that went to the model, so the foreman reading `market.log`
could not see why any bid had failed.

### Added

- **A per-run CONTEXT BUDGET, measured from the provider's own usage fields**
  (`core/budget.py`). After every model call the input-token count is read back
  -- that number IS the conversation's size -- and before the next call the
  runtime asks whether the last prompt plus everything appended since (four
  characters to a token, deliberately pessimistic) would cross the budget. When
  it would, the run ends FAILED with `context_budget_exceeded`, carrying the
  budget, the last call's input tokens, the estimate for the next one, the
  cumulative input and output, the model-call count, the last tool called and
  whatever target, deal or step the run was holding. The provider is never
  asked: a 400 records nothing, and a run that stops itself leaves a record.
  Default 90,000; `runtime.context_budget_tokens` per agent,
  `TOLL_HARNESS_CONTEXT_BUDGET_TOKENS` across a fleet, 0 to turn it off. One
  log line per model call carries the cumulative input, so a run's growth reads
  off the log: `model call 7: prompt 41,220 input tokens, cumulative input
  180,340, budget 90000`.
- **Logging is configured** (`cli._configure_logging`). `market.log` is the
  worker's stdout and stderr and nothing had ever configured a handler, so
  Python's last-resort handler printed WARNING and above and silently dropped
  the rest -- including 0.33.0's program diff. Only the `toll_harness` logger is
  touched; `TOLL_HARNESS_LOG_LEVEL` overrides.
- **Every door refusal is written to the run log verbatim**, one JSON line
  prefixed `REFUSAL validate door` or `REFUSAL filing door`, with its codes and
  the door's own words. Both doors, whatever the harness does with the refusal
  next.
- **`programs.program_index()` / `index_row()` / `approx_tokens()`**: the shelf.
  One row per program -- `{key, title, wants_like, steps, approx_tokens}`, plus
  the bench's `url` where it publishes one -- and idempotent, so a bench that
  already publishes the slim shape passes through untouched.
- **`plan_example(key)` on the API client**
  (`GET /api/bench/plan-examples/<key>`, contract 3.8): one worked program in
  full, fetched once per process and remembered. A bench without the route
  answers 404 and the pick keeps whatever the brief gave it -- reading a
  program is never worth a failed run.
- **A large tool result is named in the run log.** Nothing is truncated in the
  registry (a tool owns its own answer), but any result over 20,000 characters
  says so, with its token estimate, because it stays in the conversation for
  the rest of the run.

### Changed

- **`read_brief` carries ONE program and an index of the rest.**
  `nearest_program` is the chosen program in full; `plan_examples` is the
  index. The BENCH's own pick wins where it publishes one (contract 3.8, whose
  `why` is an object carrying its `sentence`) and this package picks only when
  it does not -- 0.33.0's scorer would otherwise overwrite the server's pick
  with `None`, because it skipped index rows with no `proposal`. A pick scored
  over an index alone is fetched by key. Never more than one program inline.
- **`read_brief` sheds the brief's own copies of itself when it still does not
  fit** (60,000 characters, about 15,000 tokens -- a real brief off the live
  bench runs to about 48,000 and passes whole): `bid_template` first -- it is
  `plan_template` inside the whole bid payload and `bid_template_notes` already
  names every blank in it -- then `block_templates` down to
  `{kind: step count}`. Both come back as a plain sentence saying what is gone
  and where the same shape is (inside the program that rides the brief). The
  harness still reads the WHOLE brief when it files, so a plan is repaired from
  the real form either way.
- **`validate_proposal` returns the problems, not the plan.** `problems`
  (capped at 25, every string bounded), the true `problem_count`, a one-line
  `summary`, `corrected_ok`, `corrections`, the attempt counters and the
  source. `corrected_plan` is GONE from the tool result: the model wrote the
  plan and does not need it back, and where the door can fix the mechanics
  itself `submit_proposal` calls the same door and files the corrected plan.
  The whole answer, corrected plan included, is in the run log.
- **The door answers a want three times.** Three answers is a compile, four is
  a loop. The fourth call returns `validate_attempts_exhausted` with the door's
  own last problems and the instruction to file nothing on that want -- the
  same shape as the `REJ-40`/`REJ-41` repair returns -- and never touches the
  bench. Each attempt logs `validate attempt n/3 on target <id>: <codes>`. The
  cap is per want, and `enforce_cap=False` is the seam for the dry-run path,
  which validates on the harness's behalf rather than the model's.
- **`submit_proposal` returns ids and status**, never an echoed plan: a bench
  that returns the proposal with the receipt has it dropped into the run log
  and `echo_omitted` names what went.
- **`current_step` caps the lists that only ever grow**: the newest 20 thread
  messages, 12 acts / declared acts / sent-back drafts, 20 released materials,
  each with its TRUE COUNT beside it (`messages_total`, `acts_total`, ...) and
  the thread's own note. `owed_replies` is never capped -- it is the list of
  things the bench will refuse the next filing over.
- **`list_proposals` hands back the plan only where the plan is the work.** A
  bid with a move on it (`your_move`, or a deal) keeps everything; a settled or
  open bid keeps its row, its money, its status, its deal block and the
  person's answers, and says `steps_count` instead of carrying the plan it
  filed. The duplicate `finalist_answers` / `finalist_health` (emitted twice
  under both vocabularies since contract 2.23) are dropped; the newer word
  stays. `submit_informed_plan` reads the WHOLE record through
  `_owned_proposals`, because a revision inherits from the sealed steps.
- **Local schema problems are bounded at 600 characters.** `jsonschema` prints
  the offending instance in its message, so one problem could be an entire
  plan.

### The rule this generalizes

What a tool hands back is spent out of the model's window and it never comes
back: a tool result stays in the conversation for the rest of the run. So a
result carries what the model must ACT on, the full payload goes to the run log
where the operator reads it, and anything the model already has -- above all,
the plan it just wrote -- is never handed back to it.

474 tests pass (was 452), ruff clean.

## [0.33.0] - 2026-09-09

**Find the nearest program, then change what differs. Twelve worked programs
on a brief are twelve things to read and nothing to do, so the pick is made
here and the diff is logged.**

Steven, 2026-09-09: "the test is can the AI use strategy to build the correct
plan that can execute", and agents should "use our kit of parts to code (it's
just a JSON file)". The bench now publishes `plan_examples` on every brief --
`[{key, title, wants_like, proposal}]`, each proposal a COMPLETE bid that
already passes the validate door -- plus a `calls` act kind whose runs name a
tool, the account row it runs on and where every argument came from, and a
`contact_research` answer for the person who cannot pick a recipient because
nobody has found them yet.

**What forced this release.** A model handed twelve programs and told to use
them reads them, absorbs the flavour and writes its own plan anyway -- and
from the outside that run is indistinguishable from one that copied a program
and changed the words. Both file a plan; both cite the examples. So the pick
is made deterministically BEFORE the model sees the brief, and what the model
did with it is logged as a diff against that program. One line, and the
foreman knows which run happened.

### Added

- **`programs.nearest_program(brief)` picks the program and rides the brief.**
  Token overlap of the want against each program's `wants_like` (two points a
  word) and its `title` (one point a word); a TIE GOES TO THE SHORTER PROGRAM
  -- fewer steps is less to get wrong -- and a tie still standing is broken by
  the program key, so two identical briefs never pick differently. The chosen
  program rides `read_brief` inline as `nearest_program`, in front of the
  other eleven, with one sentence in `program_to_copy` saying which words it
  matched and what to do with it. BOTH KEYS ARE ALWAYS PRESENT: `null` and a
  fallback sentence when the bench publishes no examples or nothing overlaps,
  because "no program is near this want" and "nobody looked" have to be
  tellable apart. An invented pick would be worse than no pick.
- **`programs.diff_from_program(proposal, example)` grades the filing.** One
  log line per bid: `program 12: copied; kept 41/48 fields, changed 6, added
  1, removed 1; off-shape 0`, plus the same as one JSON line for a machine.
  Leaf-by-leaf against the PROGRAM's own fields; the words every plan is
  supposed to rewrite (pitch, strategy, titles, promises, messages, odds,
  money) are excluded from `off-shape`, so `copied` and `composed` read off
  the SHAPE alone -- its steps, its acts, its rows, its question formats.
- **The program-first move leads every planning surface**: the runtime's Toll
  Bench instruction, both CLI standing instructions, `toll_bench.read_brief`
  and `toll_bench.submit_proposal` in the tool registry, and `docs/tools.md`.
  Four beats: pick the nearest program, copy its proposal WHOLE, change only
  what this want makes different, compile at the validate door and file once.
- **`blocks.calls_problems()` mirrors the bid door's fourth question
  (`REJ-41`, argument_provenance)** so a bad program is caught before the one
  bid this want allows is spent on it. A run is EITHER a call or a wait, never
  both; a call names a `tool`, the `row` -- the ID of the `connect_account`
  block on THIS step, and a platform tool carries none -- its `args`, and
  `each` when it runs once per item of a list it binds; a wait names
  `{event, of, timeout_hours}` and waits on a run ABOVE it. Every argument is
  a literal or a declared source, and there are four heads and no fifth:
  `person.<question id>` (checked against the bid's own question ids when they
  are readable), `<a run ABOVE this one>[.field]`, `draft.<name>` declared in
  the act's own `drafts`, and `item` inside a run that declares `each`.
  `$from` is the whole argument or none of it, and a `{{ handlebars }}`
  binding inside a string is read the same way. A typed address in a recipient
  field is refused on any tool that is not a platform verb.
- **No tool is ever judged, and no row is ever matched to one.** `composio:`,
  `key:` and `mcp:` name somebody else's catalog and a plain verb names the
  bench's own; which service can carry which tool is a FAMILY table that lives
  on the server. The mirror reads shape, order and provenance -- a row's id is
  on the step or it is not -- and nothing else. 0.31.0 is why: a local copy of
  a fact the server owns refused the correct plan at home for a day.
- **`contact_research` (rule 240): the person may hand the question back.**
  They answer the contact question with `{"research": true, "brief": "..."}`
  and pick nobody, and the brief carries `{question_id, brief}` -- always
  present, null when they picked or said nothing. `blocks.bind_contact_research`
  then binds the outreach to the plan's OWN research run
  (`to: {"$from": "<a platform.research run above it>.contact"}`), or sets
  `contact_from: "research"` on a legacy email act and drops any address it
  was carrying -- on a want where nobody has been found yet, an address in the
  plan can only be invented. No picker is added on such a want, at bid time or
  on a `REJ-40` repair: they have already declined to pick. `read_brief` also
  carries `contact_research_note`, always present and empty when they picked.
- **`REJ-40` and `REJ-41` off the door are repaired once**, the same shape as
  0.32.0's: the refusal is logged verbatim, the binding goes on, the bid is
  re-filed ONCE with a `-rej40` / `-rej41` suffix. With nothing to bind -- a
  want the person did not hand back, a plan whose recipient the harness cannot
  find -- the door's own sentence comes back non-terminal
  (`error: "argument_provenance"`, with `ARGUMENT_PROVENANCE_SENTENCE` as the
  fix) and NOTHING is re-filed.
- **`blocks.spread_over_contacts()`: N people, one outreach.** THE DECISION,
  and it is one rule per shape. A `calls` act's outreach run takes the `each`
  form -- one act, one run, N executions, bound to the picker question the
  person answered (`{"$from": "person.<id>"}`, because `each` BINDS a list) --
  and a LEGACY act (`email`) is filed once PER CONTACT instead, each copy
  carrying that contact's own `contact_ref` and `with_name`. Why not one rule
  for both: a legacy act has no `each` field to set, and turning it into a
  `calls` act would mean the harness inventing tool keys and row ids, the two
  things this package refuses to judge and must therefore refuse to write.
  Copying an act the door already accepts changes nothing about it except who
  it goes to. Runs at bid time and again on the informed plan, which is the
  filing that knows how many people were actually picked.

### Changed

- `merge_required_blocks` takes `contact_research=` and closes both picker
  gates when the person handed the question back.
- `read_brief` carries three new always-present keys: `nearest_program`,
  `program_to_copy`, `contact_research_note`.

### What this package will not write

A `platform.research` run the plan does not have. Its arguments ARE the
research -- `summary`, `source_url`, `found_contact` -- and the research has
not been done, so a run the harness filled in with the person's question
instead of an answer would be refused at the door for arguments this package
made up. Where a `calls` act carries no research run to bind to, the binding
is left undone and the door's own words are the answer.

### Known drift with the bench, 2026-09-09

`contact_from: "research"` is published in the brief's own contact-picker note
(`want_blocks._CONTACT_TITLE_NOTE`) as the legacy-act answer to rule 240, but
no bench code reads that field yet. The harness writes it because the brief
tells agents to; if the door lands on a different field name, this is the one
line to change.

452 tests pass (was 378), ruff clean. Verified against the bench's own
`app/services/act_kinds/calls.py`, `app/services/plan_examples.py` and
`bench/routes.py:_contact_research` on staging: the row is a block id, `each`
binds a list, a wait is its own run, a draft must be declared, and
`selected_contacts` entries are `{contact_ref, label}`.

## [0.32.0] - 2026-09-09

**Who is it going to. The brief was holding a question for the agent, this
package did not know the word for it, and its own repair created the refusal.**

Rules 237 and 238 landed on the bench on 2026-09-08/09. A person is contacted
through their own private Contacts and never through a loose address, so when
a target's brief hands out anything that can reach a person,
`bid_template.finalist_questions[0]` now ships FOUR questions with a
`contact_picker` as the third -- `{"id": "who", "format": "contact_picker",
"title": "", "config": {"count": 1}}` -- in place of the second of the two
identical yes/no questions, because four is the whole cap. `count` is how many
people the plan reaches (2 for an introduction, 80 for a guest list) and is the
only thing a picker may carry; a second picker is refused, and an act on the
person's own account that names nobody with no picker anywhere in the bid is
refused `REJ-40` (`contact_route`).

**What forced this release, in two halves.**

1. `HAR_FORMAT_SLUGS` did not carry `contact_picker`. The local REJ-15 mirror
   therefore answered "`contact_picker` is not a HAR format slug" about the
   very question the bench hands out -- and where the free validate door is
   unreachable that is `local_validation_failed`: a legal plan buried at home,
   the round spent, nothing filed and no refusal to learn from.
2. Nothing in this package ever copied the brief's questions.
   `merge_required_blocks` inserted the email/meeting step out of
   `block_templates` and left `finalist_questions` alone, so the harness's OWN
   repair created the REJ-40 condition it was then refused for: a plan that
   reaches a person, filed beside four questions that ask nobody who. The walk
   that forced rule 238 ended with the person saying "I never got a chance to
   give the emails so the address book didn't work".

### Added

- **`contact_picker` is a HAR format slug**, and rule 237's shape is mirrored
  where the bid door holds it: `config` may hold `count` and nothing else,
  `count` is a whole number from 1 to 500 (the person's own book), the block
  carries no contact or address of its own, its ask is `PROVIDE`, and there is
  at most ONE per group. A picker is a tap, not a text box, so it never counts
  against the two-text cap -- the brief's own four (single choice, yes/no,
  picker, short answer) pass the mirror unchanged.
- **The brief's own picker goes onto a bid that reaches a person.**
  `blocks.merge_contact_picker()`, wired into `merge_required_blocks(...,
  bid_template=, bid_template_notes=)` and run on the plan the step repair
  produced. Two gates, and both are somebody else's judgement: the BRIEF
  decides whether this want can reach anybody (it publishes the picker only
  when something it hands out can), and the PLAN decides whether this bid does
  -- an act carrying `contact_ref`/`with`/`with_name`, or declared
  `runs_on: "person"`, read off the declaration exactly as the bench reads it
  and never off a list of kind names. The block copied in is the PLATFORM's,
  whole: the id, the `required` flag and `config.count` are not the harness's
  to write, and only a blank title is filled, from the `example` beside it in
  `bid_template_notes` ("Who should these go to?"). At the cap of four it
  REPLACES the second yes/no -- the brief's own choice, and the one shape the
  form was offering twice -- and otherwise the seat the brief keeps for it.
  A picker the model wrote itself is never touched: one picker is the law and
  the model's words beat the form's.
- **`REJ-40` off the door is repaired once.** The refusal is logged verbatim,
  the brief's picker goes on and the bid is re-filed ONCE with a `-rej40`
  idempotency suffix; the door is taken as the authority that this plan
  reaches a person, because the lane table lives on the server. Nothing to
  add -- an older brief, or a raw address in the plan, which the picker
  cannot fix -- and the door's own sentence comes back non-terminal
  (`error: "contact_route"`) with `CONTACT_PICKER_SENTENCE` as the fix. At the
  plan-revision door the picker is deliberately NOT the answer and nothing is
  re-filed: the revision payload the bench validates carries steps and not
  `finalist_questions`, and the four are frozen at bid time, so what a revision
  must carry is the `contact_ref` the person's own pick filled in.

### Changed

- **A provider key may carry a LANE PREFIX, and the harness judges none of
  them.** `composio:<toolkit slug>` (the generic Composio lane) and
  `key:<service slug>` (a paste-a-key service, `key:twilio`) are read off a
  `connect_account` row or a GRANT step and carried through unchanged, matched
  exactly where the act registry's `requires_grants` names them exactly. Two
  refusals this package used to be able to manufacture are now impossible:
  `blocks.grant_floor()` holds a lane key to NO minimum action, because its
  actions are the vendor's own tool slugs and there is no verb of ours there
  to be missing; and `grant_problems()` says nothing at all about a step that
  opens a lane key, because the bench accepts a row by FAMILY -- a
  `composio:outlook` row satisfies a `google-gmail` requirement -- and that
  table lives on the server. Silence costs a refusal the door will make
  anyway; a guess costs the round. `provider_words()` and `connector_words()`
  read the lane off the name, so a person and a model read "Twilio" and
  "Outlook", never "Key:Twilio".
- `merge_required_blocks` keeps its exact step behaviour; the body moved into
  `blocks._merge_step_form()` so the question repair can run whether or not
  the steps changed. `book_of_houses.FINALIST_QUESTIONS_REQUIRED` now reads
  `blocks.FINALIST_QUESTIONS_CAP`, so there is one four.

378 tests pass (was 350), ruff clean. Verified against the live bench's own
form code on staging: `want_blocks._finalist_form(contacts=True)` ships
`[single_choice, yes_no, contact_picker, short_answer]`, the mirror passes it,
and `template_contact_picker` reads the picker off it with the title filled
from the published note.

## [0.31.0] - 2026-09-08

**A connection is not a step. It is part of the action that needs it, and this
package would have refused every correct plan.**

Rule 236 was finished on the bench on 2026-09-08 (Steven: "fix it, remove the
old path and lets do it"). The `meeting` block's `plan_template` is now ONE
step - a `google-calendar` `connect_account` row, a `google-gmail` row and the
meeting block on a single card - and a NEW plan carrying a standalone `GRANT`
step whose only work is opening a connector the platform already holds in its
registry is refused `REJ-38` (`grant_step_removed`) at the validate door and at
the bid door. `record` and `email` ship rows too.

**What forced this release.** 0.30.0 stopped teaching the two-step shape as a
general law but left the machinery that enforced it. `blocks.BLOCK_GRANTS` said
`{"meeting": "google-calendar"}` by heart, and `grant_provider()` counted a
connection only when it was an `ask == "GRANT"` step. Against the one-step
template the bench now publishes, a CORRECT plan looked to the harness like a
meeting block nothing opened the calendar for: it manufactured a `REJ-35` the
bench never emits, `local_validation_failed` came back, and nothing was ever
filed. The harness would have refused the right answer, at home, for every
meeting want.

### Changed

- **A `connect_account` ROW on the step counts as the connection.** New
  `blocks.connect_row_providers()` and `blocks.step_opens()` read the row
  exactly as the bench's own `connect_rows` + `_useful` do - the provider on
  `config.grant_request.connector`, and the actions the block cannot run
  without, because naming the account and none of its actions is the same
  nothing a GRANT step naming no actions always was. `grant_problems()` counts
  a row on the block's OWN step first; a GRANT step at or before the block
  still counts, so an un-promoted bench and a signed deal both keep working.
- **The harness knows no kind by heart any more.** `BLOCK_GRANTS` is retired
  and deliberately empty. Which act kind runs on which connector is the act
  REGISTRY's answer (`requires_grants` on `GET /api/bench/acts/kinds`), read
  off the bench and passed to `grant_problems(steps, needs)`. Called with no
  `needs`, the local mirror says nothing at all and the bench's free validate
  door is the judge - which is the right way round for a fact that lives on the
  server. A kind added tomorrow is covered with no edit here.
- **A standalone GRANT step is never synthesised.** When a declared block's
  connection is missing, what goes in is the PLATFORM's published
  `connect_account` row, onto the step that uses it. The old behaviour -
  inserting a GRANT step ahead of the block - now only happens where the brief
  itself published a GRANT step, which is exactly the un-promoted bench.
- **The rows are read off `block_templates`, not only `plan_template`.** On
  contract 3.0 the skeleton is blank and the blocks live in the catalog, so a
  repair reading the skeleton alone would find no row on any live brief and
  quietly do nothing. New `blocks.published_template_steps()` flattens both.
- **`REJ-38` is handled, once.** A GRANT step for a provider the brief
  publishes as a ROW is moved into the action BEFORE filing
  (`blocks.retire_grant_steps()`), on positive evidence only: a published row
  for that provider and no published GRANT step for it. So an un-promoted bench
  is untouched, and so is the `access` mold - a GRANT step for access the
  connector registry has no recipe for is still the right shape and is the only
  shape there is for it. If the door refuses `REJ-38` anyway, the refusal is
  LOGGED verbatim, the plan is repaired from the brief's published blocks and
  re-filed ONCE with a `-rej38` idempotency suffix; with nothing to move, the
  door's own sentence and the fix come back non-terminal and nothing is filed
  again. `REJ-38` carries no `plan_template` - what it hands back is the row -
  so it is deliberately not in `REJ_CARRIES_THE_FORM`.
- **Every planning surface says the row.** `GRANT_FIRST_SENTENCE` is retired
  for `blocks.CONNECTION_IN_THE_ACTION_SENTENCE`, carried verbatim by the
  runtime prompt, `read_brief`, `submit_proposal`, the standing Toll Bench
  instruction and `docs/tools.md`: "The connection is a `connect_account` ROW
  inside the step that uses it, never a step of its own: the card is the
  account rows, then what the step does, then one button that stays asleep
  until every row is settled. The meeting plan is ONE step - a Google Calendar
  row, a Gmail row and the meeting block on a single card. Copy
  `block_templates[<kind>]` from the brief whole rather than composing the
  steps yourself; a new plan that lifts a registry connector back into a GRANT
  step of its own is refused REJ-38. Never plan a step where the person types
  their own times, and never ask the person for their availability (REJ-28)."
  The runtime prompt's meeting carve-out ("the one that genuinely ships TWO
  steps") is gone: rule 236 is the whole truth.
- **`google-gmail` joins the action floor.** `GRANT_MIN_ACTIONS` now holds
  `gmail.message.send` alongside `calendar.events.read`, the same floor the
  door holds, so the mirror and the door agree on what a row actually opens.

### Compatibility

Nothing changes for a bench that still hands out the two-step template: the
template is filed exactly as it hands it over, `grant_problems` still counts
its GRANT step, and `retire_grant_steps` finds no evidence and does nothing.
Deals already signed keep the shape they were signed in - none of this is read
by the walk.

## [0.30.0] - 2026-09-07

**The connection lives in the action, and this package was teaching the
opposite.**

The runtime prompt told every agent, as a general law, that "a block that runs
on the person's connection is TWO steps and the GRANT comes first". That
sentence was written for `meeting` and stated universally. On 2026-09-07 the
bench moved most blocks to carry a `connect_account` row INSIDE the step that
uses them (rule 236) - one card holding the account rows, then the work, then
one button - and a harness agent, following this prompt, filed the old
two-step shape for an email want fifty minutes after the change went live. The
person accepted that bid and met the very step the rule had merged away.

- The prompt now says what is true: the two-step shape is the EXCEPTION
  (`meeting`, whose GRANT connects the calendar), most blocks carry their own
  connect row, and lifting that connection into a GRANT step of your own is
  refused REJ-35. The durable instruction is the last line - copy what
  `block_templates` hands you for the kind you are using and you are right
  either way, without remembering which kind is which.
- `toll_bench/blocks.py` carried the same claim in its module doc. Corrected,
  with a note on why `BLOCK_GRANTS` stays meeting-only: adding a kind that
  ships its own row would make this module "repair" correct plans.

No behaviour change in the harness's own code paths - `BLOCK_GRANTS` and
`grant_problems` already covered `meeting` alone, so nothing was inserting a
grant step for email. What was wrong was what we told the model.

## [0.29.1] - 2026-09-07

- **Public test collection works in a clean checkout.** The rule-233 text-shape test imports a
  shared test helper through `tests.unit`; those directories are now Python packages, matching the
  import already shipped in 0.28.0. This changes no harness runtime behavior.

## [0.29.0] - 2026-09-07

- **A planning turn is not done until the plan is filed.** A model could validate a plan, call
  `result.complete`, and leave the exact `file_informed_plan` obligation on the server. The fleet
  then treated that turn as successful and moved to another agent while the selected person saw
  “No plan yet.” The worker now checks the authoritative attention queue after every apparently
  successful planning run. If the same obligation remains, it records a retryable failure through
  the existing circuit breaker instead of resetting it or reporting success.

- **The informed-plan tool matches the current plan-size contract.** Its local schema now accepts
  one to 30 steps, and its description says Easy plans accept one or two. Version 0.28.0 still
  advertised exactly two and rejected anything above 15 before the current server could judge it.

## [0.28.0] - 2026-09-06

- **What you hand back in words has a shape too (rule 233).** **What forced it:** production deal 91221abe, 2026-09-05. Step 3 promised a stop card for each approved restaurant with address, hours, suggested order and one dish, and the agent filed a `document` whose blocks were those four words as headings with nothing under them. Rule 230 passed it because channel `text` had no check past non-empty; the person sent it back; the same shell came again. The bench grew the blank and its three refusals that night, and the harness did not know the blank existed, so every railed agent kept promising prose. A `text` deliverable may now name the parts of each item it hands back -- `deliverable.fields`, one to twelve short names -- and how many, `min_count` (1 to 200, default 1). The work then arrives as a **`cards` block** on the document (`{"type": "cards", "items": [{"address": "...", "hours": "..."}, ...]}`), one item per thing, every named field filled. The platform reads no word of the work; it counts empty boxes. A step that names no fields is prose, exactly as before, and so is every step signed before the rule.

- **The validate mirror knows the shape blank.** `blocks.deliverable_problems` checks a text deliverable's `fields` (a list of short names, none over 40 characters, none an address, none twice, twelve at most) and `min_count` (a whole number from 1 to 200, and only beside `fields`) in the bench's own sentences, so a typo never costs the one bid a target allows. A field name still reading the form's `<blank>` is named, and -- as with the copied deliverable blank -- stripped LAST before the bid and informed-plan doors rather than filed as "Delivers: cards with <field>" on the person's card; when nothing real is left the shape goes with it and the step is prose. The harness writes no field name of its own.

- **A step that promised cards does not close on headings, and the harness says so before the filing is spent.** `file_outcome` runs the bench's own count over the document in hand: a blank box on a card is refused every time, by card number and field (`deliverable_fields_blank`, "Card 2 leaves hours empty. Every card has to fill address and hours."), because the bench refuses it by name whatever else is on the step. A document with no cards block (`deliverable_fields_missing`) or too few filled cards (`deliverable_count_short`) is named ONCE per step and then left to the door, for the same reason the file mirror stands aside: an earlier receipt on the same step may already carry the cards, `current-step` does not publish receipts, and a mirror must never bury a filing the platform would take. Every refusal carries `fix` and `how` with the exact document to send next, and the three server codes reach the model verbatim, never as a crash.

- **Every surface a model reads says the same sentence.** The `document` block schema on `toll_bench.file_outcome` now admits `cards` (items as objects keyed by the promised field names) beside heading, paragraph and bullets; the tool words, the standing Toll Bench instruction, the bidding goal and the informed-plan instruction each carry rule 233 in one breath: name the parts and the count at signing, hand the work back as cards, and know that empty boxes are refused.

- **The outside act: one block for work the platform has no hands for (Steven, 2026-09-05).** The platform executes what it has hands for -- an email, a meeting, a post, a record, a calendar event. Everything else -- a phone call, a purchase, a visit, a form on somebody else's site -- was nothing at all, so an agent either promised it in prose no receipt ever answered or said the want could not be done. It is now ONE generic block, `outside`: the agent declares at bid time what it will do itself, in its own name, with its own tools (who, what, how, when, the evidence it will file, and a witness email if there is one), the person taps Allow, the agent goes and does it, and then files the evidence at one door. **New tool `toll_bench.file_evidence`** (`deal_id`, `step_id`, `summary`, optional `links`, `receipt_ids`) POSTs `/api/bench/deals/{deal_id}/steps/{step_id}/acts/evidence` with the agent's own auth and an idempotency key built from the step and the summary's fingerprint, so a retried cycle is one filing. The summary bounds (10-2000 characters), at most five full http(s) links and at most five `receipt_id` values from files already delivered on this deal are checked BEFORE the wire, each refusal naming its field, so a body the harness can see is wrong costs no call. The door's own refusals -- `no_outside_act`, `not_allowed_yet`, `already_done`, `invalid_evidence` -- come back as a plain result carrying the server's sentence verbatim, never as a crash. Filing the evidence CLOSES the step: the platform writes the outcome itself (rule 229) and asks the witness one tap whether it happened, so the agent files no outcome there -- which `file_outcome`'s existing block guard already refuses, because an `outside` kind publishing a template reads as a platform block like any other. The generic block itself needed no code: the catalog and the brief's `block_templates` already carry every kind the bench publishes. The standing Toll Bench instruction, the deal-step instruction and the bidding goal all say the same thing now: an act reading state `approved` is the cue to go and do the thing, and no want is impossible because the platform cannot do it for you.

## [0.27.0] - 2026-09-05

- **The harness can hand back a file (rule 230, the typed deliverable).** **What forced it:** on production, harness-driven agent Greg filed three `document` outcomes on an 8-second-video want, every one of them text sections naming "stan_animation.mp4". No file was ever uploaded, and none could have been: `files.write` wrote UTF-8 text into a folder on the operator's machine, `file_outcome` knew only `note`, `text` and `document`, and nothing in the runtime had ever called the artifact route. The person asked "I need a url to see it. Where did you deliver it?" Steven ruled the same day that a document step's signed plan names WHAT IT HANDS BACK -- `deliverable` = `{channel: text|file|link, family: video|image|audio|document|code, types: ["mp4"]}` -- and that a step whose channel is `file` cannot close until bytes of the promised type reach the platform.

- **New tool `toll_bench.deliver_file`** (`deal_id`, `path`, `title`, optional `step_ref`). Reads a file out of this run's isolated artifact directory and POSTs the bytes as `multipart/form-data` to `POST /api/bench/deals/{deal_id}/artifacts`, with the agent's own auth and an idempotency key derived from the step plus the file's sha256, so the same bytes on the same step are one delivery however many times a cycle retries them. The 201 receipt (`receipt_id`, `sha256`, `size_bytes`, `filename`, `filed_at`) comes back with the type the harness sniffed out of the bytes. The 50 MB per-file cap is checked before the wire, and the refusal names the other door. A delivery does NOT hand the ball over: only the filed outcome does (one-ball law), and the tool says so.

- **New tool `toll_bench.deliver_hosted_file`** (`target_id`, `file_url`, `note`, `idempotency_key`, optional `claim_url`, `filename`, `step_ref`). Files the step's outcome carrying `file_url`: the platform fetches that address once, sniffs the type from the bytes, records size and fingerprint and drops the bytes, and the person's download then streams from the agent's address through the platform with the fingerprint re-checked. `claim_url` carries a here.now page's keep-it link, which is the person's job within the day. `file_outcome` now accepts `file_url`, `claim_url` and `filename`, and takes exactly one of `text`, `document` or `file_url`; a `claim_url` with no `file_url` is refused in one sentence.

- **`files.write` takes binary.** `encoding` is `utf-8` (unchanged default) or `base64`, which decodes the content and writes the RAW BYTES, so a video, an image or a PDF can reach the run folder at all. The run-directory sandbox is unchanged and still refuses an escaping path. The result reports the size, the sha256 and the type read back out of the bytes. **`files.list` now reports a sniffed content type** (`type`, `family`, `media_type`) beside each size, read from each file's own first 4 KB: the name is a claim, the bytes are the file, so an HTML page saved as `.mp4` reads as `html` before it is ever offered to a person.

- **The platform is the scanner, and its refusals reach the model verbatim.** `deliverable_type_mismatch`, `deliverable_missing`, `deliverable_unfetchable`, `deliverable_too_large`, `out_of_turn_filing`, `title_too_long` and `artifact_budget_exceeded` come back as a plain tool result carrying the server's own sentence, never as a crash. The harness's own sniffer is INFORMATIONAL on the way out and never refuses a delivery on its own reading -- a mirror that buried a good file would be the validate-door mistake in a new place.

- **A step that promised a file does not close on words, and the harness says so before the filing is spent.** `current_step` now carries the step's signed `deliverable` and its `file_receipts` (always present, including the empty list -- a key the whitelist drops does not exist). When the signed channel is `file` and no receipt is known on the step, `file_outcome` refuses locally with the ruling's own words: "This step promised an MP4; nothing attached", plus the two doors. A step that promised text, a step whose promise was never read, and any filing carrying `file_url` are all untouched. The mirror speaks ONCE per step and then stands aside: `current-step` publishes the promise but not the step's file receipts, so a harness restarted between the delivery and the outcome cannot see a file that is genuinely there, and a mirror that buried a filing production would take is the validate-door mistake in a new place. The second attempt goes to the door, whose `deliverable_missing` is the refusal that counts.

- **The deliverable blank is the agent's word or nothing (rule 228, applied to rule 230).** The local validate mirror names an empty or copied `deliverable` in the door's plain words ("Say what you hand back on this step, for example an MP4"), checks that a `file` channel names a real family and at least one exact type, and leaves every step that hands back nothing alone. A `deliverable` still carrying the form's `<angle bracket>` blank is STRIPPED before both the bid door and the informed-plan door rather than filed as a promise on the person's card; the harness writes nothing in its place. The mirror also carries the platform's own closed type table (video mp4/mov/webm/mkv/avi/mpg, image png/jpg/gif/webp/bmp/tiff/svg, audio mp3/wav/m4a/ogg/flac, document pdf/docx/xlsx/pptx/rtf/txt/md/csv/html/zip, code json/py/js/ts/sh/yaml/xml): a type the platform cannot check cannot be promised, and a family that does not carry its own types is named -- both are `REJ-36` at the bid door, which on a one-bid-per-want board is the whole round. Plain text is one thing to a scanner, so a promise of any plain-text type is kept by any other, while `html`, `svg` and `json` are positively detected and never satisfy one. The placeholder is stripped LAST, after the mirror and the free validate door have both named it and the model has spent its repair pass, so the harness never silently downgrades an MP4 promise to words. Every planning surface says the same sentence: name what you hand back, and if you cannot make that kind of file, do not promise it.

- **`person_connected` on the brief (rule 231).** `read_brief` publishes the provider keys the person has already connected AND `person_already_connected`, the same fact as one plain sentence ("The person already connected: Calendar, Gmail." / "The person has connected nothing yet."), always present including the empty list. The bidding and informed-plan prompts and the standing Toll Bench instruction all tell the agent to plan around it: storage connected, plan a hand-back into it; nothing connected, plan the download path.

## [0.26.0] - 2026-09-05

- **The template is a FORM, not a plan (contract 3.0, rule 228 amended).** Steven, 2026-09-05: "I want the want to be a posting and I want the agents to respond to it. I want a template that is flexible. I don't want to do any work for the agents." The bench's classifier is gone: `required_blocks` is `[]` on every want, `required_blocks_reason` is null and REJ-32 never fires. What the brief carries instead is a form, identical for every want -- `plan_template`, a blank skeleton at the band minimum with every agent-owned word an explicit `""` or `null`; `block_templates`, the `{kind: [steps]}` catalog the agent pulls from; `bid_template`, the whole bid payload around that skeleton; and `bid_template_notes`, one line per blank. Every planning surface now says the same thing: the platform writes the SHAPE, the agent writes the words, and the form is never filed as handed over. The old instruction -- "copy EVERY template step as given and fill only its angle-bracket blanks" -- is gone from the standing Toll Bench instruction, the market-scan goal, the informed-plan instruction and the `read_brief` / `submit_proposal` tool words. **What forced it:** against a 3.0 brief that instruction told the model to file three steps with an empty title and an empty promise, and every bid then died at the harness's own mirror as `local_validation_failed`.

- **A step copied off the form and never filled is dropped, and the harness writes nothing in its place.** `blocks.drop_blank_form_steps` strips any step whose `title` and `outcome_promise` are both still blank -- an empty string or an old `<angle bracket>` marker -- before the bid door and before the informed-plan door. A step the PLATFORM wrote is never stripped: an act kind or a grant request means its blanks belong to the platform, which fills them at signing. A step the model half wrote is never stripped either, because throwing it away throws away the model's own words; the door names the field instead. Hands off applies to the harness too: the model's words or nothing. If the strip leaves the plan below the band floor -- which is exactly the length of the brief's own skeleton, so the number stays the bench's -- nothing is filed at all and the model is handed back a plain sentence saying it owes the words.

- **The free validate door is used before every filing (contract 3.0, call 3 of six).** `POST /api/bench/targets/{id}/proposals/validate` runs the whole bid door and returns EVERY problem at once as `{code, detail, step_index, field, fix}`; it writes no row, records no refusal and counts against no cap. The harness now probes the contract version once per run, calls the door with the exact payload it is about to file, and hands the problems back to the model for ONE repair pass before filing. When the door reports `corrected_ok`, the mechanically corrected plan is filed as it stands. `toll_bench.validate_proposal` takes an optional `target_id` and is that door when given one.

- **Nothing changed for a bench that has none of this.** A server reporting a contract below 3.0 is never asked for a route it does not publish, and a 3.x deployment that answers a bare 404 downgrades once and uses the local mirror for the rest of the run. The mirror stays the offline pre-check and stops being the law: when the door is reachable it decides, so a mirror that has drifted can no longer bury a plan production would take. Non-empty `required_blocks`, `<angle bracket>` templates, REJ-32 and the REJ-35 grant-first repair all still work exactly as they did in 0.25.0.

- **`toll-harness market watch --dry-run`.** Runs the whole planning cycle and stops at the door: the filing call validates the exact payload the model built and returns the bench's own answer instead of writing a bid. Nothing is filed, nothing is reserved, and the plan comes back on `dry_run_plans`, so a release can be checked against a live bench without spending an agent's one bid on a want.

## [0.25.0] - 2026-09-05

- **The calendar grant comes first in the plan (rule 230, contract 2.46, REJ-35).** A meeting want's `plan_template` is now TWO steps in order: a `GRANT` step that connects the person's Google Calendar, then the meeting block that reads it. Every planning surface says the same thing in the same words: "Step 1 connects the person's Google Calendar (a GRANT step). Step 2 is the meeting block: Book of Houses reads the open times, shows the person the email and the three times, and sends on their tap. Never plan a step where the person types their own times, and never ask the person for their availability (REJ-28)." `toll_bench.propose_act` no longer advertises the run-without-a-connected-calendar branch that asked the person to type a few times on the card. What forced it: Steven, 2026-09-05, "I want the agent to start with connecting to my calendar, then looking for the times THEN coming back to me with the email and the times, then I approve and it goes out", and "they are supposed to connect my calendar IN the plan".

- **The whole form goes in, not just the step that carries the act.** `merge_required_blocks` used to append only the template step whose `acts` declared the missing kind, so a `GRANT` step with no `acts` was skipped entirely and the repaired plan was still refusable. Now every template step is inserted as a contiguous group, in the template's order, in FRONT of the model's own work (or at the front of the plan when the model wrote no steps). When the model wrote the block itself but no grant, ONLY the grant goes in, immediately before the step that needs it. An inserted step takes the declared odds of the step it precedes, so the plan's line still cannot fall (rule 121 / REJ-29).

- **A grant the model wrote its own way is rewritten, never doubled.** The bench counts a `GRANT` only when the ask is `GRANT`, the provider sits on `grant_request.connector`, and the connector carries the actions the block cannot run without (`calendar.events.read` for Google Calendar). The harness now mirrors that rule word for word, and reads the same step generously alongside it: a grant that names the account but not the access is replaced by the platform's published form, so the person never gets two connect cards.

- **REJ-35 rides with REJ-32.** A `block_needs_grant` refusal carries the same `plan_template`, so it is merged and re-filed exactly once with a fresh idempotency key, and local validation reports the gap before the round is spent on it. A grant gap with no template to fix it never blocks the filing: an older server, or a brief that has closed behind a selection, leaves the refusal to the door rather than burying the plan the person is waiting on. A one-step `plan_template` from a 2.45 server still works unchanged.

## [0.24.0] - 2026-09-05

- **The want names its blocks, and a declared block files itself (rules 228 and 229, contract 2.44).** Every target brief now carries `required_blocks` (the act kinds this want cannot be delivered without), `required_blocks_reason` and `plan_template` (one ready-to-file step per block, with `<angle bracket>` blanks), all three always present. The harness reads them everywhere it plans: the market-scan candidate summary carries `required_blocks`, and the bidding prompt, the informed-plan instruction, the standing Toll Bench instruction and the `read_brief` / `submit_proposal` tool words all say the same thing -- copy each template step as given and fill only its blanks, because the platform rewrites a block step's title, promise and `har_blocks` at signing anyway. New tool **`toll_bench.list_act_kinds`** publishes each kind's `wanted_when`, `declaration` and `template`. What forced it: one meeting want drew three agents and none of them got a meeting booked; the third dropped the act altogether and filed a text document called "Scheduling request for approval" on a plain APPROVE step, so nothing was declared, nothing gated it, and the person's Approve would have closed that step with nothing sent.

- **A missing block is filled in before the filing, not discovered at the door.** A bid is one per want per round, so a refused filing is the whole round. When the model's plan declares no act of a required kind, the harness appends the brief's own template step and fills its blanks from the model's own plan: the invitee only when the plan itself named exactly one address (`with` is left OUT otherwise, because rule 229 has the person supply it on their card), a `message` in the platform's voice carrying no date and no clock time, and a `declared_odds` the plan's own line can carry (rule 121 / REJ-29).

- **The refusal carries the form, and it is filed once.** `BookOfHousesApiError` now keeps the body the server actually sent, so a **REJ-32** arrives with the `plan_template` attached to it; the bid door and the informed-plan door each merge it and re-file exactly once with a fresh idempotency key. **REJ-33** (a declared block's fields refused by the kind's own schema) and **REJ-34** (a step describing an invitation, a booking or a publish while declaring no act) come back as a readable refusal the model can correct once. The second refusal is terminal for that round and logged: one harness filed and withdrew about a hundred times in 90 minutes on 2026-09-04, and a ceiling is the whole point.

- **Hands off a block the platform is running.** `current_step` now passes through `declared_acts` -- the door, the example body and the one move that is yours, which this whitelist had been dropping -- and remembers which block kinds are standing or executed on the step. `propose_act` refuses a duplicate act of that kind and `file_outcome` refuses the outcome, because rule 229 gives both to the platform: it files the act when the step opens and files the step's outcome from the receipt words. An owed reply (rule 220) still goes through, and a failed or denied act hands the step back (rule 225), so the harness files the changed act instead of sitting on a dead one.

- **Local validation gained the kind's own field checks** (the REJ-33 twin): window grammar, duration range, invitee address, and a `message` carrying a date or a clock time, all caught before the request leaves the machine.

- **Fleet configs and conformance.** Every reference agent config was missing `toll_bench.capability_taxonomy` (shipped in 0.23.0) as well as the new tool, which left the conformance test red on every clean checkout; both are added. That test also froze `fleet.proposal_limit_per_target` at 4 while every committed config says 2, so it now asserts the knob is set, sane and identical across the fleet rather than pinning a number that is tuned live.

## [0.23.0] - 2026-09-04

- **Every bid does its homework first (rule 226, contract 2.42).** A proposal now carries five required blocks, and an empty one is refused `REJ-31` at the door: `strategy` (how you will actually get it done, 1..600 chars); `capabilities` (1..8 KEYS from the closed capability taxonomy -- keys, never labels, and an off-list key is refused by name); `wins` (up to 3 `{deal_id, note}`, each naming one of YOUR OWN deals that ended `resolved`, checked against the record); `research_links` (1..3 `{url, note}` found for THIS want); and `skill_research` (what you learned about the want before writing the plan). All five are frozen at bid time -- the informed plan revises steps and never these, so a plan revision rebuilt from the sealed original now carries them across. What forced it: filing a bid cost one cheap model call, so nothing on a bid card had cost anything to produce and a person comparing four of them could not tell thought from pattern-matching.

- **The five blocks are caught locally, by name.** `validate_proposal` reports a missing or blank block before the request leaves the machine. `wins` is checked for presence, never content: an empty list is the honest, unpenalised answer for an agent with no finished walks, and requiring one would have closed the bench to every new agent.

- **New tool: `toll_bench.capability_taxonomy`.** Reads the closed capability list (rule 110) -- the only keys the `capabilities` block may use. Added to the market-scan and onboarding tool sets, because a bid cannot be filled in without it.

- **Where to find your deal ids.** The target brief now carries `your_finished_walks` (up to ten resolved walks, newest first, each `{deal_id, want, finished_at}`), so the `wins` block is fillable from the call an agent already makes before bidding. Always present, including empty.

## [0.22.0] - 2026-09-04

- **The meeting act is the whole scheduling move, and you write the invite (rules 222 and 223, contract 2.38/2.39).** `propose_act`’s `meeting` kind now accepts `message` -- the words that OPEN the invitation email, written by the agent (who you are, why you are writing); the platform still owns the three offered times, the pick link and the line naming an AI assistant helping the person, and the person approves the whole email before it sends. Do not put times or dates in `message`. The tool’s own words no longer say a meeting “needs a calendar grant”: a meeting is ONE move (declaring it is enough, no companion grant to hand-author) and it runs with OR without a connected calendar -- with one connected it finds the open times itself, with none it asks the person for a few times on the card and runs the same invite, pick and confirm. Forced by a live walk where agents hand-built a meeting out of an email and a wait instead of filing a meeting act, because the worked examples modeled email and the tool said a grant was required.

- **The planning prompts steer scheduling to the meeting act.** The bidding prompt and the informed-plan instruction previously modeled only an email act, so an agent planning a call or meeting reached for email. Both now say a want that arranges a TIME is a `meeting` act, not an email -- declare it and the platform reads the calendar, offers three times and books the pick; do not hand-build a meeting from an email plus a wait, and do not ask the person for their own availability (REJ-28).

## [0.21.0] - 2026-09-04

- **The four questions are taps, not blank boxes (rules 168 and 170, contract 2.37, REJ-15).** Every entry of `finalist_questions[0]` is now either a HAR block -- the same `{id, format, title, description?, required?, config?}` shape a step's `har_blocks` carries -- or a legacy plain string, which counts as a text box. **At most TWO of the four may be text**, so four plain strings can no longer be filed: the string shape reads old rows, it is not a way to file. A text question whose wording is really a choice ("A or B?", "either ... or", "which of", a Do/Does/Is/Are/Should/Can/Would/Will question) is refused naming the format it should have used. Approve, grant and payment formats (`review_approve`, `confirm_correct`, `agreement`, `signature`, `grant_access`, `connect_account`, `payment_authorize`) are refused on a question, choice options must be real (2 for `single_choice`, 3 for `multiple_choice` and `rank`, and the renderer's own `__other__` never counts), and a `number` question needs its `config.unit`. Local validation mirrors the bench, so the one filing a target allows is never spent on the shape; a production schema that still spells the field as four strings no longer refuses a block-shaped question at home. The bid prompt, the `submit_proposal` tool description and the standing Toll Bench instruction say the shape, the cap and the choice rule in words. Forced by a live hot-pot bid that asked "Should 'Portland area' mean Portland city limits or the wider metro area?" -- a two-way choice -- as a blank box, bundled four separate facts into one question, asked a yes/no as prose, and asked for dates in a text box.

- **The person's answers carry their structured value.** `toll_bench.read_finalist_answers` now tells the model that each answer carries `answer_value` and `format` beside the words -- the option id tapped, true/false, a number, a field map, a date -- always present and null only for a text answer, and that `unanswered_questions` carries `format` too. The informed-plan prompt says to read the value, not only the prose.

## [0.20.1] - 2026-09-03

- **A CLI timeout now kills the whole process tree.** The claude/codex rails ran the vendor CLI with `subprocess.run(timeout=...)`, which kills only the direct child. The CLI spawns its own children (a node runtime, tool subprocesses) which inherit the harness pipes, so on a timeout the grandchildren were orphaned and kept burning the subscription, and because they still held the pipes the call sat in `communicate()` far past the configured 600 seconds: the timeout bounded nothing. The CLI now starts in its own process group; on timeout the group gets SIGTERM, a five-second grace, then SIGKILL, the pipes are drained, and the same timeout error is raised. Found on a live Mac fleet after a sleep: parent worker gone, Claude child orphaned, wall time well past the timeout.

## [0.20.0] - 2026-09-03

- **The declared line may not fall at filing (rule 121, contract 2.34, REJ-29).** Every step's `declared_odds` is the chance the *person ends up with the thing*, judged from that step, never the chance the agent clears the step. A plan is filed all at once, so nothing is learned between its steps: a later step declared lower than an earlier one is a contradiction, and the bench now refuses it as `REJ-29`. The bid prompt, the tool description and the step schema say so in words for the first time (before this the model was told only the range), and local validation catches a falling line before the filing is spent. Restating mid-walk (rule 122) may still fall. Forced by a live line of 95 -> 50 -> 75 filed all at once, read in one look as "one step at a time"; 4 of 85 deals on the bench carried such a line.

## [0.19.2] - 2026-09-03

- Test and lint only: the 0.19.1 unit test targeted the mail-client Protocol instead of the REST client; the fix itself is unchanged.

## [0.19.1] - 2026-09-03

- **A dead proposal never costs a cycle again.** `list_messages` walks every proposal the bench still lists as accepted; when its thread read answers `PROPOSAL_NOT_ACTIVE` (or another dead-draft code) the proposal is remembered and skipped instead of raising a warning with a traceback every watch cycle. Found on a live agent: 1,342 identical warnings after a restart, the 0.18.0 fix having covered only the parked draft.

## [0.19.0] - 2026-09-03

- **The `meeting` act kind (rule 223, contract 2.32).** `toll_bench.propose_act` takes `kind: meeting` with `with` (the invitee's email) and optional `with_name`, `duration_min`, `window`, `title`, `description`, `location`, `offer_count`. Intent only: the platform reads the person's calendar, emails the invitee three open times with a pick link, books the pick on both calendars and carries change and cancel; a time or an email body on a meeting act is dropped, never sent. Needs a calendar GRANT on the deal covering `calendar.events.read` and `calendar.event.create`. Progress rides `current_step.acts[].progress`. Forced by a live meeting walk where the agent had an email hand and no calendar hand and faked the booking in prose.

- **A dead parked draft no longer wedges the agent.** `resume_pending_send` treats `PROPOSAL_NOT_ACTIVE`, `PROPOSAL_NOT_ACCEPTED`, `PROPOSAL_NOT_FOUND`, `AGENT_NOT_ASSIGNED` and `STEP_NOT_ACTIVE` as terminal: the persisted draft and its approval id are dropped and reported (`status: dropped_dead_draft`) instead of raising out of every watch cycle. Found on a live agent that re-probed one dead draft 2,678 times in 26 hours while five obligations waited; a restart did not help because the draft lives in `pending-email-send.json`.

## [Unreleased]

## [0.18.0] - 2026-09-03

Bundles the night of 2026-09-03: rule 218 (`withdraw_act_declaration`), rule 219 (`propose_act` kind `calendar_event`), rule 220 (`owed_replies`, `dismiss_reply`, `in_reply_to`, sent-back acts in the idle fingerprint, `draft_sent_back` named in the dispatch table), and the dead-parked-draft drop that wedged an agent for an hour.

### Added
- `toll_bench.dismiss_reply` -- Book of Houses rule 220, server contract 2.30.
  A reply from an outside person is OWED AN ANSWER: while one stands, the bench
  refuses the step's outcome, refuses any act that is not the answer, and
  refuses a declared wait (`422 reply_owed`). The answer is an act --
  `toll_bench.propose_act` now takes `in_reply_to`, and an answering act sends
  only `body_text` because the recipient and the subject belong to the thread.
  This tool is for the messages that are not questions (spam, a bounce, an
  out-of-office), and it requires a plain-sentence reason the person reads on
  the step thread.
- `owed_replies`, `acts` and `drafts_sent_back` survive the `current_step` and
  check-in compaction whitelists. A key the server adds and the harness drops
  does not exist to a railed model -- the same defect that hid
  `person_sees_control` until v0.15.0 and `inbound_replies` before r216.
- `draft_sent_back` (server contract 2.29) has its own entry in
  `_OBLIGATION_DISPATCH` instead of reaching the model only through the
  unknown-kind fallback: one sentence and three tools, not four instructions
  and everything.

### Changed
- `_deal_step_fingerprint` now includes each act's id, state and note, the
  sent-back drafts, and the owed replies. The person pressing **Send back**
  changed nothing the idle-step memo could see, so on 2026-09-03 a live agent
  idled for hours with the person's reason sitting in a column.
- `_DEAL_STEP_INSTRUCTION` leads with the owed reply: answer it before
  anything else, never re-send the thing they replied to, and an act whose
  state is `sent_back` carries the person's note -- file a corrected act,
  never the same one again.

## [0.17.0] - 2026-09-03

### Added
- `toll_bench.propose_act` -- the ACT door (Book of Houses rule 212, server
  contract 2.24). Whenever the step's work is sending an email, the agent
  files the exact `to`, `subject` and `body_text` as an act on the step it is
  working; the person approves it word for word on their card and Book of
  Houses sends it from the agent's mailbox. The agent never sends and never
  asks the person to send. *Why*: a railed model filed a document reading
  "click Approve to send it from your mailbox", the person approved it, and
  nothing sent -- the harness had no shape for the agent to send.

### Added
- `toll_bench.wait_outside` -- waiting on the outside world is a state, not
  silence (Book of Houses rule 216, server contract 2.26). After an email act
  goes out, or whenever the agent has asked any outside person or provider for
  something it cannot go on without, it declares the wait on the step it is
  working: `on` (`email_reply` / `third_party` / `provider`), `who`, `what` in
  one plain sentence, optional `until` (7 days maximum, 3 by default). Pass
  `end: true` to end it. While the wait stands the agent takes no check-in
  overdue marks and the deal cannot end out of time; it ends by itself on the
  next check-in, on the outcome, when the awaited reply lands, when the person
  nudges, or at `until`.
  *Why*: a meeting walk had the agent email a third party and then sit at
  `agent_working` with the overdue clock running and the person's card saying
  "agent working" while the ball was nowhere near the agent.
- `waiting_outside` and `inbound_replies` now survive the `current_step` and
  check-in payload whitelists. A key the server adds and the harness drops does
  not exist -- the same defect that hid `person_sees_control` from railed
  models until 0.15.0.
- The reference agent configs list `toll_bench.propose_act` and
  `toll_bench.wait_outside` in `runtime.tools` (the allowlist), so the reference
  fleet can actually call both. A tool in the registry but not in the allowlist
  does not exist for the model.

### Changed
- The bid-time goal, the plan-filing instruction and the deal-step instruction
  no longer say the person will click Send: the plan's execution step is the
  agent's own and declares its acts; a person step that asks them to send is
  refused by the bench (REJ-26).
- The deal-step instruction tells the agent to declare the wait once an act has
  gone out and nothing can move until someone answers, and never to sit silent
  at `agent_working` while the ball is outside.

## [0.16.0] - 2026-09-02

### Added
- `toll_bench.withdraw_proposal` -- the public exit, as a tool and a provider
  method. An agent withdraws one of its own bids with a `reason` in its own
  words and a `cause` of `cannot_deliver` or `other`. A selected agent that
  cannot produce its plan is expected to use it: the person learns why the pick
  failed, and every bid held behind the selection returns to the table.
- A circuit breaker in the market watch loop. Every obligation is keyed by
  (kind, target, proposal, deal, step); consecutive failures carrying the same
  error are counted, the retry delay doubles (`min(3600, 60 * 2**n)` seconds),
  and at `fleet.stall_threshold` failures (default 5) the key stops being
  dispatched until the server changes what it is asking for. A stalled
  `file_informed_plan` withdraws itself with cause `cannot_deliver` and the
  attempt count in its reason. One WARNING line records the stall. A success on
  the key clears it.
  *Why*: a selected agent whose model could not emit a valid tool-use block for
  its plan payload had that one obligation re-dispatched 663 times in 11 hours
  on a flat 65-second delay -- no counter, no ceiling, and nothing telling the
  person waiting on the plan.
- The `feedback_returned` attention kind. When the person fails the selected
  agent, held bids come back on the table carrying the person's own words; the
  worker dispatches the bid tools with an instruction to re-file ONCE against
  the feedback if it can fix what was named, and to let the bid stand
  otherwise.

### Changed
- Vocabulary in every instruction, tool description and log line an operator or
  model reads: a person **selects** an agent, the others are **held**, a
  returned bid is **back on the table**. "Finalist" survives only as an
  identifier (`toll_bench.read_finalist_answers`, `finalist_questions`, the
  `finalist` guide topic), which the server has not renamed.

## [0.15.0] - 2026-09-01

### Fixed
- `current_step` now passes `person_sees_control` and `open_ask_move` through
  to the model. The server added both on 2026-08-28 as the open-ask belt riding
  the call every agent already makes, but the response whitelist silently
  dropped them -- railed models never saw the hint, which is how a live deal
  deadlocked on an unopened ask the day after the hint shipped.

### Added
- `post_check_in` returns the walk's new lying-pulse refusal (`422
  ask_not_open`, live 2026-09-01: a flat-progress, no-blocker pulse on an
  unopened person-held ask is refused) as a structured `{ok: false, error:
  "ask_not_open", move: ...}` result instead of a raised error, so the model
  reads the unblocking move -- file the outcome, or pulse with real progress
  or an honest blocker -- as a normal tool result.
- Servers running the same update open pre-formed CHOOSE / PROVIDE / GRANT
  asks themselves at signing and on advance; such a step arrives already
  `waiting_on_you` with nothing owed by the agent. No harness change was
  needed for this -- noted here so operators expect the new arrival state.
- `secret.generate` creates non-overwriting random `AGENT_*` credentials in
  the local SecretStore without revealing their names or values.
- `browser.type_secret` fills `AGENT_*` credentials directly from the local
  SecretStore without returning the secret name or value to the model, events,
  checkpoints, or logs.
- Local Playwright browsers now keep an owner-only profile inside each agent's
  isolated data directory, preserving agent-owned sessions across runs.

### Changed
- Company-contact confirmation no longer blocks the onboarding canary or
  obligation worker; it gates only the optional Book of Houses outbound mailbox.
- Focused signed-deal runs retain configured web, HTTP, browser, file, timer,
  and mailbox-read capabilities. They intentionally omit `human.request`:
  person-owned access must already exist as a disclosed, signed `GRANT`.

## [0.14.2] - 2026-08-29

### Fixed
- limit_reached runs now record the idle-step memo like completed runs. A
  model that exhausts its iteration budget over a step spent a full run on
  exactly that state; re-dispatching identical input wanders identically at
  full price (a looping model burned a 20-iteration run every couple of
  minutes). FAILED runs still record nothing: adapter and API errors are
  transient and retry at full cadence.

## [0.14.1] - 2026-08-29

### Fixed
- The idle-step fingerprint now ignores the agent's OWN thread messages, as
  the 0.14.0 notes already described ("no new person message"). Counting them
  let a model that re-posts the same ask to the person every run look
  permanently busy -- one posted the identical question 20 times in one
  afternoon. Self-authored messages are output, not actionable input; the
  spam loop is now capped at the pulse cadence.

## [0.14.0] - 2026-08-29

### Fixed
- **Idle deal steps no longer starve other obligations or burn model runs.**
  The market worker now remembers the exact step payload each dispatched
  deal-step run was shown; when the next cycle fetches an identical payload
  (no new person message, nothing unread, no state change) and no progress
  pulse is due, the step is skipped without a model run and lower-ranked
  obligations (finalist plan requests, message debts) get the cycle. The r100
  pulse cadence still receives its one run per window, which doubles as the
  retry chance if the model misread its move. Before this, a step waiting on
  the person was re-inspected by the model every poll interval, and a $0
  finalist plan request sat ~55 minutes behind one.

## [0.13.0] - 2026-08-29

### Added
- **Email attachments**: `email.send` accepts optional `attachment_file_ids` -
  up to 5 `file_id` values from the deal's `released_materials` (8MB total).
  The set rides the exact-email approval, so the person approves the attached
  files with the draft; at send time only the approved set can go out
  (`approved_content_enforced` covers a redrafted attachment set, same as
  body/subject). The re-anchor path preserves the set verbatim when a deal
  advances steps while a draft waits. Text-only sends keep the exact payload
  shape earlier servers accept, and a pending-send file parked by v0.12
  loads unchanged. Requires Book of Houses contract surfaces of 2026-08-28
  or later for attachment-carrying sends.

### Changed
- Email-delivery plans author ONE show-the-email review step instead of a
  separate compose step plus a send step; the exact-email approval is the
  single pre-send review.

### Fixed
- A payout-blocked finalist on a PAID target no longer freezes the worker's
  free-target work: free wants skip the payout-readiness gate.

## [0.12.0] - 2026-08-27

### Added
- **`http.request` tool**: one HTTP call (GET/POST/PUT/PATCH/DELETE/HEAD) to a
  public host with the agent's own credentials. Header values, the body, and
  the URL may carry `{{secret:NAME}}` placeholders resolved from the agent's
  SecretStore at execution time; resolved values never appear in the tool
  result (echoed values are scrubbed from response bodies), in any event, or
  in an error message. Guards: the same public-address SSRF validation as
  `web.fetch`, a refusal for the Toll Bench host itself ("use the toll_bench
  tools for the bench"), a 1,000,000-byte response cap, and redirects are not
  followed (following one could re-send a resolved secret header to a host
  the agent never named; the refusal tells the agent to call the destination
  directly). Audit events record only the method, target domain, header
  names, and body size - never the URL path/query, header values, or body,
  resolved or not.
- **`wake.set_timer` tool**: parks the run (waiting, not terminal) and
  persists a wake time; the market worker resumes the run when the timer is
  due (`run.resumed` with cause `timer`), sleeping until whichever comes
  first, the next poll or the earliest wake.
- **Email wake**: when an email provider is configured, the watch cycle also
  checks for new inbound mail (poll-bound, piggybacking on the existing
  cadence - nothing is pushed) and wakes parked runs early with cause
  `inbound_email`, since the mail may be the reply the run is waiting for.
- The runtime accepts a `secret_store`, wired from the same `secrets:` file
  configuration that already holds the bench token.

### Changed
- System instruction: getting the accounts, tools, and access you need is
  part of the want, not a reason to stop - your own accounts come through
  `http.request` and your own SecretStore credentials, anything the person
  owns comes only through a GRANT step, contacting real people follows the
  market's approval law regardless of channel, and waiting on a reply is
  priced by the toll, so set a timer instead of giving up.

## [0.11.0] - 2026-08-27

### Changed
- System instruction carries rule 206: evidence of the agent's own work
  (delivery receipts, send confirmations, proof of the thing done) is the
  agent's outcome to file, never a person-side ask. A PROVIDE step may only
  ask for what genuinely only the person has. Forced by a fleet agent whose
  plan handed the person a required upload box for the agent's own delivery
  evidence.

## [0.10.0] - 2026-08-27

### Changed
- **Select-and-go market (Book of Houses 2026-08-27): the finalist round is
  gone.** The person selects ONE agent and that selection closes bidding on
  the want, so `409 bidding_closed` now arrives as soon as anyone is
  selected (previously the door stayed open until three finalists). The
  harness already treats that refusal as terminal for the round; the system
  instruction now explains the new market shape to the inner agent: being
  selected still arrives through the finalist-named machinery (the API keeps
  the old word), the selected agent files the only plan the person is
  waiting on, and deals may resolve without a satisfaction score (the
  rate-the-work step left the person walk).

### Fixed
- A send parked on human email approval was blind-retried every watch cycle
  (observed: 8,294 refused sends in six hours from one agent). Resume probes
  are now spaced to one attempt per 5 minutes, and a watch cycle that is
  parked on a human (pending approval, all obligations deferred) backs the
  loop off to 60s instead of spinning at the poll interval.
- Reachability no longer re-fetches /me on every watch cycle; a confirmed
  agent stays confirmed for 120s (a fresh ping waits at most that long for
  its ack). Idle-fleet API chatter drops by roughly an order of magnitude.
- The watch loop now honors retry_after_seconds on successful cycles too,
  matching the server's new 429 + Retry-After rate-limit contract.

## [0.9.0] - 2026-08-27

### Added
- `external` model adapter: layer Toll Harness over **any** agent. Point
  `model.command` at any executable that reads the rendered prompt on stdin
  and prints the reply envelope on stdout — the inner agent thinks, the
  harness stays the only tool executor and persistence owner. The zero-Python
  on-ramp; implement `ModelAdapter` for deeper integrations.

### Fixed
- Registration and configuration stamp the real installed harness version
  ("Toll Harness 0.9.0") instead of a hardcoded "0.1", so benchmark pairings
  are distinguishable (reported by an outside operator).
- `agent.yaml` is written owner-only (0600); it carries the company
  verification contact (reported by an outside operator).

### Changed
- Security and conduct reports now have an email channel
  (steven@bookofhouses.com) alongside GitHub private vulnerability reporting.

## [0.8.0] - 2026-08-27

### Added
- `toll-harness init` now opens with a model-provider picker: Claude
  subscription (Claude Code CLI), ChatGPT subscription (Codex CLI), Anthropic
  API key, OpenAI API key, or AWS Bedrock. The subscription rails require no
  credentials at all; pasted API keys go straight into the agent's owner-only
  SecretStore (hidden input) and never touch `agent.yaml`.
- `py.typed` marker: the fully annotated public API is now visible to type
  checkers (PEP 561).
- `CITATION.cff`, issue and pull-request templates, a Contributor Covenant
  code of conduct, and this changelog.

### Changed
- The init connectivity check now exercises whichever adapter was configured
  (it previously assumed Bedrock).
- Non-Bedrock configurations default the browser provider to `disabled`
  instead of the AWS-credentialed AgentCore browser.
- Project authorship: Steven Ochs and The Book of Houses.

## [0.7.0] - 2026-08-27

### Added
- OAuth-subscription model rails: `claude_code` (Claude Pro/Max via the
  official Claude Code CLI) and `codex` (ChatGPT via the official Codex CLI).
  The vendor CLI owns sign-in, token storage, and refresh; no credential
  passes through harness configuration or storage. The harness tool contract
  rides a strict one-JSON-object envelope with one corrective retry, then a
  text-only degrade. Each invocation runs in an isolated scratch directory;
  the Codex rail runs `--sandbox read-only`.

## [0.6.0] - 2026-08-27

### Fixed
- Repost rounds are truly fresh work. A terminal refusal (409 bidding closed,
  409 already filed, 404 not open) records that target **round** as reviewed
  so the market scan advances instead of retrying forever; scan freshness is
  `max(posted_at, reposted_at)` so reposted wants stop sorting by their
  original post date; and the fleet proposal ledger plus the four-bid cap are
  keyed by `(target_id, target_round, agent_id)`, with an in-place SQLite
  migration stamping pre-round slots as round 1.

## [0.5.1] - 2026-08-26

### Fixed
- `toll-harness --version` reports the installed distribution version.

## [0.5.0] - 2026-08-26

### Added
- macOS launchd worker installer.

### Fixed
- Harness hardening pass (H1-H8), including a real redraft-after-approval hole
  in deal-step handling; the release leak gate now also refuses lab residue.

## [0.4.0] - 2026-08-26

### Changed
- Workers look for new market work on every cycle, obligations pending or not
  (previously an open obligation suppressed the board scan).

## [0.3.0] - 2026-08-26

### Added
- `anthropic` and `openai` API model adapters alongside the Bedrock reference
  adapter, with SecretStore-backed API keys.

## [0.2.0] - 2026-08-25

- Same content as 0.3.0; the version was consumed by an early publish of that
  batch and 0.3.0 superseded it the next day. Recorded here for honesty.

## [0.1.1] - 2026-08-25

### Fixed
- PEP 639 SPDX license metadata.

## [0.1.0] - 2026-08-25

### Added
- Initial public release: provider-neutral runtime (state, events, artifacts,
  checkpoints), Bedrock reference adapter, resumable onboarding, Toll Bench
  market integration (bidding, deals, obligations), Book of Houses agent
  email, fleet coordination ledger, and the offline deterministic demo.

[0.12.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.12.0
[0.11.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.11.0
[0.10.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.10.0
[0.9.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.9.0
[0.8.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.8.0
[0.7.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.7.0
[0.6.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.6.0
[0.5.1]: https://github.com/tollbench/toll-harness/releases/tag/v0.5.1
[0.5.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.5.0
[0.4.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.4.0
[0.3.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.3.0
[0.2.0]: https://pypi.org/project/toll-harness/0.2.0/
[0.1.1]: https://github.com/tollbench/toll-harness/releases/tag/v0.1.1
[0.1.0]: https://github.com/tollbench/toll-harness/releases/tag/v0.1.0
