"""THE VERSION ON THE WIRE IS THE VERSION IN THE SOURCE.

WHAT FORCED IT (checker, 2026-09-17). The release commit raised `pyproject`
to 0.46.0 and the venv kept a 0.45.0 `dist-info` beside it, so every agent
that read `toll_harness.__version__` -- the number that rides the user agent
on every bench call -- announced a build that was not the one running. An
editable install does not re-read its own metadata; only a re-install does.
A number nobody checks drifts, so this checks it.
"""
from __future__ import annotations

import re
from pathlib import Path

import toll_harness

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _declared() -> str:
    for line in PYPROJECT.read_text().splitlines():
        match = re.match(r'^version\s*=\s*"([^"]+)"\s*$', line.strip())
        if match:
            return match.group(1)
    raise AssertionError("pyproject.toml declares no version")


def test_the_installed_version_is_the_declared_one():
    assert toll_harness.__version__ == _declared(), (
        "the installed metadata is stale: re-run "
        "`pip install -e . --no-deps` in the venv"
    )


def test_a_source_tree_that_was_never_installed_still_imports():
    assert re.match(r"^\d+\.\d+\.\d+", toll_harness.__version__)
