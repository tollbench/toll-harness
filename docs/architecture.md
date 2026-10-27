# Architecture

## Runtime boundary

`ModelAdapter` receives a system instruction, normalized messages, and versioned tool definitions.
It returns normalized text, tool calls, usage, errors, and stop state. The core runtime has no
provider-specific branches.

For each wake cycle, the intelligence receives the original goal, its compact checkpoint, and only
events newer than the checkpoint cursor. Tool calls and results remain in the active wake context so
the provider can continue its own reasoning. A new wake does not replay an unlimited transcript.

The intelligence updates its checkpoint by calling `state.save`. Toll Harness never asks another
model to summarize or transform it.

An operator can opt an agent into a named knowledge namespace. In that case `state.save` may also
replace a small persistent knowledge object supplied to later runs using that namespace. There is
deliberately no automatic extraction, summarization, or version graph.

## Storage boundary

- `StateStore`: run metadata and compact checkpoints.
- `EventStore`: immutable ordered audit events.
- `ArtifactStore`: scoped working files.
- `SecretStore`: non-enumerable, explicitly named secret access for provider implementations.

Local Playwright browser profiles live beside those stores in the permanent agent's isolated data
directory, never in Book of Houses or a shared harness directory.
The local reference combines state and events in SQLite and stores artifacts on disk. These
interfaces can be replaced without changing the runtime.

## Execution states

A run is Running, Waiting, Completed, Failed, or Limit Reached. `human.request` transitions to
Waiting. `result.complete` and `result.fail` are the only model-driven terminal actions. Iteration
limits are protocol guardrails, not planning decisions.

## Context budget

A run also stops on its own INPUT TOKENS. Every model call's usage is read back from the provider,
and before the next call the runtime asks whether the prompt it is about to send -- the last
measured prompt plus whatever was appended since, four characters to a token -- would cross the
budget. When it would, the run ends Failed with `context_budget_exceeded`, naming the budget, the
last call's input tokens, the cumulative input, the last tool called and whatever target, deal or
step the run was holding. One line goes to the log per model call, carrying the cumulative input
tokens, so a run's growth is readable after the fact.

`runtime.context_budget_tokens` sets it per agent, `TOLL_HARNESS_CONTEXT_BUDGET_TOKENS` across a
fleet, and the default is 90,000; 0 turns the guard off. WHAT FORCED IT: on 2026-09-09 four
production agents died inside Bedrock on prompts over 129,000 tokens against a 131,072-token
context. A run that stops itself leaves a record and an honest failure; a run the provider stops
leaves a stack trace. The budget is a floor under a bug, not a fix for one -- when a run hits it,
the thing to shrink is what the tools hand back.

THE ROOM A PROMPT GETS FOLLOWS THE MODEL'S WINDOW (0.73.0). Every model adapter carries
`context_window_tokens`: `model.context_window_tokens` in `agent.yaml`, else
`TOLL_HARNESS_CONTEXT_WINDOW_TOKENS`, else the window `core/window.py` knows for the model id, else
131,072. The table is conservative: a plain Claude id is 200,000 and only an id carrying `[1m]` is
1,000,000; Gemini 2/3 is 1,048,576. Every character budget the harness keeps for a
prompt it builds -- the brief a tool hands back, each draft-loop ask, the step ask, the tools index,
what the person said, the operator's materials, one step -- is the number written for a
131,072-token window, times the model's window over 131,072, and never less than that number.
What still has to come off comes off in a fixed order: on the brief, the program index and the
earlier attempts first, then the example's proposal, the bid template, the tools' arguments and,
last of all, the block catalog (to its kinds and step counts); in the draft loop, the outline lines
and the forecast numbers first, then the slot tables, then the example, then the tools. The draft
loop reads the brief whole (`read_whole_brief`): it never sends the brief in one piece, so the fit
belongs to the tool result alone. WHAT FORCED IT: lab agent Nia (claude-opus-5-5), 2026-10-26, bid
and planned a song workflow without the block menu, the bid template, the example, her tools and
the forecast context, every one shed to fit a budget set for a 131,072-token window.

## Two stages: the proposal, then the plan form

There are three words and the runtime keeps them apart. A WANT is what the person asked for. A
PROPOSAL is the agent's short answer to it, and it is what the person chooses between. A PLAN is
what the chosen agent writes afterwards. Until 2026-09-11 this package wrote a whole plan for a
want nobody had picked it for: an eighteen-step, ~33KB document against 44 refusal rules, thrown
away for every agent but one.

**Stage one, the proposal: ONE model call and one POST.** Eight fields and nothing else --
`pitch_title`, `headline`, `pitch_body`, `odds`, `total_ask_cents`, `research_links`,
`finalist_questions`, `tools_needed` (`toll_bench.draft.PROPOSAL_FIELDS`). No steps, no blocks, no
account rows, no grant requests, no finish line, no wins, no capabilities, no strategy block.
`headline` (bench contract 4.0.12) is the agent's own short title, up to 40 characters, that the
person's card wears once the agent is picked: the bench trims a longer one at a word and says so
on `trimmed`, and refuses only an empty one (REJ-21), so an empty headline gets one more small
ask (`HEADLINE_INSTRUCTION`) and a long one is only logged. The ask is
`PROPOSAL_INSTRUCTION`; `read_proposal()` takes the eight fields out of whatever the model
answered and drops the rest, because a field the door does not name is a field the door will not
read. THE HARNESS DOES NOT TRIM. The caps are said out loud in the ask because a model writes
better inside a stated cap, but the DOOR owns them: it cuts a long title or paragraph to the cap
and says what it cut on `bench_fixed`, which `draft.bench_fixed()` reads and the run log carries.
A harness that trimmed too would cut the same sentence twice and hide the bench's own answer. A
proposal needs no draft door at all (`_the_proposal_road_is_open`).

**Stage two, the plan: a FORM, and only once the person has chosen.**
`PUT /api/bench/targets/{id}/proposals/draft` with `{"kind": "plan"}` and nothing else answers
`next: "form"` and hands over everything at once: `your_proposal` (the agent's own proposal, one
line per step), `the_person_answered`, `stance` (the person's own sentence about how they want it
done, built from the three sliders on their want -- it opens the prompt, because that is the kind
of instruction models follow well), `example_plan` (ONE finished plan for a want like this one, in
the same form, whole -- for a smaller model this is the single biggest lever there is), the blank
`form`, and `blanks`: every question in plain words with its choices listed. The runtime fills it
and PUTs it back as `{"kind": "plan", "form": {"span_days": N, "steps": [...]}}`.

A step is five things -- a `verb` off a closed list, three short lines (`do_line`,
`hand_over_line`, `need_line`), one `declared_odds`, a `proof` pick and a `who` pick -- plus six
picks it MAY carry: `only_if`, `do_ask`, `tool`, `repeats`, `bid_step`, and a `move` in the fix
round. THE BENCH DOES THE TYPING: no connector row, no grant request, no block title, no room
list, no `$from` pointer and no schedule row is ever written here. A line past its cap is trimmed
and an odds line that falls is raised, both reported on `bench_fixed` and neither costing a round.

**The plan door answers in ONE language** (bench contract 4.0, 2026-09-17; the minimum this
harness plans against, `book_of_houses.PLAN_DOOR_MIN_CONTRACT`). Steven: "The form says what goes
in. It never lists what is wrong." Two lists, both ALWAYS PRESENT including empty:

- `problems`, one row per thing still open: `{step, slot, problem, who_fixes, say, path, accepted,
  codes}`. `problem` is one of `empty`, `wrong_kind`, `no_source`, `unsupported`, `cycle`,
  `not_allowed`, `unknown_field`. `path` is what you PATCH, and a null `path` means re-send the
  step with PUT. `codes` are the legacy rule names, for the run log only -- NOTHING branches on
  them, because one fault told under three of them is still one fault. `next_fix` is the first
  `who_fixes: "agent"` row in step order, and it only says where to start: every row is shown at
  once.
- `form_steps`, one entry per form step saying what GOES IN it: `{step, action, person_slot,
  slots}`, each slot `{name, kind, filler, state, source, accepted, path, required}`. That table
  is what the fix round puts in front of the model, with the bench's own sentences underneath.

**Who fixes it is honoured.** A `platform` row is the bench's own bug: it never costs a model call
or a try, it shouts in the run log with its step, slot and codes, and when nothing else is left the
run stops with `bench_must_fix`. A `person` row is a wait, not a failure (`waiting_on_the_person`).
Both park the want for the ROUND, on the brake that was already there, and a repost asks again.

**Two rows are answered with no model at all.** A key the form has no room for is taken off and the
object at the row's `path` is sent back, read off the row's own `accepted` list. A step that reaches
somebody with no who step above it gets the insert call, read off the slot table: a `contact` slot
the platform fills from `person.who`, and no step marked `person_slot: "who"`.

THREE MISSES END IT: the door closes with `plan_failed`, the bench scores the agent "selected,
could not present a plan", the person is told in red and picks somebody else, and that agent may
not propose on that want again this round. `plan_failed` is therefore TERMINAL in the runtime
(`cli._TERMINAL_DOOR_ERRORS`) and is memoized like a closed want: reopening the draft cannot undo
it and the person has already moved on.

**The stall guard** is keyed on the STEP, the SLOT and the PROBLEM -- the bench's own `fix_key`, so
the harness and the try counter agree on what "the same problem" is -- and never on the words. A
model that rewords the same bad sentence looks like progress to a hash of the whole draft, and
three fleet units burned two hundred rounds each doing exactly that.

**Sending an email is `email.send`**, one neutral name whatever mail service the person has
connected; the platform picks the connector off their account. The provider-shaped names are still
taken quietly and are no longer taught.

**The BID door is untouched by 4.0** and still answers the way it always has.

**Bounds belong to the bench and are the only bounds**: 24 hours, three rounds per opening
problem, ceiling 200. There is no strike count and no round ceiling in the harness; the safety net
is the bench's own `rounds.left` reaching 0 and its `closed`. A cycle READS the draft the bench is
already holding ONCE before it writes anything (`GET .../proposals/draft?kind=plan` costs no round;
since 0.68.0 the market watch's loop-guard probe is that read and the draft loop starts from it),
and sends the EMPTY opening `PUT` only when nothing stands, because an empty PUT over a standing form
replaces it. A bench that publishes no plan door falls back to the single-shot road, which is a
proposal too.

**Every red row, every round (0.68.0).** Once a form stands, each round works the rows that need no
model first (the two above, plus a key the bench says may not be there), then asks ONE model call
with the whole form, every `who_fixes: "agent"` row (step, slot, problem, `say`, path, `accepted`;
never the codes) and the `form_steps` entry of each of those steps, and files the whole answer back
in ONE `PUT {"kind": "plan", "form": {...}}` over the standing form, which the bench carries its
rounds forward on (a full form answers the row it asked about). A form missing steps it does not
name in `drop` is never filed; an answer carrying only `odds` / `overview` / `span_days` changes
those and keeps every step; an empty, partial or unchanged answer is asked once more saying which.
The stall guard is unchanged: a row shown twice and still red the third time ends the draft. The
older outline road keeps the one-row road.

**The empty catalog is not the default catalog.** `tools_index` treats `tools: []` as "no tools on
this want", never as "no index published". Reading an empty list as unpublished put a Slack row on
every plan and is why three agents burned the full ceiling.

## What a plan costs

A PROPOSAL COSTS ONE CALL. Everything below is the plan stage, where a run may take several.

Every call of a plan run is `[stable prefix][variable tail]`. The prefix is the front door,
the block index and the bench's `tools` index -- byte for byte the same on every call of a want,
which is what lets a provider charge a fraction for it. `ModelAdapter.caches_a_stable_prefix()`
says whether that is worth doing: Anthropic marks the system block `cache_control: ephemeral`,
Bedrock Converse appends a `cachePoint` for the families that take one, OpenRouter gets the marker
an Anthropic model behind it needs, OpenAI caches by itself. The default for an unknown provider is
FALSE, and there the rules and the tools ride the first call ONCE instead of every round.

The tail is small by design: the form ask carries the want, the person's answers, the stance line,
one worked example and the blank form; a fix round on a form carries the form and every red row
(0.68.0), and on the older outline road the one content question and the plan's shape as one line
per step. Never the expanded document. Each model call logs ONE line: the want, the prompt's size
(prefix and tail), the answer's size, the output tokens and what stopped it, and what the reply read
as; each bench door answer logs one line at the HTTP client with the want/deal id, the status, the
answer's size and the door's own sentence. A reply that could not be used leaves the first 600
characters of what the model wrote, with the step it was about.

A reply the output limit cut off -- empty, or sliced part way through its JSON -- gets ONE retry
(0.68.0): the OpenAI adapter on OpenRouter asks again with the reasoning turned down when the model
reasoned; otherwise the draft loop asks the same question once more for the object alone, short.
Never both. A plan reply that never became JSON is handed back once with the parser's own words.

TWO SHELVES, AND THEY ARE NOT THE SAME SHELF. The FORM ask carries the bench's own `example_plan`
-- one finished plan for a want like this one, chosen by the bench, in the same form being filled --
and nothing of ours goes into it. The own-wins shelf is the OTHER one: before a LEGACY OUTLINE is
written (the road a bench with no plan door still walks), the loop reads this agent's own accepted
plans, picks the nearest by tool-family overlap with what the want needs, and seeds that outline
with its shape so the model is asked only to adjust it; nothing overlaps, nothing seeds. Not a
stranger's worked program, but the jobs this agent has already been picked for.

## What a step costs

A signed deal is walked one step at a time, and a step the agent can move is ONE small ask and
ONE call (`toll_bench/step.py`). The prefix is the plan loop's, byte for byte, so the provider's
cache is shared. The tail carries only this step: its title, ask and promise, what changed since
the last look, the person's own words whole, the acts and the one move that is the agent's,
with the bench's forms for that move as the tools. A move the small ask cannot shape (bytes to
hand back, a message on another step) takes the old road, the whole agentic run, and the log says
why.

WHAT THE EARLIER STEPS HANDED BACK RIDES EVERY STEP, WHOLE (0.62.0). `earlier_steps` is one block
per earlier approved step: its head line, then its hand-back line by line, each card field
(`field: value`, the way the bench flattens a card) on its own line, never folded and never
clipped; then a line per file and per act. The bench trims a hand-back at 600 characters on the
current-step answer and says so in `trimmed`; the provider then reads that receipt whole from the
bench's history (`current-step?full=1`), once per receipt. The block is capped at 6,000
characters. Over the cap the OLDEST steps go first, whole steps only; a step still too big on its
own has its free text shortened, never a card field. The block then opens with one line naming
what was left out and how to read it whole. Measured on a six-step deal (step 5, two cards before
it) the tail is about 10,500 characters, about 2,600 tokens, against about 9,100 before 0.62.0,
when the step-4 card lost its last fields.

FETCH, NEVER ASK. Every move that writes words out of earlier material -- `hand_back`, `file_act`,
`refile_act`, `answer_reply`, and `outside_work` -- may answer `need_tools` instead of making its
one call. That hands the step to the old road on purpose, where the goal carries every earlier step
whole and `toll_bench.current_step` with `whole: true` reads them again. A fact a card already
carries is never asked of the person.
