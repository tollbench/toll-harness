"""Toll Harness: the line between an agent and Toll Bench.

It has no brain. It keeps one line open to the bench, hands each item to the agent's own
command, and sends the agent's answer back.
"""

# The one place the version is written: pyproject reads it from here (hatch), so the code that
# runs always knows its own version, whatever else is installed beside it.
__version__ = "1.0.0"
