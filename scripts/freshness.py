"""Refuse a stale or already-taken Toll Harness version before a build, a publish or a lab install.

What forced it: on 2026-10-26 a harness build started from a stale local copy at 0.68.0
while 0.69.0 through 0.72.0 were already on PyPI, and named itself "0.69.0", a version
that was taken. Nothing caught it. PyPI would only have refused at upload, and a wheel
built locally could have been installed onto a live agent with no check at all.

Three checks:

- checkout: after a fetch, this git checkout's HEAD contains origin/main, so it is not
  behind upstream.
- version: the version in pyproject.toml is strictly newer than PyPI's newest.
- wheel: a wheel about to be installed is either PyPI's own file for its version (same
  sha256), or strictly newer than PyPI's newest and built from a checkout that passes
  the checkout check.

Where they run (each is a subcommand of ``python -m toll_harness.freshness``):

- ``release``: checkout + version. Run it before cutting a release.
- ``build``: what the build hook (``hatch_build.py``) runs on a build from a git checkout.
  It only WARNS and never stops a build: this is a public package, and an old tag must keep
  building for whoever wants it. A rebuild of PyPI's newest version and a checkout sitting
  on its own release tag pass quietly. The gates are ``publish`` and ``wheel``, which fail
  closed.
- ``publish``: what .github/workflows/publish.yml runs before building a tag: the tag
  matches pyproject.toml, the version is newer than PyPI's newest, and the tagged commit
  is on origin/main.
- ``wheel``: what scripts/install_on_agent.sh runs before it touches an agent.

Every check prints one plain line, and any failure exits 1 (``build`` always exits 0).
``TOLL_HARNESS_FRESHNESS_OVERRIDE=<reason>`` turns a failure into a pass and prints the reason
loudly.

This file uses only the standard library and never imports toll_harness, so the build
hook can load it by path inside a bare build environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, TextIO

PYPI_URL = "https://pypi.org/pypi/toll-harness/json"
OVERRIDE_ENV = "TOLL_HARNESS_FRESHNESS_OVERRIDE"
REMOTE = "origin"
BRANCH = "main"
UPSTREAM = f"{REMOTE}/{BRANCH}"
NETWORK_TIMEOUT_SECONDS = 15
GIT_TIMEOUT_SECONDS = 120

OK, FAIL, WARN = "ok", "FAIL", "warn"
Result = tuple[str, str]


class NetworkError(RuntimeError):
    """PyPI could not be reached, or answered with something unreadable."""


# Same ordering as toll_harness.updates.version_key (PEP 440-lite). Kept here because this
# file must load without the package; tests/unit/test_freshness.py holds the two together.
_VERSION = re.compile(
    r"^\s*v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre>a|alpha|b|beta|c|rc|pre|preview)[-_.]?(?P<pre_n>\d*))?"
    r"(?:[-_.]?(?:post|rev|r)[-_.]?(?P<post>\d*))?"
    r"(?:[-_.]?dev[-_.]?(?P<dev>\d*))?"
    r"(?:\+.*)?\s*$",
    re.IGNORECASE,
)
_PRE_RANK = {"a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}


def version_key(value: Any) -> tuple | None:
    match = _VERSION.match(str(value or ""))
    if not match:
        return None
    release = [int(part) for part in match.group("release").split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    pre, dev, post = match.group("pre"), match.group("dev"), match.group("post")
    if pre:
        phase = (_PRE_RANK[pre.lower()], int(match.group("pre_n") or 0))
    elif dev is not None and post is None:
        phase = (0, 0)
    else:
        phase = (4, 0)
    post_n = int(post or 0) + 1 if post is not None else 0
    dev_n = int(dev or 0) if dev is not None else 1 << 30
    return (tuple(release), phase, post_n, dev_n)


# --- PyPI -------------------------------------------------------------------------------


def fetch_pypi(url: str = PYPI_URL) -> dict[str, list[dict[str, Any]]]:
    """Every release PyPI lists for toll-harness: version -> its files. Raises NetworkError."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "toll-harness-freshness"}
    )
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:  # noqa: BLE001 - every failure here means "could not read PyPI"
        raise NetworkError(f"could not read {url}: {error}") from error
    releases = payload.get("releases") if isinstance(payload, dict) else None
    if not isinstance(releases, dict) or not releases:
        raise NetworkError(f"{url} answered without a list of releases")
    return {str(version): list(files or []) for version, files in releases.items()}


def newest(releases: dict[str, Any]) -> str | None:
    keyed = [(version_key(v), v) for v in releases if version_key(v) is not None]
    return max(keyed)[1] if keyed else None


def _published(version: str, releases: dict[str, Any]) -> str | None:
    """The name PyPI lists for this version (taken even if yanked), or None."""
    key = version_key(version)
    return next((v for v in releases if key is not None and version_key(v) == key), None)


def _pypi(results: list[Result], *, closed: bool) -> dict[str, list[dict[str, Any]]] | None:
    try:
        return fetch_pypi()
    except NetworkError as error:
        if closed:
            results.append((FAIL, (
                f"pypi: {error}. Failing closed: without PyPI's list nothing can tell whether "
                f"this version is already taken. Retry when online, or set {OVERRIDE_ENV}=<reason>."
            )))
        else:
            results.append((WARN, (
                f"pypi: {error}. The version was not checked here; the publish step and the "
                "lab install helper check it again and fail closed."
            )))
        return None


# --- git --------------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )


def _last_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def pyproject_version(text: str) -> str | None:
    """The [project] version from pyproject.toml text (no tomllib on Python 3.10)."""
    section = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
        elif section == "[project]":
            match = re.match(r"""version\s*=\s*["']([^"']+)["']""", line)
            if match:
                return match.group(1)
    return None


def _read_version(repo: Path) -> str | None:
    try:
        return pyproject_version((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except OSError:
        return None


def _fetch_upstream(repo: Path, *, closed: bool) -> tuple[list[Result], bool]:
    """Fetch origin/main. Returns (results, go_on): go_on is False once a FAIL is decided."""
    try:
        inside = _git(repo, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        return [(FAIL if closed else WARN, "checkout: git is not installed, so nothing can "
                                           f"tell whether this checkout is behind {UPSTREAM}.")
                ], False
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return [(FAIL, f"checkout: {repo} is not a git checkout.")], False
    if _git(repo, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        return [(FAIL, "checkout: this is a shallow clone, so git cannot see whether HEAD "
                       f"contains {UPSTREAM}. Run: git fetch --unshallow {REMOTE}")], False
    fetch = _git(repo, "fetch", "--quiet", REMOTE,
                 f"+refs/heads/{BRANCH}:refs/remotes/{UPSTREAM}")
    if fetch.returncode == 0:
        return [], True
    why = _last_line(fetch.stderr) or f"exit {fetch.returncode}"
    if closed:
        return [(FAIL, f"checkout: git fetch {REMOTE} failed ({why}). Failing closed: without a "
                       f"fresh {UPSTREAM} nothing can tell whether this checkout is behind it. "
                       f"Retry when online, or set {OVERRIDE_ENV}=<reason>.")], False
    known = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{UPSTREAM}")
    if known.returncode != 0:
        return [(WARN, f"checkout: git fetch {REMOTE} failed ({why}) and there is no {UPSTREAM} "
                       "to compare against; the checkout was not checked here.")], False
    return [(WARN, f"checkout: git fetch {REMOTE} failed ({why}); comparing against "
                   f"{UPSTREAM} as last fetched.")], True


def check_checkout(
    repo: Path, *, closed: bool = True, release_tag: str | None = None
) -> list[Result]:
    """(a) HEAD contains origin/main after a fetch. ``release_tag`` (build only) also lets a
    checkout sitting on that tag pass when the tagged commit is on origin/main."""
    results, go_on = _fetch_upstream(repo, closed=closed)
    if not go_on:
        return results
    head = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip() or "HEAD"
    contains = _git(repo, "merge-base", "--is-ancestor", UPSTREAM, "HEAD")
    if contains.returncode == 0:
        return results + [(OK, f"checkout: HEAD ({head}) contains {UPSTREAM}; "
                               "it is not behind upstream.")]
    if contains.returncode != 1:
        return results + [(FAIL, f"checkout: git could not compare HEAD with {UPSTREAM} "
                                 f"({_last_line(contains.stderr)}).")]
    if release_tag:
        tags = _git(repo, "tag", "--points-at", "HEAD").stdout.split()
        on_main = _git(repo, "merge-base", "--is-ancestor", "HEAD", UPSTREAM).returncode == 0
        if release_tag in tags and on_main:
            return results + [(OK, f"checkout: HEAD ({head}) is the release tag {release_tag}, "
                                   f"which is on {UPSTREAM}.")]
    missing = _git(repo, "rev-list", "--count", f"HEAD..{UPSTREAM}").stdout.strip() or "some"
    theirs = pyproject_version(_git(repo, "show", f"{UPSTREAM}:pyproject.toml").stdout) or "?"
    ours = _read_version(repo) or "?"
    return results + [(FAIL, (
        f"checkout: HEAD ({head}) is missing {missing} commit(s) that are on {UPSTREAM}. "
        f"{UPSTREAM} is at {theirs}; this checkout says {ours}. Start from {UPSTREAM} "
        f"(git fetch {REMOTE} && git switch -c <branch> {UPSTREAM}), or rebase onto it."
    ))]


def check_on_main(repo: Path) -> list[Result]:
    """The commit at HEAD (a tag being published) is on origin/main. Fails closed."""
    results, go_on = _fetch_upstream(repo, closed=True)
    if not go_on:
        return results
    head = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip() or "HEAD"
    if _git(repo, "merge-base", "--is-ancestor", "HEAD", UPSTREAM).returncode == 0:
        return results + [(OK, f"publish: the tagged commit ({head}) is on {UPSTREAM}.")]
    return results + [(FAIL, f"publish: the tagged commit ({head}) is not on {UPSTREAM}. "
                             "Tag a commit only after it has merged to main.")]


# --- the version and the wheel ----------------------------------------------------------


def check_version(
    version: str | None, releases: dict[str, Any], *, allow_rebuild: bool = False
) -> list[Result]:
    """(b) the version is strictly newer than PyPI's newest."""
    key, top = version_key(version), newest(releases)
    if key is None:
        return [(FAIL, f"version: cannot read the version {version!r} from pyproject.toml.")]
    if top is None or key > version_key(top):
        return [(OK, f"version: {version} is newer than PyPI's newest, {top or 'none'}.")]
    if allow_rebuild and key == version_key(top):
        return [(OK, (
            f"version: {version} is PyPI's newest and already published, so this build is a "
            "rebuild, not a release. A wheel built here is that release only if it is "
            "byte-identical to PyPI's file; the lab install helper checks."
        ))]
    if _published(version, releases):
        return [(FAIL, (
            f"version: {version} is already on PyPI (the newest there is {top}). A release "
            f"must be newer than {top}: start from an up-to-date {UPSTREAM} and bump from there."
        ))]
    return [(FAIL, f"version: {version} is older than PyPI's newest, {top}. This checkout is "
                   "behind the published releases.")]


def wheel_metadata(path: Path) -> tuple[str, str] | None:
    """(name, version) from a wheel's METADATA, or None when it cannot be read."""
    try:
        with zipfile.ZipFile(path) as archive:
            member = next(
                (n for n in archive.namelist()
                 if n.count("/") == 1 and n.endswith(".dist-info/METADATA")),
                None,
            )
            if member is None:
                return None
            text = archive.read(member).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile):
        return None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            break
        name, _, value = line.partition(":")
        fields.setdefault(name.strip().lower(), value.strip())
    if fields.get("name") and fields.get("version"):
        return fields["name"], fields["version"]
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkout_of(wheel: Path) -> Path | None:
    """A wheel in <checkout>/dist/ came from that checkout."""
    folder = wheel.resolve().parent
    if folder.name == "dist" and (folder.parent / ".git").exists():
        return folder.parent
    return None


def check_wheel(
    target: str, releases: dict[str, Any], *, checkout: Path | None = None
) -> list[Result]:
    """(c) what may be installed: a published version, PyPI's own file, or a newer wheel
    built from a checkout that is not behind origin/main."""
    top = newest(releases) or "none"
    if not target.endswith(".whl"):
        published = _published(target, releases)
        if version_key(target) is None:
            return [(FAIL, f"wheel: {target!r} is neither a wheel file nor a version.")]
        if published:
            return [(OK, f"wheel: {published} is a published release; pip installs PyPI's "
                         "own file.")]
        return [(FAIL, f"wheel: {target} is not on PyPI (the newest there is {top}). To install "
                       "a build that is not published yet, pass its wheel file.")]
    path = Path(target)
    if not path.is_file():
        return [(FAIL, f"wheel: no such file: {path}")]
    meta = wheel_metadata(path)
    if meta is None:
        return [(FAIL, f"wheel: {path.name} has no readable METADATA, so nothing can vouch "
                       "for it.")]
    name, version = meta
    if re.sub(r"[-_.]+", "-", name).lower() != "toll-harness":
        return [(FAIL, f"wheel: {path.name} is {name}, not toll-harness.")]
    published = _published(version, releases)
    if published:
        digest = _sha256(path)
        sums = {(f.get("digests") or {}).get("sha256") for f in releases[published]}
        if digest in sums:
            return [(OK, f"wheel: {path.name} is byte-identical to PyPI's file for {published} "
                         f"(sha256 {digest[:12]}).")]
        return [(FAIL, (
            f"wheel: {path.name} says {version}, which is published, but it is not PyPI's "
            f"file (sha256 {digest[:12]} matches none of PyPI's). It was built from other code "
            f"under a taken name. Install the published release instead: pass {published}, "
            "not the wheel."
        ))]
    key, top_key = version_key(version), version_key(newest(releases))
    if key is None:
        return [(FAIL, f"wheel: {path.name} carries a version nothing can read: {version!r}.")]
    if top_key is not None and not key > top_key:
        return [(FAIL, (
            f"wheel: {version} is not published and is older than PyPI's newest, {top}. It was "
            "built from a checkout that is behind the published releases."
        ))]
    results: list[Result] = [(OK, f"wheel: {version} is newer than PyPI's newest, {top}.")]
    repo = checkout or _checkout_of(path)
    if repo is None:
        return results + [(FAIL, (
            f"wheel: {version} is not published yet, so it must come from an up-to-date "
            f"checkout, and nothing says which checkout built {path.name}. Pass --checkout DIR "
            "(a wheel in <checkout>/dist/ is matched to its checkout on its own)."
        ))]
    ours = _read_version(repo)
    if version_key(ours) != version_key(version):
        return results + [(FAIL, (
            f"wheel: the checkout {repo} says {ours}, but the wheel says {version}; the wheel "
            "was not built from this checkout as it stands."
        ))]
    return results + check_checkout(repo, closed=True)


# --- the modes --------------------------------------------------------------------------


def run_release(repo: Path) -> list[Result]:
    results = check_checkout(repo, closed=True)
    releases = _pypi(results, closed=True)
    if releases is not None:
        results += check_version(_read_version(repo), releases)
    return results


def run_build(repo: Path, version: str | None = None) -> list[Result]:
    version = version or _read_version(repo)
    results = check_checkout(repo, closed=False, release_tag=f"v{version}")
    releases = _pypi(results, closed=False)
    if releases is not None:
        results += check_version(version, releases, allow_rebuild=True)
    return results


def run_publish(repo: Path, tag: str) -> list[Result]:
    tag = tag.removeprefix("refs/tags/")
    ours = _read_version(repo)
    results: list[Result] = []
    if version_key(tag.lstrip("v")) is None or version_key(tag.lstrip("v")) != version_key(ours):
        results.append((FAIL, f"publish: the tag {tag} does not match pyproject.toml's version "
                              f"{ours}."))
    else:
        results.append((OK, f"publish: the tag {tag} matches pyproject.toml ({ours})."))
    releases = _pypi(results, closed=True)
    if releases is not None:
        results += check_version(ours, releases)
    return results + check_on_main(repo)


def run_wheel(target: str, checkout: Path | None = None) -> list[Result]:
    results: list[Result] = []
    releases = _pypi(results, closed=True)
    if releases is not None:
        results += check_wheel(target, releases, checkout=checkout)
    return results


def report(title: str, results: list[Result], *, stream: TextIO | None = None) -> int:
    """Print every line; 0 when nothing failed or the override is set, else 1."""
    out = stream or sys.stdout
    print(f"toll-harness freshness: {title}", file=out)
    for status, line in results:
        print(f"  {status:<4}  {line}", file=out)
    failed = sum(1 for status, _ in results if status == FAIL)
    if not failed:
        print("Fresh.", file=out)
        return 0
    reason = os.environ.get(OVERRIDE_ENV, "").strip()
    if reason:
        bar = "!" * 78
        print(bar, file=out)
        print(f"!! FRESHNESS OVERRIDE: {failed} check(s) above FAILED and are being let through.",
              file=out)
        print(f"!! Reason given: {reason}", file=out)
        print(f"!! Set by {OVERRIDE_ENV}. Whoever set it owns what ships.", file=out)
        print(bar, file=out)
        return 0
    print(f"Not fresh: {failed} check(s) failed. To go on anyway, on purpose: "
          f"{OVERRIDE_ENV}=<reason>", file=out)
    return 1


def warn_only(title: str, results: list[Result], *, stream: TextIO | None = None) -> int:
    """The build's report: every failure is printed as a loud WARN, and it always returns 0."""
    out = stream or sys.stderr
    print(f"toll-harness freshness: {title}", file=out)
    stale = 0
    for status, line in results:
        if status == FAIL:
            stale += 1
        print(f"  {'WARN' if status in (FAIL, WARN) else status:<4}  {line}", file=out)
    if not stale:
        print("Fresh.", file=out)
        return 0
    bar = "!" * 78
    print(bar, file=out)
    print("!! WARNING: this build is stale or reuses a taken version (see above). It still",
          file=out)
    print("!! builds, but it is not a release: the publish workflow refuses it, and",
          file=out)
    print("!! scripts/install_on_agent.sh will not put it on an agent.", file=out)
    print(bar, file=out)
    return 0


def build_hook(root: Path, version: str, target: str) -> None:
    """Called by hatch_build.py on a build from a git checkout. Warns; never stops the build."""
    warn_only(f"build {target} {version} from {root}", run_build(Path(root), version))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m toll_harness.freshness",
        description="Refuse a stale or already-taken toll-harness version.",
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode, text in (
        ("release", "checkout contains origin/main, and its version is newer than PyPI's newest"),
        ("build", "what the build hook runs: warnings only, always exits 0"),
    ):
        one = sub.add_parser(mode, help=text)
        one.add_argument("--repo", type=Path, default=Path.cwd())
    publish = sub.add_parser("publish", help="a tag about to be published to PyPI")
    publish.add_argument("--tag", required=True, help="the tag, e.g. v0.74.0")
    publish.add_argument("--repo", type=Path, default=Path.cwd())
    wheel = sub.add_parser("wheel", help="a wheel file or a version about to be installed")
    wheel.add_argument("target", help="path to a .whl, or a version such as 0.73.0")
    wheel.add_argument("--checkout", type=Path, default=None,
                       help="the checkout that built an unpublished wheel")
    args = parser.parse_args(argv)

    if args.mode == "release":
        return report(f"release from {args.repo}", run_release(args.repo))
    if args.mode == "build":
        return warn_only(f"build from {args.repo}", run_build(args.repo), stream=sys.stdout)
    if args.mode == "publish":
        return report(f"publish {args.tag} from {args.repo}", run_publish(args.repo, args.tag))
    return report(f"install {args.target}", run_wheel(args.target, args.checkout))


if __name__ == "__main__":
    sys.exit(main())
