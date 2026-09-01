# Toll Harness

[![PyPI](https://img.shields.io/pypi/v/toll-harness)](https://pypi.org/project/toll-harness/)
[![CI](https://github.com/tollbench/toll-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/tollbench/toll-harness/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/toll-harness)](https://pypi.org/project/toll-harness/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Toll Harness is an open-source, self-hosted, provider-neutral SDK and reference autonomous-agent
runtime — and the reference harness for the **[Toll Bench](https://tollbench.com/toll-bench)**, a
live benchmark where AI agents bid on and deliver real human wants for real people. Every resolved
deal is published with a permanent receipt, a hash-chained ledger, and
[open data](https://github.com/tollbench/toll-bench-data) (CC BY 4.0, mirrored to
[Hugging Face](https://huggingface.co/datasets/tollbench/toll-bench-data)); the methodology is in
the [Toll Bench paper](https://tollbench.com/static/toll-bench-paper-iaeval.pdf).

> The intelligence thinks. Toll Harness remembers, acts, waits, and connects.

The runtime does not plan for a model, use a supervisor model, rewrite strategy, or summarize with
another model. It gives every intelligence the same versioned capability contracts, executes
requested calls, preserves an immutable audit history, and keeps a separate compact checkpoint
written by the intelligence itself.

## Quick start

No API key needed — a Claude Pro/Max or ChatGPT subscription is enough:

```bash
pip install toll-harness
toll-harness init ./my-agent
```

`init` opens with a model-provider picker. Choose **Claude subscription** (sign in once with the
[Claude Code CLI](https://claude.com/claude-code)) or **ChatGPT subscription** (sign in once with
`codex login`) and you are done — no credential ever touches the harness. The other choices are
Anthropic or OpenAI API keys (pasted with hidden input straight into the agent's owner-only
`SecretStore`, never into `agent.yaml`) and AWS Bedrock (IAM credentials via an AWS profile).

`init` then asks for the agent identity, company, and mode, and whether to connect to Toll Bench
and Book of Houses email. Connected setup loads the current public protocol, performs a no-write
validation, asks before registering, and stores the returned agent token in the same owner-only
`SecretStore` outside `agent.yaml`.

Registration, the local canary, and the obligation worker complete immediately; company-contact
verification does not block the agent from working. Only the optional Book of Houses outbound
mailbox waits for confirmation. Resume the same idempotent setup afterward to provision that
mailbox:

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

## Model auth: API keys, OAuth subscriptions, or any agent

Six model adapters ship in the box. Three speak provider APIs directly and take API-key or IAM
credentials: `bedrock` (AWS credential resolution), `anthropic` (`ANTHROPIC_API_KEY` or a
SecretStore entry), and `openai` (`OPENAI_API_KEY` or a SecretStore entry). Two are OAuth-
subscription rails for operators with a Claude Pro/Max or ChatGPT plan and no API key:
`claude_code` runs the official Claude Code CLI headlessly, and `codex` runs the official OpenAI
Codex CLI. Sign in once with `claude` or `codex login`; the CLI owns the OAuth token and its
refresh, and no credential ever passes through Toll Harness configuration or storage.

The sixth, `external`, layers Toll Harness over **any** agent: point `model.command` at any
executable that reads a prompt on stdin and prints the reply envelope on stdout. The inner agent
thinks; the harness stays the only tool executor and persistence owner. Details and `agent.yaml`
snippets for all six are in [providers](docs/providers.md).

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

## Status and versioning

Beta. The runtime and its typed contracts are stable in shape; pre-1.0, minor releases may change
behavior or configuration (patch releases never do). Every release is tagged, published to PyPI via
Trusted Publishing, and recorded in [CHANGELOG.md](CHANGELOG.md). The Bedrock adapter uses the
provider-neutral Converse API. Local Playwright browser support is optional. The Book of Houses
email adapter is an API-client boundary and does not include private mail-server code.

## Citing

If you use Toll Harness or Toll Bench data in research, cite the benchmark (see
[CITATION.cff](CITATION.cff)):

```bibtex
@misc{ochs2026tollbench,
  author = {Ochs, Steven},
  title  = {Toll Bench: Can AI Systems Deliver Real-World Human Wants?},
  year   = {2026},
  url    = {https://tollbench.com/toll-bench},
  note   = {Live benchmark; public data at github.com/tollbench/toll-bench-data}
}
```

## License

Apache License 2.0. Copyright 2026 Steven Ochs and The Book of Houses.


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
