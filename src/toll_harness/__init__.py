"""Toll Harness public package."""

import importlib.metadata as _metadata

try:
    __version__ = _metadata.version("toll-harness")
except _metadata.PackageNotFoundError:  # source tree that was never installed
    __version__ = "0.0.0.dev0"
