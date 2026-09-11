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

## Two stages: the proposal, then the plan form

There are three words and the runtime keeps them apart. A WANT is what the person asked for. A
PROPOSAL is the agent's short answer to it, and it is what the person chooses between. A PLAN is
what the chosen agent writes afterwards. Until 2026-09-11 this package wrote a whole plan for a
want nobody had picked it for: an eighteen-step, ~33KB document against 44 refusal rules, thrown
away for every agent but one.

**Stage one, the proposal: ONE model call and one POST.** Seven fields and nothing else --
`pitch_title`, `pitch_body`, `odds`, `total_ask_cents`, `research_links`, `finalist_questions`,
`tools_needed` (`toll_bench.draft.PROPOSAL_FIELDS`). No steps, no blocks, no account rows, no
grant requests, no finish line, no wins, no capabilities, no strategy block. The ask is
`PROPOSAL_INSTRUCTION`; `read_proposal()` takes the seven fields out of whatever the model
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

**Only content comes back as a question**, and only four things count as content: nothing came
back, the plan does not address the want, a step makes the person do the agent's work, or a step
names a tool the agent cannot reach. Each arrives in plain words with the choices listed, never a
rule code. THREE MISSES END IT: the door closes with `plan_failed`, the bench scores the agent
"selected, could not present a plan", the person is told in red and picks somebody else, and that
agent may not propose on that want again this round. `plan_failed` is therefore TERMINAL in the
runtime (`cli._TERMINAL_DOOR_ERRORS`) and is memoized like a closed want: reopening the draft
cannot undo it and the person has already moved on.

**The stall guard** is keyed on the PATH and the CODE, never on the words. A model that rewords
the same bad sentence looks like progress to a hash of the whole draft, and three fleet units
burned two hundred rounds each doing exactly that.

**Bounds belong to the bench and are the only bounds**: 24 hours, three rounds per opening
problem, ceiling 200. There is no strike count and no round ceiling in the harness; the safety net
is the bench's own `rounds.left` reaching 0 and its `closed`. A run READS the draft the bench is
already holding before it opens one (`GET .../proposals/draft?kind=plan` costs no round) and sends
at most ONE `PUT` per run, because a PUT replaces the standing draft and zeroes the rounds. A
bench that publishes no plan door falls back to the single-shot road, which is a proposal too.

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
one worked example and the blank form; a fix round carries the one content question and the plan's
shape as one line per step. Never the whole document. Each ask logs its own size and the cached
share the provider reported.

TWO SHELVES, AND THEY ARE NOT THE SAME SHELF. The FORM ask carries the bench's own `example_plan`
-- one finished plan for a want like this one, chosen by the bench, in the same form being filled --
and nothing of ours goes into it. The own-wins shelf is the OTHER one: before a LEGACY OUTLINE is
written (the road a bench with no plan door still walks), the loop reads this agent's own accepted
plans, picks the nearest by tool-family overlap with what the want needs, and seeds that outline
with its shape so the model is asked only to adjust it; nothing overlaps, nothing seeds. Not a
stranger's worked program, but the jobs this agent has already been picked for.
