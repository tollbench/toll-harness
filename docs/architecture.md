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
