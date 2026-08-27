# Contributing to Toll Harness

Thank you for helping make the reference Toll Bench harness better. This
document covers the development setup, the bar for changes, and how releases
work.

## Development setup

Use Python 3.10 or newer:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[aws,browser,dev]'
```

Before proposing any change, run both gates locally — CI runs the same two on
Python 3.10 through 3.13:

```bash
.venv/bin/ruff check .
.venv/bin/pytest
```

## What we accept

- **Bug fixes with a regression test.** A fix without a test that fails on the
  old code is a resubmit.
- **New provider adapters** (models, browsers, email, benches) that implement
  the existing typed contracts. The runtime is deliberately neutral: no
  provider-specific planning, supervision, or prompt rewriting lands in it.
- **Documentation** that matches the code as shipped.

Keep pull requests focused — one change per PR, with a description of what
forced it. Behavior changes need a documentation update in the same PR.

## Licensing of contributions

By submitting a contribution you agree that it is your own work (or that you
have the right to submit it) and that it is licensed under the
[Apache License 2.0](LICENSE), consistent with the rest of the project.

## Conduct and security

We follow the [Contributor Covenant](CODE_OF_CONDUCT.md). Security issues go
through [private vulnerability reporting](SECURITY.md) — never a public issue.

## How releases are cut (maintainers)

1. Bump `version` in `pyproject.toml` and add a [CHANGELOG.md](CHANGELOG.md)
   entry in the same commit.
2. Tag `vX.Y.Z` and push the tag. GitHub Actions builds and publishes to PyPI
   via Trusted Publishing (OIDC — there is no long-lived PyPI token to leak).
3. A GitHub Release is created for the tag with the changelog entry as notes.

Versioning is SemVer with a pre-1.0 caveat: minor releases may change behavior
or configuration; patch releases never do.
