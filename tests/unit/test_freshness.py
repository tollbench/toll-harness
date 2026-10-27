"""The freshness guard: a stale or already-taken version is refused before build, publish, install.

What forced it: on 2026-10-26 a harness build started from a stale local copy at 0.68.0 while
0.69.0-0.72.0 were published, and named itself "0.69.0" (taken). Nothing caught it.

PyPI is mocked (``freshness.fetch_pypi``); git is real, against a bare repository in tmp_path
standing in for origin, so "behind" means what git says it means.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

from toll_harness import freshness, updates

PUBLISHED = ("0.66.1", "0.68.0", "0.69.0", "0.70.0", "0.71.0", "0.72.0", "0.73.0")


def releases(**files):
    """PyPI's releases map; files= {version: [sha256, ...]} adds digests."""
    found = {version: [] for version in PUBLISHED}
    for version, sums in files.items():
        found[version.replace("_", ".")] = [{"digests": {"sha256": s}} for s in sums]
    return found


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key, value in {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(freshness.OVERRIDE_ENV, raising=False)


@pytest.fixture
def pypi(monkeypatch):
    """PyPI answers with PUBLISHED; set .releases to change it, .down = True to unplug it."""

    class FakePyPI:
        releases = releases()
        down = False

        def __call__(self, url=freshness.PYPI_URL):
            if self.down:
                raise freshness.NetworkError(f"could not read {url}: <urlopen error timed out>")
            return self.releases

    fake = FakePyPI()
    monkeypatch.setattr(freshness, "fetch_pypi", fake)
    return fake


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_version(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(
        f'[build-system]\nrequires = ["hatchling"]\n\n[project]\nname = "toll-harness"\n'
        f'version = "{version}"\n',
        encoding="utf-8",
    )
    git(repo, "add", "pyproject.toml")
    git(repo, "commit", "-qm", f"version {version}")


class Upstream:
    """origin (a bare repo) whose main is at 0.72.0, a seed that pushes to it, and a clone."""

    def __init__(self, root: Path):
        self.seed = root / "seed"
        self.seed.mkdir()
        git(self.seed, "init", "-q", "-b", "main")
        commit_version(self.seed, "0.68.0")
        commit_version(self.seed, "0.72.0")
        self.origin = root / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.seed), str(self.origin)],
                       check=True)
        git(self.seed, "remote", "add", "origin", str(self.origin))
        self.clone = root / "work"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone)], check=True)

    def moves_on(self, version: str) -> None:
        """Upstream publishes another commit the clone has not fetched."""
        commit_version(self.seed, version)
        git(self.seed, "push", "-q", "origin", "main")


@pytest.fixture
def upstream(tmp_path):
    return Upstream(tmp_path)


def statuses(results):
    return [status for status, _ in results]


def text(results):
    return "\n".join(line for _, line in results)


# --- (a) the checkout --------------------------------------------------------------------


def test_checkout_equal_to_origin_main_passes(upstream):
    results = freshness.check_checkout(upstream.clone)
    assert statuses(results) == ["ok"]
    assert "contains origin/main" in text(results)


def test_checkout_ahead_of_origin_main_passes(upstream):
    commit_version(upstream.clone, "0.74.0")
    assert statuses(freshness.check_checkout(upstream.clone)) == ["ok"]


def test_checkout_behind_fails_after_a_fetch_and_says_where_to_start(upstream):
    # The 2026-10-26 shape: the local copy's own origin/main still looks current until a fetch.
    upstream.moves_on("0.73.0")
    git(upstream.clone, "merge-base", "--is-ancestor", "origin/main", "HEAD")  # stale ref agrees
    results = freshness.check_checkout(upstream.clone)
    assert statuses(results) == ["FAIL"]
    line = text(results)
    assert "missing 1 commit(s) that are on origin/main" in line
    assert "origin/main is at 0.73.0; this checkout says 0.72.0" in line
    assert "git switch -c <branch> origin/main" in line


def test_fetch_failure_fails_closed_for_release_and_warns_for_build(upstream):
    git(upstream.clone, "remote", "set-url", "origin", str(upstream.clone.parent / "gone.git"))
    closed = freshness.check_checkout(upstream.clone, closed=True)
    assert statuses(closed) == ["FAIL"]
    assert "git fetch origin failed" in text(closed) and "Failing closed" in text(closed)
    opened = freshness.check_checkout(upstream.clone, closed=False)
    assert statuses(opened) == ["warn", "ok"]
    assert "as last fetched" in text(opened)


def test_shallow_clone_fails_plainly(upstream, tmp_path):
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{upstream.origin}",
                    str(shallow)], check=True)
    results = freshness.check_checkout(shallow)
    assert statuses(results) == ["FAIL"]
    assert "shallow clone" in text(results) and "git fetch --unshallow" in text(results)


def test_not_a_git_checkout_fails(tmp_path):
    assert "is not a git checkout" in text(freshness.check_checkout(tmp_path))


def test_missing_git_fails_closed_and_only_warns_on_a_build(monkeypatch, tmp_path):
    def no_git(repo, *args):
        raise FileNotFoundError("git")

    monkeypatch.setattr(freshness, "_git", no_git)
    assert statuses(freshness.check_checkout(tmp_path, closed=True)) == ["FAIL"]
    assert statuses(freshness.check_checkout(tmp_path, closed=False)) == ["warn"]


def test_build_passes_a_release_tag_on_main_after_main_moves_on(upstream, pypi):
    git(upstream.clone, "tag", "v0.72.0")
    upstream.moves_on("0.72.1")
    pypi.releases = {"0.72.0": []}
    assert "FAIL" in statuses(freshness.run_release(upstream.clone))
    results = freshness.run_build(upstream.clone)
    assert statuses(results) == ["ok", "ok"]
    assert "is the release tag v0.72.0" in text(results)


# --- (b) the version ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "allow_rebuild", "status", "words"),
    [
        ("0.74.0", False, "ok", "newer than PyPI's newest, 0.73.0"),
        ("0.73.0", False, "FAIL", "0.73.0 is already on PyPI"),
        ("0.73.0", True, "ok", "rebuild, not a release"),
        ("0.69.0", False, "FAIL", "0.69.0 is already on PyPI (the newest there is 0.73.0)"),
        ("0.69.0", True, "FAIL", "0.69.0 is already on PyPI"),
        ("0.65.0", False, "FAIL", "older than PyPI's newest, 0.73.0"),
        (None, False, "FAIL", "cannot read the version"),
    ],
)
def test_version_must_be_newer_than_pypi(version, allow_rebuild, status, words):
    results = freshness.check_version(version, releases(), allow_rebuild=allow_rebuild)
    assert statuses(results) == [status]
    assert words in text(results)


def test_version_ordering_matches_the_update_check():
    versions = ["0.7.0", "0.66.1", "0.66.1.post2", "0.73", "0.73.0", "0.74.0rc1", "0.74.0.dev3",
                "v0.74.0", "1.0.0", "nonsense", ""]
    for value in versions:
        assert freshness.version_key(value) == updates.version_key(value), value
    assert freshness.newest(releases()) == "0.73.0"


def test_release_fails_closed_when_pypi_is_down(upstream, pypi, capsys):
    pypi.down = True
    results = freshness.run_release(upstream.clone)
    assert statuses(results) == ["ok", "FAIL"]
    assert "Failing closed" in text(results) and "timed out" in text(results)
    assert freshness.report("release", results) == 1


def test_build_only_warns_when_pypi_is_down(upstream, pypi):
    pypi.down = True
    results = freshness.run_build(upstream.clone)
    assert statuses(results) == ["ok", "warn"]
    assert "check it again and fail closed" in text(results)
    assert freshness.report("build", results) == 0


def test_fetch_pypi_turns_any_failure_into_a_plain_network_error(monkeypatch):
    def refuse(*args, **kwargs):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(freshness.urllib.request, "urlopen", refuse)
    with pytest.raises(freshness.NetworkError, match="could not read https://pypi.org"):
        freshness.fetch_pypi()


# --- (c) the wheel -----------------------------------------------------------------------


def make_wheel(folder: Path, version: str, name: str = "toll-harness", salt: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("toll_harness/__init__.py", f"# {salt}\n")
        archive.writestr(
            f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\nbody: not a header\n",
        )
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_published_version_string_installs_and_unpublished_one_does_not():
    assert statuses(freshness.check_wheel("0.73.0", releases())) == ["ok"]
    refused = freshness.check_wheel("0.74.0", releases())
    assert statuses(refused) == ["FAIL"] and "pass its wheel file" in text(refused)
    nonsense = freshness.check_wheel("latest!", releases())
    assert "neither a wheel file nor a version" in text(nonsense)


def test_wheel_identical_to_pypi_passes(tmp_path):
    wheel = make_wheel(tmp_path, "0.73.0")
    results = freshness.check_wheel(str(wheel), releases(**{"0_73_0": [sha(wheel)]}))
    assert statuses(results) == ["ok"] and "byte-identical" in text(results)


def test_wheel_under_a_taken_name_is_refused(tmp_path):
    # The incident: stale code built as "0.69.0", a version already published from other code.
    published = make_wheel(tmp_path / "pypi", "0.69.0", salt="the real 0.69.0")
    stale = make_wheel(tmp_path / "local", "0.69.0", salt="0.68.0 code")
    results = freshness.check_wheel(str(stale), releases(**{"0_69_0": [sha(published)]}))
    assert statuses(results) == ["FAIL"]
    assert "not PyPI's file" in text(results) and "pass 0.69.0, not the wheel" in text(results)


def test_new_wheel_from_a_fresh_checkout_passes(upstream):
    commit_version(upstream.clone, "0.74.0")
    wheel = make_wheel(upstream.clone / "dist", "0.74.0")
    results = freshness.check_wheel(str(wheel), releases())
    assert statuses(results) == ["ok", "ok"]
    assert "contains origin/main" in text(results)


def test_new_wheel_from_a_stale_checkout_is_refused(upstream):
    commit_version(upstream.clone, "0.74.0")
    wheel = make_wheel(upstream.clone / "dist", "0.74.0")
    upstream.moves_on("0.73.0")
    results = freshness.check_wheel(str(wheel), releases())
    assert statuses(results) == ["ok", "FAIL"]
    assert "missing 1 commit(s)" in text(results)


def test_new_wheel_needs_to_know_its_checkout(upstream, tmp_path):
    wheel = make_wheel(tmp_path / "loose", "0.74.0")
    assert "Pass --checkout DIR" in text(freshness.check_wheel(str(wheel), releases()))
    mismatched = freshness.check_wheel(str(wheel), releases(), checkout=upstream.clone)
    assert "says 0.72.0, but the wheel says 0.74.0" in text(mismatched)


def test_unpublished_wheel_older_than_pypi_is_refused(tmp_path):
    results = freshness.check_wheel(str(make_wheel(tmp_path, "0.70.5")), releases())
    assert statuses(results) == ["FAIL"] and "older than PyPI's newest, 0.73.0" in text(results)


def test_wheel_that_is_not_toll_harness_or_not_there_is_refused(tmp_path):
    other = make_wheel(tmp_path, "9.0.0", name="other-package")
    assert "not toll-harness" in text(freshness.check_wheel(str(other), releases()))
    missing = freshness.check_wheel(str(tmp_path / "gone-1.0.0-py3-none-any.whl"), releases())
    assert "no such file" in text(missing)


# --- publish -----------------------------------------------------------------------------


def test_publish_passes_a_newer_tag_on_main(upstream, pypi):
    commit_version(upstream.clone, "0.74.0")
    git(upstream.clone, "push", "-q", "origin", "HEAD:main")
    results = freshness.run_publish(upstream.clone, "refs/tags/v0.74.0")
    assert statuses(results) == ["ok", "ok", "ok"]
    assert freshness.report("publish", results) == 0


def test_publish_refuses_a_mismatched_tag_a_taken_version_and_a_commit_off_main(upstream, pypi):
    commit_version(upstream.clone, "0.74.0")  # committed here, never pushed to main
    results = freshness.run_publish(upstream.clone, "v0.75.0")
    assert statuses(results) == ["FAIL", "ok", "FAIL"]
    assert "does not match pyproject.toml's version 0.74.0" in text(results)
    assert "is not on origin/main" in text(results)
    taken = freshness.run_publish(upstream.clone.parent / "seed", "v0.72.0")
    assert "0.72.0 is already on PyPI" in text(taken)


# --- the report, the override, the CLI and the build hook --------------------------------


def test_override_turns_a_failure_into_a_loud_pass(monkeypatch, capsys):
    failing = [("FAIL", "version: 0.69.0 is already on PyPI")]
    monkeypatch.setenv(freshness.OVERRIDE_ENV, "   ")
    assert freshness.report("release", failing) == 1
    assert "Not fresh" in capsys.readouterr().out
    monkeypatch.setenv(freshness.OVERRIDE_ENV, "PyPI JSON API down, Steven approved")
    assert freshness.report("release", failing) == 0
    out = capsys.readouterr().out
    assert "FRESHNESS OVERRIDE" in out and "PyPI JSON API down, Steven approved" in out


def test_cli_exit_codes(pypi, capsys):
    assert freshness.main(["wheel", "0.73.0"]) == 0
    assert freshness.main(["wheel", "0.74.0"]) == 1
    assert "0.74.0 is not on PyPI" in capsys.readouterr().out


def test_build_hook_stops_the_build_only_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(freshness, "run_build", lambda root, version: [("ok", "fine")])
    freshness.build_hook(tmp_path, "0.74.0", "wheel")
    monkeypatch.setattr(freshness, "run_build", lambda root, version: [("FAIL", "stale")])
    with pytest.raises(SystemExit):
        freshness.build_hook(tmp_path, "0.74.0", "wheel")


def test_the_module_loads_by_path_with_only_the_standard_library():
    # hatch_build.py loads it this way inside a build environment that has only hatchling.
    path = Path(freshness.__file__)
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('f', {str(path)!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['f'] = m\n"
        "spec.loader.exec_module(m)\n"
        "assert 'toll_harness' not in sys.modules\n"
        "print(m.version_key('0.73.0') < m.version_key('0.74.0'))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "True"


def test_the_build_hook_is_wired_into_pyproject():
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.hatch.build.hooks.custom]\npath = "hatch_build.py"' in pyproject
    hook = (root / "hatch_build.py").read_text(encoding="utf-8")
    assert 'version == "editable"' in hook and '(root / ".git").exists()' in hook
