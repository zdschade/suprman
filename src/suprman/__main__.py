"""Allow ``python -m suprman``."""

from __future__ import annotations

import sys

# Hand off to a resident instance before importing gi: on the fast path this
# process never touches GTK, which is the whole point.
from . import ipc

if ipc.try_handoff(sys.argv):
    raise SystemExit(0)

from .app import main

if __name__ == "__main__":
    sys.exit(main())
