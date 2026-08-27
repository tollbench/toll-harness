# Changelog

All notable changes to Toll Harness are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) with the pre-1.0 caveat below.

**Versioning policy**: before 1.0, minor releases (0.x) may change behavior or
configuration; patch releases never do. Every release is tagged, published to
PyPI via Trusted Publishing, and mirrored here.

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
