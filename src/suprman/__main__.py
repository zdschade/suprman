"""Allow ``python -m suprman``."""

from __future__ import annotations

import sys

# Hand off to a resident instance before importing gi: on the fast path this
# process never touches GTK, which is the whole point.
from . import ipc, timing

if ipc.try_handoff(sys.argv):
    timing.mark("handoff: delivered")
    raise SystemExit(0)
timing.mark("handoff: no resident instance")

from .app import main

if __name__ == "__main__":
    sys.exit(main())
