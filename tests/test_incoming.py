"""A file the person handed over comes in by its path (staging, Nov 15: Sam was woken with
Steven's upload as a bare id and wrote "photo arrived only as an upload ID, not viewed").

The watch fetches each file the item lists with the agent's own key, into a folder made for
the item; the bridge moves it into the scratch folder the agent is started in and the prompt
names its path. The key goes only to the bench and never reaches the model; bytes land only
in the folder, under a safe name; the caps and the fingerprint hold; a file that cannot be
fetched never fails the item. The agent programs here are stand-ins: no model is called."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from tests.fakebench import TOKEN, FakeBench, item
from toll_harness import incoming, wake
from toll_harness.agent import TOKEN_SECRET, load
from toll_harness.bench import Bench, BenchError, bench_path
from toll_harness.logs import ActivityLog
from toll_harness.watch import Watch

SRC = str(Path(__file__).resolve().parents[1] / "src")
DEAL = "f2871bca-b449-4f5a-ba5f-0133e330fe28"
PHOTO_ID = "8c741b75-4abe-4b8b-b227-9dc1d6672b7f"
PHOTO_URL = f"/api/bench/deals/{DEAL}/materials/{PHOTO_ID}"

# A stand-in agent program: it writes down its argv, its whole environment, the prompt, the
# folder it was started in and what it finds in the from_* folders there, then replies.
LOOKER = textwrap.dedent("""\
    #!{python}
    import hashlib, json, os, sys
    prompt = sys.stdin.read()
    files = {{}}
    for sub in ("from_person", "from_earlier_steps", "from_bench"):
        if os.path.isdir(sub):
            for name in sorted(os.listdir(sub)):
                with open(os.path.join(sub, name), "rb") as handle:
                    files[sub + "/" + name] = hashlib.sha256(handle.read()).hexdigest()
    with open(os.environ["PROGRAM_RECORD"], "a") as handle:
        handle.write(json.dumps({{"argv": sys.argv, "prompt": prompt, "cwd": os.getcwd(),
                                  "files": files, "env": dict(os.environ)}}) + "\\n")
    reply = os.environ.get("REPLY", '{{"reply": "looked"}}')
    if "--output-last-message" in sys.argv:
        open(sys.argv[sys.argv.index("--output-last-message") + 1], "w").write(reply)
        print('{{"type": "event stream, not the answer"}}')
    else:
        print(reply)
""")


@pytest.fixture
def other():
    """Another host. Nothing the harness does may ever reach it."""
    fake = FakeBench().start()
    yield fake
    fake.stop()


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Stand-in agent programs first on PATH, and one temp folder for the watch and the bridge
    so a test can see that nothing is left in it after the item."""
    folder = tmp_path / "bin"
    folder.mkdir()
    for name in ("claude", "codex"):
        path = folder / name
        path.write_text(LOOKER.format(python=sys.executable))
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PYTHONPATH", SRC)
    monkeypatch.setenv("PROGRAM_RECORD", str(tmp_path / "program.jsonl"))
    monkeypatch.setenv("TMPDIR", str(temp))  # the bridge's scratch folder
    monkeypatch.setattr(tempfile, "tempdir", str(temp))  # the watch's folder for the files
    for name in ("REPLY", "TOLL_AGENT_CONFIG", "TOLL_HARNESS_BASE_URL", incoming.FILES_ENV):
        monkeypatch.delenv(name, raising=False)
    return temp


def runs(tmp_path) -> list[dict]:
    record = tmp_path / "program.jsonl"
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() \
        else []


def woken(tmp_path, bench, wake_config):
    folder = tmp_path / "agent"
    folder.mkdir(exist_ok=True)
    config = {"version": 2,
              "agent": {"name": "TestEcho", "company": "Test Owner Co", "model": "Echo",
                        "wake": wake_config, "time_limit_minutes": 15},
              "bench": {"url": bench.url}, "update": {"auto": False}}
    (folder / "agent.yaml").write_text(yaml.safe_dump(config))
    agent = load(folder)
    agent.secrets.set(TOKEN_SECRET, TOKEN)
    return agent


def watch_once(agent):
    Watch(agent, wait=0, updater=lambda *a, **k: {"action": "none"}, sleep=lambda s: None,
          spread=0).run(1)


def person_file(name="View_recent_photos.png", url=PHOTO_URL, body=b"", **extra):
    entry = {"file_id": str(url).rsplit("/", 1)[-1], "from": "person", "step": 2,
             "ask": "full_body_photo", "asked": "Upload her full-body photo", "filename": name,
             "content_type": "image/png", "size_bytes": len(body), "download_url": url}
    entry.update(extra)
    return entry


def step_item(item_id, files, **extra):
    return item(item_id, kind="step", context={"inputs": {"files": files,
                                                          "files_count": len(files)}}, **extra)


def brought(bench, entries, tmp_path, name="it"):
    folder = tmp_path / f"brought-{name}"
    folder.mkdir()
    log = ActivityLog(tmp_path / "logs", echo=False)
    manifest = incoming.bring_in(Bench(bench.url, token=TOKEN), step_item(name, entries),
                                 folder, log, label=name)
    return manifest, folder


def fetches(bench) -> list[str]:
    return [path for path, _ in bench.auth if "/deals/" in path]


# -- the whole way: watch -> bridge -> the agent's program ---------------------------------
@pytest.mark.parametrize("agent_name", ["claude", "codex"])
def test_a_file_the_person_handed_over_comes_in_by_its_path(tmp_path, bench, scratch,
                                                            agent_name):
    photo = os.urandom(3 * 1024 * 1024)
    bench.files[PHOTO_URL] = {"body": photo}
    agent = woken(tmp_path, bench, {"agent": agent_name})
    bench.add(step_item("it_photo", [person_file(body=photo)]))
    watch_once(agent)
    (run,) = runs(tmp_path)
    assert run["files"] == {"from_person/View_recent_photos.png":
                            hashlib.sha256(photo).hexdigest()}
    assert "toll-wake-" in run["cwd"]  # the scratch folder, never the agent's folder
    assert ('- from_person/View_recent_photos.png (image/png, 3.0 MB), the person\'s answer '
            'to "Upload her full-body photo" on step 2') in run["prompt"]
    assert "Open them with your own file tools" in run["prompt"]
    assert (PHOTO_URL, f"Bearer {TOKEN}") in bench.auth  # the agent's own key, to the bench
    assert bench.answers == [("it_photo", {"reply": "looked"})]
    # the key never reaches the model: not in its argv, its environment or its prompt
    assert TOKEN not in json.dumps(run)
    assert incoming.FILES_ENV not in run["env"]
    log = (agent.data_dir / "harness.log").read_text()
    assert "it_photo: brought in from_person/View_recent_photos.png (3.0 MB)" in log
    assert TOKEN not in log
    assert list(scratch.iterdir()) == []  # nothing is kept after the item


def test_the_two_example_entries_from_the_platform(tmp_path, bench, scratch):
    photo = os.urandom(5672550)
    board = b"%PDF-1.4 the moodboard step 1 handed in"
    receipt = "3f0d2a9e-61b4-4c1e-9a55-0d7e2b8c4f11"
    board_url = f"/api/bench/deals/{DEAL}/files/{receipt}"
    entries = [
        {"file_id": PHOTO_ID, "from": "person", "step": 2, "ask": "full_body_photo",
         "asked": "Upload her full-body photo", "filename": "View_recent_photos.png",
         "content_type": "image/png", "size_bytes": 5672550, "sha256": "4b234f74...",
         "download_url": PHOTO_URL},
        {"file_id": receipt, "from": "agent", "step": 1, "ask": None, "asked": None,
         "filename": "moodboard.pdf", "content_type": "application/pdf",
         "size_bytes": len(board), "sha256": hashlib.sha256(board).hexdigest(),
         "download_url": board_url},
    ]
    bench.files[PHOTO_URL] = {"body": photo}
    bench.files[board_url] = {"body": board}
    said = {"step": 2, "asked": "Upload her full-body photo",
            "answer": "the file View_recent_photos.png (image/png, 5672550 bytes), in "
                      "inputs.files", "file_ids": [PHOTO_ID]}
    agent = woken(tmp_path, bench, {"agent": "claude"})
    bench.add(item("it_two", kind="step", context={"inputs": {
        "files": entries, "files_count": 2, "person_said": [said]}}))
    watch_once(agent)
    (run,) = runs(tmp_path)
    assert run["files"] == {"from_person/View_recent_photos.png":
                            hashlib.sha256(photo).hexdigest(),
                            "from_earlier_steps/moodboard.pdf":
                            hashlib.sha256(board).hexdigest()}
    assert ('- from_person/View_recent_photos.png (image/png, 5.4 MB), the person\'s answer '
            'to "Upload her full-body photo" on step 2') in run["prompt"]
    assert ("- from_earlier_steps/moodboard.pdf (application/pdf, 39 bytes), handed in on "
            "step 1") in run["prompt"]
    assert "Not fetched" not in run["prompt"]
    assert bench.answers == [("it_two", {"reply": "looked"})]


def test_a_404_leaves_the_agent_woken_with_the_not_fetched_line(tmp_path, bench, scratch):
    agent = woken(tmp_path, bench, {"agent": "claude"})
    bench.add(step_item("it_gone", [person_file(body=b"x" * 10)]))  # the bench answers 404
    watch_once(agent)
    (run,) = runs(tmp_path)
    assert run["files"] == {}
    assert ("Not fetched: View_recent_photos.png (could not be fetched from the bench)."
            in run["prompt"])
    assert bench.answers == [("it_gone", {"reply": "looked"})]  # not a failure, not counted
    log = (agent.data_dir / "harness.log").read_text()
    assert ("it_gone: not brought in from_person/View_recent_photos.png: could not be fetched "
            "from the bench") in log
    assert list(scratch.iterdir()) == []


def test_no_files_changes_nothing(tmp_path, bench, scratch):
    plain = item("it_plain", kind="step")
    empty = step_item("it_empty", [])
    agent = woken(tmp_path, bench, {"agent": "claude"})
    bench.add(plain)
    bench.add(empty)
    watch_once(agent)
    first, second = runs(tmp_path)
    assert first["prompt"] == wake.prompt_for(plain)
    assert second["prompt"] == wake.prompt_for(empty)
    assert "Files you were given" not in first["prompt"] + second["prompt"]
    assert first["files"] == second["files"] == {}
    assert incoming.FILES_ENV not in first["env"]
    assert fetches(bench) == []
    assert list(scratch.iterdir()) == []
    for value in (plain, empty):
        assert (wake.prompt_for(value) == wake.prompt_for(value, None)
                == wake.prompt_for(value, {"files": [], "missing": []}))


def test_an_agent_that_runs_its_own_command_gets_the_item_as_it_was(made):
    agent = made.agent()
    given = step_item("it_own", [person_file(body=b"x")])
    made.bench.files[PHOTO_URL] = {"body": b"x"}
    made.bench.add(given)
    Watch(agent, wait=0, updater=lambda *a, **k: {"action": "none"}, sleep=lambda s: None,
          spread=0).run(1)
    assert made.runs()[0]["item"] == given
    assert fetches(made.bench) == []


def test_a_file_brought_in_can_be_handed_back_by_its_path(tmp_path, bench, scratch,
                                                         monkeypatch):
    photo = b"\x89PNG the person's photo"
    bench.files[PHOTO_URL] = {"body": photo}
    monkeypatch.setenv("REPLY", json.dumps({"status": "done", "files": [
        {"path": "from_person/View_recent_photos.png"}]}))
    agent = woken(tmp_path, bench, {"agent": "claude", "extra_args": ["--tools", "default"]})
    bench.add(step_item("it_back", [person_file(body=photo)]))
    watch_once(agent)
    ((item_id, answer),) = bench.answers
    (sent,) = answer["files"]
    assert sent["filename"] == "View_recent_photos.png"
    assert base64.b64decode(sent["content_base64"]) == photo


# -- the key goes only to the bench --------------------------------------------------------
def test_an_address_off_the_bench_is_never_fetched_and_the_key_never_leaves(tmp_path, bench,
                                                                            other):
    host = other.url.split("//", 1)[1]
    addresses = [
        other.url + PHOTO_URL, f"//{host}{PHOTO_URL}", "https://evil.example" + PHOTO_URL,
        "/api/bench/../me", f"/api/bench/deals/{DEAL}/materials/../../../me",
        PHOTO_URL.lstrip("/"), "/api/benchx/deals/1/materials/2", PHOTO_URL + "?next=x",
        "/api/bench/deals/%2e%2e/me", "/api/bench/deals\\1", f"/api/bench//{host}/x",
        "/api/bench/", " " + PHOTO_URL, PHOTO_URL + "\n", " ", 7, ["/api/bench/x"],
    ]
    entries = [person_file(name=f"f{number}.png", url=address)
               for number, address in enumerate(addresses)]
    bench.files[PHOTO_URL] = {"body": b"x"}
    manifest, folder = brought(bench, entries, tmp_path)
    assert manifest["files"] == []
    assert [row["why"] for row in manifest["missing"]] == ["not a bench address"] * len(entries)
    assert other.auth == [] and bench.auth == []  # no request at all, so no key went anywhere
    for address in addresses:
        assert bench_path(address) is None
        with pytest.raises(BenchError):
            Bench(bench.url, token=TOKEN).download(address, lambda block: None, most=10,
                                                   seconds=5)
    assert other.auth == [] and bench.auth == []
    assert bench_path(PHOTO_URL) == PHOTO_URL
    assert bench_path(f"/api/bench/deals/{DEAL}/files/r-1") is not None


def test_a_file_the_bench_keeps_no_copy_of_is_listed_and_never_asked_for(tmp_path, bench,
                                                                        scratch):
    kept = b"the one the bench kept"
    bench.files[PHOTO_URL] = {"body": kept}
    entries = [person_file(name="gone.png", download_url=None),
               person_file(name="empty.png", download_url=""),
               person_file(body=kept)]
    agent = woken(tmp_path, bench, {"agent": "claude"})
    bench.add(step_item("it_nocopy", entries))
    watch_once(agent)
    (run,) = runs(tmp_path)
    assert fetches(bench) == [PHOTO_URL]  # no request for the two with no copy
    assert run["files"] == {"from_person/View_recent_photos.png":
                            hashlib.sha256(kept).hexdigest()}
    assert ("Not available: gone.png (the bench keeps no copy); empty.png (the bench keeps no "
            "copy).") in run["prompt"]
    assert "Not fetched" not in run["prompt"]
    assert bench.answers == [("it_nocopy", {"reply": "looked"})]  # never a failure
    no_address = {"filename": "bare.png", "from": "person"}
    manifest, _ = brought(bench, [no_address], tmp_path)
    assert manifest["missing"][0]["why"] == incoming.NO_COPY


def test_the_released_materials_shape_comes_in_too(tmp_path, bench):
    body = b"a mailbox file"
    bench.files[PHOTO_URL] = {"body": body}
    released = {"file_id": PHOTO_ID, "filename": "View_recent_photos.png",
                "content_type": "image/png", "size_bytes": len(body),
                "released_at": "2026-11-15T12:00:00Z", "download_url": PHOTO_URL}  # no sha256
    folder = tmp_path / "released"
    folder.mkdir()
    manifest = incoming.bring_in(Bench(bench.url, token=TOKEN),
                                 item("it_r", context={"released_materials": [released]}),
                                 folder, ActivityLog(tmp_path / "logs", echo=False))
    assert [row["path"] for row in manifest["files"]] == ["from_person/View_recent_photos.png"]
    assert (folder / "from_person" / "View_recent_photos.png").read_bytes() == body
    assert incoming.note_for(manifest).endswith("(image/png, 14 bytes), from the person")


def test_a_redirect_is_never_followed(tmp_path, bench, other):
    bench.files[PHOTO_URL] = {"status": 302, "body": b"",
                              "headers": {"Location": other.url + PHOTO_URL}}
    other.files[PHOTO_URL] = {"body": b"elsewhere"}
    manifest, folder = brought(bench, [person_file(body=b"x" * 9)], tmp_path)
    assert manifest["files"] == []
    assert manifest["missing"][0]["why"] == incoming.NOT_FETCHED
    assert other.auth == []
    assert list((folder / "from_person").iterdir()) == []


# -- bytes land only inside the folder, under a safe name ---------------------------------
@pytest.mark.parametrize("given, saved", [
    ("View_recent_photos.png", "View_recent_photos.png"),
    ("../../etc/passwd", "passwd"),
    ("..\\..\\windows\\win.ini", "win.ini"),
    ("/etc/hostname", "hostname"),
    (".bashrc", "bashrc"),
    ("...", "file-4"),
    ("", "file-4"),
    (None, "file-4"),
    ("a\nb\x00c\x1b[31m.png", "abc[31m.png"),
    ("‮gnp.exe", "gnp.exe"),
    ('wh<at>:is*"this"?.jpg', "wh_at__is__this__.jpg"),
    ("photo.", "photo"),
    ("Résumé été.pdf", "Résumé été.pdf"),
])
def test_odd_names_are_made_safe(given, saved):
    assert incoming.safe_name(given, 4) == saved


def test_a_long_name_is_cut_and_keeps_its_extension():
    name = incoming.safe_name("é" * 300 + ".jpeg", 1)
    assert name.endswith(".jpeg") and len(name.encode("utf-8")) <= incoming.NAME_BYTES
    assert incoming.safe_name("x" * 300, 1) == "x" * incoming.NAME_BYTES


def test_a_name_that_climbs_stays_in_the_folder(tmp_path, bench):
    urls = [f"/api/bench/deals/d/materials/f{number}" for number in range(3)]
    for url in urls:
        bench.files[url] = {"body": b"bytes of " + url.encode()}
    names = ["../../../evil.png", "..\\..\\evil.txt", "/tmp/evil.sh"]
    manifest, folder = brought(bench, [person_file(name=name, url=url, body=b"")
                                       for name, url in zip(names, urls, strict=True)],
                                 tmp_path)
    assert [row["path"] for row in manifest["files"]] == [
        "from_person/evil.png", "from_person/evil.txt", "from_person/evil.sh"]
    inside = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("evil*"))
    assert inside == ["brought-it/from_person/evil.png", "brought-it/from_person/evil.sh",
                      "brought-it/from_person/evil.txt"]


def test_two_files_with_the_same_name_never_overwrite(tmp_path, bench):
    bodies = [b"first", b"second", b"third"]
    entries = []
    names = ["photo.png", "photo.png", "PHOTO.png"]
    for number, (name, body) in enumerate(zip(names, bodies, strict=True)):
        url = f"/api/bench/deals/d/materials/p{number}"
        bench.files[url] = {"body": body}
        entries.append(person_file(name=name, url=url, body=body))
    manifest, folder = brought(bench, entries, tmp_path)
    paths = [row["path"] for row in manifest["files"]]
    assert paths == ["from_person/photo.png", "from_person/photo-2.png",
                     "from_person/PHOTO-3.png"]
    assert [(folder / path).read_bytes() for path in paths] == bodies


def test_who_handed_a_file_in_decides_its_folder(tmp_path, bench):
    entries = []
    for number, kind in enumerate(["person", "agent", "operator", None]):
        url = f"/api/bench/deals/d/materials/k{number}"
        bench.files[url] = {"body": b"k"}
        entry = person_file(name="k.txt", url=url, body=b"k", **{"from": kind})
        entries.append(entry)
    manifest, folder = brought(bench, entries, tmp_path)
    assert [row["path"] for row in manifest["files"]] == [
        "from_person/k.txt", "from_earlier_steps/k.txt", "from_bench/k.txt",
        "from_person/k-2.txt"]  # no "from" is the person's: the released mailbox files
    note = incoming.note_for(manifest)
    assert "from_earlier_steps/k.txt (image/png, 1 bytes), handed in on step 2" in note
    assert "from_bench/k.txt (image/png, 1 bytes), from the bench on step 2" in note


def test_where_the_item_lists_its_files():
    files = [{"filename": "a"}, "not an entry", {"filename": "b"}]
    released = [{"filename": "r"}]
    assert incoming.files_in({"context": {"inputs": {"files": files}}}) == [
        {"filename": "a"}, {"filename": "b"}]
    assert incoming.files_in({"context": {"released_materials": released}}) == released
    assert incoming.files_in({"context": {"inputs": {"files": []},
                                          "released_materials": released}}) == []
    assert incoming.files_in({"context": {"inputs": {"person_said": []}}}) == []
    assert incoming.files_in({}) == [] and incoming.files_in({"context": "x"}) == []


def test_other_names_for_the_address_and_the_size_are_read(tmp_path, bench):
    bench.files["/api/bench/deals/d/materials/u"] = {"body": b"u" * 20}
    bench.files["/api/bench/deals/d/materials/p"] = {"body": b"p" * 20}
    entries = [{"filename": "u.txt", "url": "/api/bench/deals/d/materials/u", "size": 20},
               {"filename": "p.txt", "path": "/api/bench/deals/d/materials/p", "size": 20}]
    manifest, _ = brought(bench, entries, tmp_path)
    assert [row["path"] for row in manifest["files"]] == ["from_person/u.txt",
                                                          "from_person/p.txt"]


# -- sizes, fingerprints and time ----------------------------------------------------------
def test_the_caps_are_the_bench_mailbox_caps():
    assert incoming.MAX_FILE_BYTES == 50 * 1024 * 1024
    assert incoming.MAX_ITEM_BYTES == 100 * 1024 * 1024
    assert incoming.MAX_FILES == 30


def test_the_per_file_cap_holds_whatever_the_entry_and_the_line_claimed(tmp_path, bench,
                                                                        monkeypatch):
    monkeypatch.setattr(incoming, "MAX_FILE_BYTES", 1000)
    urls = [f"/api/bench/deals/d/materials/c{number}" for number in range(3)]
    bench.files[urls[0]] = {"body": b"a" * 5000, "length": False}  # no Content-Length
    bench.files[urls[1]] = {"body": b"b" * 50}
    bench.files[urls[2]] = {"body": b"c" * 50, "claims": 2000}  # the line says too much
    entries = [person_file(name="lies.png", url=urls[0], size_bytes=10),
               person_file(name="claims.png", url=urls[1], size_bytes=60 * 1024 * 1024),
               person_file(name="header.png", url=urls[2], size_bytes=50)]
    manifest, folder = brought(bench, entries, tmp_path)
    assert manifest["files"] == []
    assert [row["why"] for row in manifest["missing"]] == [
        "too large to bring in (over 1000 bytes)", "too large to bring in (60.0 MB)",
        "too large to bring in (2 KB)"]
    assert urls[1] not in fetches(bench)  # a file that says it is too large is not fetched
    assert list((folder / "from_person").iterdir()) == []  # the partial file is gone


def test_the_item_cap_counts_across_every_folder(tmp_path, bench, monkeypatch):
    monkeypatch.setattr(incoming, "MAX_FILE_BYTES", 1000)
    monkeypatch.setattr(incoming, "MAX_ITEM_BYTES", 1500)
    urls = [f"/api/bench/deals/d/materials/t{number}" for number in range(3)]
    for url in urls:
        bench.files[url] = {"body": b"t" * 800, "length": False}
    entries = [person_file(name="one.png", url=urls[0], size_bytes=800),
               person_file(name="two.png", url=urls[1], size_bytes=800, **{"from": "agent"}),
               person_file(name="three.png", url=urls[2], size_bytes=10, **{"from": "agent"})]
    manifest, folder = brought(bench, entries, tmp_path)
    assert [row["path"] for row in manifest["files"]] == ["from_person/one.png"]
    assert [row["why"][:9] for row in manifest["missing"]] == ["past the "] * 2
    assert urls[1] not in fetches(bench)  # said 800 with 700 left: not fetched
    assert list((folder / "from_earlier_steps").iterdir()) == []  # three lied: stopped, gone


def test_more_than_the_most_files_are_listed_not_fetched(tmp_path, bench, monkeypatch):
    monkeypatch.setattr(incoming, "MAX_FILES", 2)
    urls = [f"/api/bench/deals/d/materials/m{number}" for number in range(3)]
    for url in urls:
        bench.files[url] = {"body": b"m"}
    manifest, _ = brought(bench, [person_file(name=f"m{n}.png", url=url, body=b"m")
                                  for n, url in enumerate(urls)], tmp_path)
    assert len(manifest["files"]) == 2
    assert manifest["missing"][0]["why"] == "more than 2 files on one item"
    assert urls[2] not in fetches(bench)


def test_a_fingerprint_that_does_not_match_deletes_the_file(tmp_path, bench):
    body = b"the bytes that came"
    bench.files[PHOTO_URL] = {"body": body}
    wrong = person_file(body=body, sha256="0" * 64)
    right = person_file(name="again.png", body=body,
                        sha256=hashlib.sha256(body).hexdigest().upper())
    manifest, folder = brought(bench, [wrong, right], tmp_path)
    assert manifest["missing"][0]["why"] == "did not match its fingerprint"
    assert [row["path"] for row in manifest["files"]] == ["from_person/again.png"]
    assert sorted(path.name for path in (folder / "from_person").iterdir()) == ["again.png"]


def test_a_slow_file_is_given_up_in_its_own_time(tmp_path, bench, monkeypatch):
    monkeypatch.setattr(incoming, "FILE_SECONDS", 0.5)
    bench.files[PHOTO_URL] = {"body": b"s" * 16 * 100, "trickle": 0.05}  # about 5 s
    started = time.monotonic()
    manifest, folder = brought(bench, [person_file(body=b"s" * 1600)], tmp_path)
    assert time.monotonic() - started < 3
    assert manifest["missing"][0]["why"] == "took too long to fetch"
    assert list((folder / "from_person").iterdir()) == []


def test_all_the_files_of_an_item_share_one_bound(tmp_path, bench, monkeypatch):
    monkeypatch.setattr(incoming, "ALL_SECONDS", 0.5)
    slow, quick = "/api/bench/deals/d/materials/slow", "/api/bench/deals/d/materials/quick"
    bench.files[slow] = {"body": b"s" * 16 * 100, "trickle": 0.05}
    bench.files[quick] = {"body": b"q"}
    started = time.monotonic()
    manifest, _ = brought(bench, [person_file(name="slow.png", url=slow),
                                  person_file(name="quick.png", url=quick)], tmp_path)
    assert time.monotonic() - started < 3
    assert [row["why"] for row in manifest["missing"]] == ["took too long to fetch",
                                                           "no time was left to fetch it"]
    assert quick not in fetches(bench)


def test_the_note_names_only_what_the_watch_brought():
    assert incoming.note_for(None) == "" and incoming.note_for({}) == ""
    note = incoming.note_for({"files": [], "missing": [
        {"name": "a.png", "why": incoming.NOT_FETCHED}, {"name": "b.pdf", "why": "took too "
                                                         "long to fetch"}]})
    assert note == ("Not fetched: a.png (could not be fetched from the bench); b.pdf (took "
                    "too long to fetch).")
