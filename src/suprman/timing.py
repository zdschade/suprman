"""Opt-in startup timing.

``SUPRMAN_TIMING=1 suprman`` prints a timestamp per startup milestone to stderr.
It costs one environment lookup when disabled, so it can stay in the hot path
without a guard at every call site.

The baseline is the real process start (from ``/proc/self/stat``), not the
import of this module, so the numbers include the Python interpreter and
PyGObject import time.
"""

from __future__ import annotations

import os
import sys
import time

ENABLED = os.environ.get("SUPRMAN_TIMING", "") not in ("", "0", "no", "false", "off")

_CLOCK_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _process_age() -> float:
    """Seconds elapsed since this process was exec'd, best effort.

    ``starttime`` (field 22 of ``/proc/self/stat``) counts clock ticks since
    boot, so the age is the boot clock minus that. It has to be measured on the
    boot clock (BOOTTIME) -- mixing it with ``time.monotonic()``, which is
    *also* uptime on Linux, silently reports uptime instead of process age.
    """
    try:
        with open("/proc/self/stat", encoding="utf-8") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        started_seconds = int(fields[19]) / _CLOCK_TICKS
        return max(0.0, time.clock_gettime(time.CLOCK_BOOTTIME) - started_seconds)
    except (OSError, IndexError, ValueError, AttributeError):
        return 0.0


_START = time.monotonic() - _process_age()
_LAST = _START


def mark(label: str) -> None:
    """Print one milestone: total since exec, and delta since the previous."""
    global _LAST
    if not ENABLED:
        return
    now = time.monotonic()
    total = (now - _START) * 1000.0
    delta = (now - _LAST) * 1000.0
    _LAST = now
    print(f"suprman: {label:<24} {total:7.1f} ms  (+{delta:6.1f})", file=sys.stderr)
