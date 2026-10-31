"""What is left of the local block mirror (0.74.0): two readers, no rules.

This module used to be a local copy of the bid door's rules for the old
plan-shaped proposal -- grant rows, template merges, a contact picker, the
deliverable table, the `calls` provenance checks -- 2,871 lines that repaired
a plan before the bench saw it. Since rule 243 (2026-09-11) a proposal carries
no steps, the plan is written at the draft door, and the bench judges every
row in its own words, so every one of those repairs ran over a document that
was not there. They are gone (harness thinness audit, item 1, 2026-10-28):
the intelligence thinks, the bench judges, the harness carries.

What stays reads the brief and writes nothing of the agent's:

* ``connected_sentence`` -- rule 231's one line saying what the person has
  already connected, always present, including "nothing yet".
* ``published_template_steps`` -- every step the brief published, the
  skeleton and the block catalog flattened, for a reader that wants both.
"""

from __future__ import annotations

from typing import Any

# The lane prefixes on a provider key: `composio:<slug>` and `key:<slug>`.
# The lane is how the platform reaches a service, never part of its name.
PROVIDER_LANES: frozenset[str] = frozenset({"composio", "key"})

# The provider keys the brief publishes, in the words a person would use.
# Anything not listed prints its own key with the dashes taken out, so a
# connector added on the platform reads sensibly here the day it ships.
CONNECTOR_WORDS: dict[str, str] = {
    "google-calendar": "Calendar",
    "google-gmail": "Gmail",
    "gmail": "Gmail",
    "google-drive": "Google Drive",
    "dropbox": "Dropbox",
}


def split_provider(provider: Any) -> tuple[str, str]:
    """``(lane, slug)`` for a provider key; the lane is "" for a plain one."""
    key = str(provider or "").strip().lower()
    lane, sep, slug = key.partition(":")
    if sep and lane in PROVIDER_LANES and slug.strip():
        return lane, slug.strip()
    return "", key


def connector_words(provider: Any) -> str:
    key = str(provider or "").strip().lower()
    if not key:
        return ""
    if key in CONNECTOR_WORDS:
        return CONNECTOR_WORDS[key]
    lane, slug = split_provider(key)
    if lane:
        key = slug
        if key in CONNECTOR_WORDS:
            return CONNECTOR_WORDS[key]
    return key.replace("google-", "").replace("-", " ").replace("_", " ").title()


def connected_sentence(person_connected: Any) -> str:
    """RULE 231: one line telling the agent what the person already has.

    ALWAYS PRESENT, including zero -- an empty list and a brief that never
    carried the field must be tellable apart from "Calendar, Gmail", and
    "nothing yet" is a fact the plan is built on.
    """
    names = [
        connector_words(item)
        for item in (person_connected if isinstance(person_connected, list) else [])
        if str(item or "").strip()
    ]
    names = [name for name in names if name]
    if not names:
        return "The person has connected nothing yet."
    return "The person already connected: " + ", ".join(names) + "."


def published_template_steps(
    plan_template: Any, block_templates: Any = None
) -> list[dict[str, Any]]:
    """Every step the PLATFORM published on this brief, flattened: the
    ``plan_template`` skeleton, then every step of ``block_templates``."""
    steps: list[dict[str, Any]] = []
    for step in plan_template if isinstance(plan_template, list) else []:
        if isinstance(step, dict):
            steps.append(step)
    if isinstance(block_templates, dict):
        for group in block_templates.values():
            for step in group if isinstance(group, list) else []:
                if isinstance(step, dict):
                    steps.append(step)
    return steps
