"""Build hook: refuse to build a stale or already-taken toll-harness version from a git checkout.

What forced it: on 2026-10-26 a build started from a stale local copy at 0.68.0 while 0.69.0
through 0.72.0 were already on PyPI, and named itself "0.69.0". Nothing caught it.

It runs only when the project root holds a .git (a git checkout). A build from an sdist or any
other archive skips it, so offline user builds keep working, and an editable install
(``pip install -e .``) skips it. The checks live in src/toll_harness/freshness.py, loaded by path
so the build environment needs nothing but hatchling. A network failure here is a warning; the
publish workflow and the lab install helper check again and fail closed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FreshnessBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        if version == "editable" or not (root / ".git").exists():
            return
        path = root / "src" / "toll_harness" / "freshness.py"
        spec = importlib.util.spec_from_file_location("_toll_harness_freshness", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module.build_hook(root, self.metadata.version, self.target_name)
