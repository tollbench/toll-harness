from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from toll_harness.storage.base import ArtifactStore


class FilesystemArtifactStore(ArtifactStore):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str, path: str = "") -> Path:
        if not run_id.isalnum():
            raise ValueError("Invalid run id")
        run_root = (self.root / run_id).resolve()
        candidate = (run_root / path).resolve()
        if candidate != run_root and run_root not in candidate.parents:
            raise ValueError("Artifact path escapes the run directory")
        return candidate

    def list(self, run_id: str, prefix: str = "") -> list[dict[str, Any]]:
        run_root = self._path(run_id)
        base = self._path(run_id, prefix)
        if not base.exists():
            return []
        paths = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        return [
            {"path": str(path.relative_to(run_root)), "size": path.stat().st_size} for path in paths
        ]

    def read(self, run_id: str, path: str) -> bytes:
        return self._path(run_id, path).read_bytes()

    def write(self, run_id: str, path: str, content: bytes) -> dict[str, Any]:
        target = self._path(run_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {
            "path": path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
