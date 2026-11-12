"""`python -m toll_harness.freshness` still runs the freshness check (0.74.0).

The check runs only from a git checkout -- before a release, in the build hook, in the publish
workflow and before a lab install -- so since 0.74.0 it lives in the checkout's
scripts/freshness.py rather than in the installed package. This name loads it from there.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "freshness.py"
if not _SCRIPT.is_file():
    raise ImportError("The freshness check runs from a toll-harness checkout: scripts/freshness.py")
_spec = importlib.util.spec_from_file_location(__name__, _SCRIPT)
_module = importlib.util.module_from_spec(_spec)
sys.modules[__name__] = _module
_spec.loader.exec_module(_module)
