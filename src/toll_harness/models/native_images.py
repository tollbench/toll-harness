"""Import only raster output belonging to the Codex subprocess we just ran.

No model-provided path is opened. Thread IDs come from the CLI event stream,
and every directory/file below generated_images is opened without symlinks.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path

from toll_harness.core.types import ModelArtifact

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_IMAGES = 8


def collect_codex_images(stdout: str | None) -> list[ModelArtifact]:
    root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "generated_images"
    threads = set()
    for line in str(stdout or "").splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "thread.started":
            continue
        value = event.get("thread_id")
        try:
            if str(uuid.UUID(value)) == value:
                threads.add(value)
        except (ValueError, TypeError, AttributeError):
            continue
    images = []
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return images
    try:
        for thread_id in sorted(threads):
            try:
                directory = os.open(
                    thread_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
                )
            except OSError:
                continue
            try:
                for name in sorted(os.listdir(directory)):
                    if len(images) >= MAX_IMAGES:
                        return images
                    suffix = Path(name).suffix.lower()
                    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                        continue
                    try:
                        descriptor = os.open(
                            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                        )
                    except OSError:
                        continue
                    with os.fdopen(descriptor, "rb") as handle:
                        info = os.fstat(handle.fileno())
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or not 0 < info.st_size <= MAX_IMAGE_BYTES
                        ):
                            continue
                        content = handle.read(MAX_IMAGE_BYTES + 1)
                    if len(content) > MAX_IMAGE_BYTES:
                        continue
                    valid = (
                        suffix == ".png"
                        and content.startswith(b"\x89PNG\r\n\x1a\n")
                        or suffix in {".jpg", ".jpeg"}
                        and content.startswith(b"\xff\xd8\xff")
                        or suffix == ".webp"
                        and content[:4] == b"RIFF"
                        and content[8:12] == b"WEBP"
                    )
                    if valid:
                        images.append(
                            ModelArtifact(
                                source_path=str(root / thread_id / name),
                                filename=f"{thread_id}-{name}",
                                content=content,
                            )
                        )
            finally:
                os.close(directory)
    finally:
        os.close(root_fd)
    return images
