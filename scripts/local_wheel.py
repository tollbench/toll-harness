"""Build a toll-harness wheel from this checkout with nothing but the standard library.

For the update tests and a staging install from a local file: no build backend is fetched and
no package index is reached. The release build is still `python -m build` (hatchling).

    python scripts/local_wheel.py OUT_DIR [--version 1.0.1]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


INIT = ROOT / "src" / "toll_harness" / "__init__.py"
_LINE = re.compile(r'^__version__ = "([^"]+)"', re.M)


def project_version() -> str:
    match = _LINE.search(INIT.read_text())
    if not match:
        raise SystemExit("src/toll_harness/__init__.py names no __version__")
    return match.group(1)


def build(out_dir: Path, version: str | None = None) -> Path:
    version = version or project_version()
    out_dir.mkdir(parents=True, exist_ok=True)
    info = f"toll_harness-{version}.dist-info"
    files: dict[str, bytes] = {}
    for path in sorted((ROOT / "src" / "toll_harness").rglob("*.py")):
        data = path.read_bytes()
        if path == INIT:  # a test build of another version says that version
            data = _LINE.sub(f'__version__ = "{version}"', data.decode()).encode()
        files[path.relative_to(ROOT / "src").as_posix()] = data
    files[f"{info}/METADATA"] = (
        "Metadata-Version: 2.1\nName: toll-harness\n"
        f"Version: {version}\n"
        "Summary: The Toll Bench harness: the line between an agent and Toll Bench\n"
        "Requires-Python: >=3.10\nRequires-Dist: PyYAML<7,>=6.0\n").encode()
    files[f"{info}/WHEEL"] = (b"Wheel-Version: 1.0\nGenerator: toll-harness local_wheel\n"
                              b"Root-Is-Purelib: true\nTag: py3-none-any\n")
    files[f"{info}/entry_points.txt"] = (b"[console_scripts]\n"
                                         b"toll-harness = toll_harness.cli:main\n")
    record = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append(f"{info}/RECORD,,")
    files[f"{info}/RECORD"] = ("\n".join(record) + "\n").encode()
    wheel = out_dir / f"toll_harness-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return wheel


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    parser.add_argument("--version")
    arguments = parser.parse_args()
    print(build(Path(arguments.out_dir), arguments.version))
