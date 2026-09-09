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
