from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from toll_harness.storage.base import SecretStore

_SECRET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


def _validated_name(name: str) -> str:
    if not _SECRET_NAME.fullmatch(name):
        raise ValueError("Secret names must use letters, numbers, dots, dashes, or underscores")
    return name


class EnvironmentSecretStore(SecretStore):
    def __init__(self, prefix: str = "TOLL_SECRET_"):
        self.prefix = prefix

    def get(self, name: str) -> str | None:
        normalized = "".join(character if character.isalnum() else "_" for character in name)
        return os.environ.get(f"{self.prefix}{normalized.upper()}")

    def set(self, name: str, value: str) -> None:
        normalized = "".join(
            character if character.isalnum() else "_" for character in _validated_name(name)
        )
        os.environ[f"{self.prefix}{normalized.upper()}"] = value


class FileSecretStore(SecretStore):
    """Owner-only local secret files with atomic replacement and no list API."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    def _path(self, name: str) -> Path:
        return self.directory / _validated_name(name)

    def set(self, name: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("Secret values must be non-empty strings")
        destination = self._path(name)
        temporary = self.directory / f".{destination.name}.{secrets.token_hex(8)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def get(self, name: str) -> str | None:
        path = self._path(name)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise PermissionError("Secret paths must be regular files")
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError("Secret file permissions must not allow group or other access")
        return path.read_text(encoding="utf-8")
