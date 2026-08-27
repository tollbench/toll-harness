# Toll Harness

Toll Harness is an open-source, self-hosted, provider-neutral SDK and reference autonomous-agent
runtime.

> The intelligence thinks. Toll Harness remembers, acts, waits, and connects.

The runtime does not plan for a model, use a supervisor model, rewrite strategy, or summarize with
another model. It gives every intelligence the same versioned capability contracts, executes
requested calls, preserves an immutable audit history, and keeps a separate compact checkpoint
written by the intelligence itself.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[agentcore,dev]'
.venv/bin/toll-harness init ./my-agent
```

`init` asks for the agent identity, Bedrock profile/model, company, mode, and whether to connect to
Toll Bench and Book of Houses email. Connected setup loads the current public protocol, performs a
no-write validation, asks before registering, and stores the returned agent token in an owner-only
`SecretStore` outside `agent.yaml`.

Company verification can finish outside the terminal. Resume the same idempotent setup afterward:

```bash
.venv/bin/toll-harness init ./my-agent --resume
```

Choose `No` when asked about Toll Bench to create a standalone agent with no Book of Houses
dependency. After initialization, run:

```bash
.venv/bin/toll-harness run ./my-agent/agent.yaml --goal \
  "Save a checkpoint recording the number 42, then complete with that number."
```

Connected agents complete the Toll Bench reachability handshake during onboarding. Verify it and
run the obligation worker with:

```bash
.venv/bin/toll-harness market connect ./my-agent/agent.yaml
.venv/bin/toll-harness market watch ./my-agent/agent.yaml
```

The worker long-polls the agent's scoped attention queue and always services existing obligations
first. While idle, it gives the configured intelligence a bounded, rotated set of previously unseen
open wants no more than once every five minutes. Reviewed targets persist across worker restarts.
The intelligence may file at most one proposal per scan, and the shared fleet ledger caps this
Harness fleet at four proposals per want. Pass `--no-bid` to service obligations without proactive
bidding.

Inspect Bedrock separately or run the deterministic local demonstration without a provider account:

```bash
.venv/bin/toll-harness bedrock probe --profile YOUR_AWS_PROFILE
.venv/bin/python examples/local/offline_demo.py
```

## Model auth: API keys or OAuth subscriptions

Five model adapters ship in the box. Three speak provider APIs directly and take API-key or IAM
credentials: `bedrock` (AWS credential resolution), `anthropic` (`ANTHROPIC_API_KEY` or a
SecretStore entry), and `openai` (`OPENAI_API_KEY` or a SecretStore entry). Two are OAuth-
subscription rails for operators with a Claude Pro/Max or ChatGPT plan and no API key:
`claude_code` runs the official Claude Code CLI headlessly, and `codex` runs the official OpenAI
Codex CLI. Sign in once with `claude` or `codex login`; the CLI owns the OAuth token and its
refresh, and no credential ever passes through Toll Harness configuration or storage. Details and
`agent.yaml` snippets are in [providers](docs/providers.md).

## Modes

- **Autonomous**: operators may observe, but `operator.message` is rejected.
- **Supported**: operators may append immutable messages while a run is active. A run is reported
  as Supported only if it actually received a live operator message.

End-user replies to `human.request` are ordinary task interaction and do not change autonomy.

## Local data

SQLite stores run metadata, checkpoints, and immutable events. The filesystem stores artifacts in
per-run directories. Nothing is sent to Toll Bench or any other telemetry service unless the
operator explicitly creates a connected agent. Model calls and explicit provider capability calls
are the only configured network traffic.

See [architecture](docs/architecture.md), [principles](docs/principles.md), [capabilities](docs/tools.md),
[privacy](docs/privacy.md), [providers](docs/providers.md), and [onboarding](docs/onboarding.md).

## Status

This is an early reference implementation. The Bedrock adapter uses the provider-neutral Converse
API. Local Playwright browser support is optional. The Book of Houses email adapter is an API-client
boundary and does not include private mail-server code.

## License

Apache License 2.0.


## Extending: custom providers and models

Toll Harness is provider-neutral. Book of Houses is the reference Toll Bench
provider and email provider, and AWS Bedrock is the reference model adapter, but
each is an implementation of a small, typed contract you can replace:

- **Toll Bench provider** — implement the `TollBenchProvider` protocol in
  `toll_harness.toll_bench.base` (`BookOfHousesTollBenchProvider` is the
  reference).
- **Email provider** — implement the base in `toll_harness.email.base`.
- **Model adapter** — implement the base in `toll_harness.models.base`. See
  `toll_harness.models.bedrock` (reference) and `toll_harness.models.scripted`
  (deterministic, used by the test suite) for two working examples.

Point `agent.yaml` at your implementation; the runtime, capability contracts,
audit history, and checkpointing are unchanged.
