"""A FILE COMES IN BY ITS PATH: the mirror of a file going back by its path (wake.attach_files).

Staging, Nov 15: Steven posted a want and the agent Sam asked him on step 2 to "Upload her
full-body photo". He did. The bench kept it privately and released it to Sam alone, behind a
download door that takes the agent's own key. But Sam was woken with the photo as a bare id,
the woken model has no key, and the harness had no way to bring a file in. Sam wrote: "photo
arrived only as an upload ID, not viewed".

So before an agent known by name is woken, the watch (the one process that holds the key)
fetches each file the item lists into a folder made for this item and writes down what it
brought. The bridge moves the files into the scratch folder the agent is started in and the
prompt names each one by its path. The folder goes when the item ends: nothing is kept.

- The key goes only to the bench: only a bench path (/api/bench/...) joined to the bench's own
  address is fetched, and a redirect is never followed (bench.download).
- The key never reaches the model: it stays in the watch. The bridge gets only the folder's
  path, and the agent's program does not even get that.
- Bytes land only inside the folder, under a safe name that never overwrites another.
- Sizes are the bench's own mailbox caps, counted as the bytes arrive; a fingerprint
  (sha256) is checked when the item carries one.
- A file that cannot be brought in never fails the item: the agent is woken and told which
  file is missing and why, in a few words, so it can say so instead of pretending it saw it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from toll_harness.bench import BenchError, bench_path

FILES_ENV = "TOLL_ITEM_FILES"  # the folder's path, for the bridge only (never item text)
MANIFEST = "files.json"  # what the watch brought and what it could not, read by the bridge

# Where the item lists its files, read in this order (the platform's lane FILE, Nov 15: the
# step item's context.inputs.files, always present; before it, the current-step read's
# context.released_materials). A rename on the bench is a one-line change here.
LISTS = (("context", "inputs", "files"), ("context", "released_materials"))
URL_KEYS = ("download_url", "url", "path")
SIZE_KEYS = ("size_bytes", "size")
NAME_KEYS = ("filename", "name")
STEP_KEYS = ("step",)
ASKED_KEYS = ("asked",)
FROM_KEY = "from"
# The folder a file lands in, by who handed it in. An entry that names no "from" is the
# person's (the released mailbox files carry none). A "from" the harness does not know yet
# still comes in, under from_bench/, so a new kind is never dropped.
FOLDERS = {"person": "from_person", "agent": "from_earlier_steps"}
OTHER_FOLDER = "from_bench"
NO_FROM = "person"

MAX_FILE_BYTES = 50 * 1024 * 1024  # the bench's own mailbox cap for one file
MAX_ITEM_BYTES = 100 * 1024 * 1024  # and for one item, across every folder
MAX_FILES = 30
# Time: fetching happens before the agent's own time limit starts, so it never eats it, but it
# is bounded so a slow bench cannot hold the item. 120 s fits a 50 MB file at a slow 0.5 MB/s;
# 300 s for all of them is a third of the default 15-minute limit; 30 s of silence on the line
# (the bench client's own timeout) gives a file up.
FILE_SECONDS = 120.0
ALL_SECONDS = 300.0
READ_SECONDS = 30.0
NAME_BYTES = 120  # a saved name's length, well under every file system's 255

NOT_FETCHED = "could not be fetched from the bench"
NO_COPY = "the bench keeps no copy"
_UNSAFE = set('/\\:*?"<>|')
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _CannotSave(Exception):
    """Writing to this computer failed (kept apart from the line's own OSErrors)."""


def _first(entry: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((entry[key] for key in keys if entry.get(key) not in (None, "")), None)


def files_in(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The files the item says were handed over: the first list found at LISTS, else none."""
    for where in LISTS:
        value: Any = item
        for key in where:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
    return []


def folder_for(entry: dict[str, Any]) -> str:
    kind = entry.get(FROM_KEY)
    kind = NO_FROM if kind in (None, "") else str(kind)
    return FOLDERS.get(kind, OTHER_FOLDER)


def _split(name: str) -> tuple[str, str]:
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem or not 0 < len(extension) <= 16:
        return name, ""
    return stem, "." + extension


def safe_name(filename: Any, number: int) -> str:
    """The name a file is saved under: the last part of what it was called, printable, no
    separators or leading dots, at most NAME_BYTES with its extension kept; file-<n> if none."""
    text = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join("_" if char in _UNSAFE else char for char in text if char.isprintable())
    text = text.strip(" .")
    stem, extension = _split(text)
    stem = stem.encode("utf-8")[:NAME_BYTES - len(extension.encode("utf-8"))]
    stem = stem.decode("utf-8", errors="ignore").strip(" .")
    return stem + extension if stem else f"file-{number}"


def _unique(folder: Path, name: str, used: set[str]) -> str:
    stem, extension = _split(name)
    candidate, count = name, 1
    while candidate.casefold() in used or (folder / candidate).exists():
        count += 1
        candidate = f"{stem}-{count}{extension}"
    used.add(candidate.casefold())
    return candidate


def size_words(count: int) -> str:
    if count >= 1024 * 1024:
        return f"{count / (1024 * 1024):.1f} MB"
    if count >= 1024:
        return f"{count / 1024:.0f} KB"
    return f"{count} bytes"


def _whole(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _line(value: Any, most: int = 200) -> str:
    return " ".join(str(value or "").split())[:most]


def bring_in(bench: Any, item: dict[str, Any], folder: Path, log: Any,
             label: str = "item") -> dict[str, Any]:
    """Fetch the item's files into `folder` and write the manifest the bridge reads. Never
    raises for one file: each is brought in or listed as missing, with why."""
    folder = Path(folder)
    root = folder.resolve()
    brought: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    used: dict[str, set[str]] = {}
    total = 0
    deadline = time.monotonic() + ALL_SECONDS
    for number, entry in enumerate(files_in(item), 1):
        sub = folder_for(entry)
        name = safe_name(_first(entry, NAME_KEYS), number)
        about = {"from": str(entry.get(FROM_KEY) or NO_FROM)[:40],
                 "step": _whole(_first(entry, STEP_KEYS)),
                 "asked": _line(_first(entry, ASKED_KEYS)) or None,
                 "content_type": _line(entry.get("content_type"), 100) or None}
        if number > MAX_FILES:
            why, saved = f"more than {MAX_FILES} files on one item", None
        else:
            try:
                why, saved = _fetch_one(bench, entry, root, sub, name, used,
                                        budget=MAX_ITEM_BYTES - total, deadline=deadline)
            except Exception as error:  # noqa: BLE001 - one file never fails the item
                why, saved = NOT_FETCHED, None
                log.line(f"{label}: {name} hit {type(error).__name__} while it was fetched")
        if saved is not None:
            path, count = saved
            total += count
            brought.append(dict(about, path=path, size_bytes=count))
            log.line(f"{label}: brought in {path} ({size_words(count)})")
        else:
            missing.append(dict(about, name=name, why=why))
            log.line(f"{label}: not brought in {sub}/{name}: {why}")
    manifest = {"files": brought, "missing": missing}
    (folder / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


def _fetch_one(bench: Any, entry: dict[str, Any], root: Path, sub: str, name: str,
               used: dict[str, set[str]], *, budget: int, deadline: float):
    """(why, None) or (None, (path in the folder, bytes saved))."""
    # The first address key the entry has decides: a null or empty download_url is the bench
    # saying it keeps no copy of that file (lane FILE, Nov 15), so nothing is asked for.
    key = next((name for name in URL_KEYS if name in entry), None)
    address = entry.get(key) if key else None
    if address in (None, ""):
        return NO_COPY, None
    path = bench_path(address)
    if path is None:
        return "not a bench address", None
    claimed = _whole(_first(entry, SIZE_KEYS))
    if claimed is not None and claimed > MAX_FILE_BYTES:
        return f"too large to bring in ({size_words(claimed)})", None
    over_item = f"past the {MAX_ITEM_BYTES // (1024 * 1024)} MB one item may bring in"
    if budget <= 0 or (claimed is not None and claimed > budget):
        return over_item, None
    left = deadline - time.monotonic()
    if left <= 0:
        return "no time was left to fetch it", None
    where = root / sub
    where.mkdir(exist_ok=True)
    name = _unique(where, name, used.setdefault(sub, set()))
    target = (where / name).resolve()
    if root not in target.parents:
        return "could not be saved on this computer", None
    most = min(MAX_FILE_BYTES, budget)
    digest = hashlib.sha256()
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return "could not be saved on this computer", None
    finished = False
    try:
        with os.fdopen(descriptor, "wb") as handle:

            def write(block: bytes) -> None:
                try:
                    handle.write(block)
                except OSError as error:
                    raise _CannotSave() from error
                digest.update(block)

            count = bench.download(path, write, most=most, seconds=min(FILE_SECONDS, left),
                                   timeout=READ_SECONDS)
        want = str(entry.get("sha256") or "").strip().lower()
        if _SHA256.fullmatch(want) and digest.hexdigest() != want:
            return "did not match its fingerprint", None
        finished = True
        return None, (f"{sub}/{name}", count)
    except _CannotSave:
        return "could not be saved on this computer", None
    except BenchError as error:
        if error.code == "too_slow":
            return "took too long to fetch", None
        if error.code == "too_large":
            length = _whole(error.body.get("length"))
            if most < MAX_FILE_BYTES and (length is None or length <= MAX_FILE_BYTES):
                return over_item, None
            return "too large to bring in (" + (
                size_words(length) if length else f"over {size_words(MAX_FILE_BYTES)}") + ")", None
        if error.code == "not_a_bench_path":
            return "not a bench address", None
        return NOT_FETCHED, None
    finally:
        if not finished:
            target.unlink(missing_ok=True)


# -- the bridge's half ------------------------------------------------------------------
def read_brought(folder: str | None) -> dict[str, Any] | None:
    """What the watch brought for this item, or None (no files, or no folder)."""
    if not folder:
        return None
    try:
        value = json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not (value.get("files") or value.get("missing")):
        return None
    return value


def move_into(folder: str | None, work: Path) -> None:
    """Move the brought files into the scratch folder the agent is started in."""
    if not folder:
        return
    for sub in (*FOLDERS.values(), OTHER_FOLDER):
        source = Path(folder) / sub
        if source.is_dir() and not source.is_symlink():
            try:
                shutil.move(str(source), str(Path(work) / sub))
            except OSError:
                pass  # the agent finds the folder missing; a file never fails the item


def _whose(row: dict[str, Any]) -> str:
    step = row.get("step")
    on_step = f" on step {step}" if isinstance(step, int) else ""
    kind = row.get("from")
    if kind == "person":
        if row.get("asked"):
            return f'the person\'s answer to "{row["asked"]}"{on_step}'
        return f"from the person{on_step}"
    if kind == "agent":
        return f"handed in{on_step or ' on an earlier step'}"
    return f"from the bench{on_step}"


def note_for(brought: dict[str, Any] | None) -> str:
    """The prompt's few lines about the files: where each one is and whose it is, and which
    could not be brought in. Empty when the item named none."""
    if not brought:
        return ""
    lines = []
    files = [row for row in brought.get("files") or [] if isinstance(row, dict)]
    if files:
        lines.append("Files for this item are in the folder you were started in. Open them "
                     "with your own file tools:")
        for row in files:
            size = size_words(_whole(row.get("size_bytes")) or 0)
            lines.append(f"- {row.get('path')} ({row.get('content_type') or 'file'}, {size}), "
                         f"{_whose(row)}")
    missing = [row for row in brought.get("missing") or [] if isinstance(row, dict)]
    for words, rows in (("Not available", [r for r in missing if r.get("why") == NO_COPY]),
                        ("Not fetched", [r for r in missing if r.get("why") != NO_COPY])):
        if rows:
            lines.append(f"{words}: " + "; ".join(
                f"{row.get('name')} ({row.get('why')})" for row in rows) + ".")
    return "\n".join(lines)
