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

## The draft loop (bidding)

A plan is not written in one call. The bench holds it while it is built, and the runtime asks the
intelligence for one piece at a time (rule 241, bench contract 3.11):

0. **Read what is standing** -- the first step of every cycle. `GET .../proposals/draft` (with
   `?kind=plan` for a plan) costs no round. A draft that stands and is not closed is resumed from
   its own blanks and `next_fix`; an outline goes out ONLY when there is no draft (`404 no_draft`),
   and never more than one `PUT` in a run. A closed draft is never PUT over: the bench counts a
   repeated PUT as a round and holds a used-up draft closed until it expires. A PUT replaces the
   standing draft and zeroes the rounds, which is why the model is never handed that door.
1. **The outline.** Steps in order, each an `ask` and a `title`, and for a step that touches the
   world the `tool` and the service it runs `on`. The prompt carries the want, what the person
   said, a one-paragraph block grammar and the tools index -- `brief["tools"]`, the bench's own
   list of every call a `calls` act can name, narrowed to the tool, its service and one line. Not
   the whole brief, and no worked program: the brief carries none, and the loop reads none.
   `PUT /api/bench/targets/<id>/proposals/draft`.
2. **The blanks, a step at a time.** The bench expands every mechanic it owns (the account row for
   each service, the tool's required arguments, the platform's own statement, the approve control)
   and names every field that is the agent's as an explicit blank with one sentence saying what
   belongs there. The intelligence sees ONE step and that step's blanks, and answers with
   `{path, value}` patches. `PATCH` the same path.
3. **One `next_fix` at a time.** Every answer carries at most one thing to change: a path, its
   current value, a code and one sentence. The intelligence is shown that and the step around it,
   and nothing else. Never the whole document -- rewriting the whole document is the failure this
   loop exists to stop.

When `ready` is true the stored document is filed through the ordinary door with
`{"from_draft": true}`. The informed plan walks the same loop with `kind: "plan"`, which starts
from the steps already filed. The bench owns the bounds and they are the only bounds: 24 hours, three
rounds per opening problem, ceiling 200 -- so a thirty-step plan gets a thirty-step plan's worth of
rounds. The loop runs until `ready` or `closed`; there is no strike count and no round ceiling in
the harness, and the only safety net is the bench's own `rounds.left` reaching 0. When the bench
says `closed` the runtime opens one fresh outline and then leaves the want for that cycle. A bench
that publishes no draft door falls back to the single-shot road.
