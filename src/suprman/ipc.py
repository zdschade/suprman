"""Control channel for a resident suprman instance.

Starting a GTK app costs several hundred milliseconds before anything is drawn
(interpreter, PyGObject, typelibs, theme), which is the wrong shape for a hotkey
popup. Running one instance in the background and handing it a command over a
Unix socket turns the hotkey path into a socket write.

This module deliberately imports nothing outside the standard library: the
launcher uses it *before* importing ``gi`` so the fast path never pays for GTK.

The protocol is one newline-terminated word per connection. No replies, no
authentication beyond the socket's directory permissions -- the worst a hostile
local process could do is show or hide a popup.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

#: "ping" is internal: it lets a second server notice an existing one without
#: showing anything.
COMMANDS = ("show", "hide", "toggle", "quit", "ping")
SOCKET_FILENAME = "suprman.sock"
_MAX_MESSAGE = 32


def runtime_dir() -> Path:
    """Directory for the socket: ``$XDG_RUNTIME_DIR`` when it exists."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and Path(runtime).is_dir():
        return Path(runtime)
    fallback = Path.home() / ".cache" / "suprman"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def socket_path() -> Path:
    return runtime_dir() / SOCKET_FILENAME


def parse_command(line: str) -> str | None:
    """Validate a message received from a client."""
    command = line.strip().lower()
    return command if command in COMMANDS else None


def send(command: str, timeout: float = 0.3) -> bool:
    """Send one command to a running instance. False when none is listening."""
    path = socket_path()
    if not path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(path))
            client.sendall(command.encode("utf-8") + b"\n")
    except (OSError, ValueError):
        return False
    return True


def try_handoff(argv: list[str]) -> bool:
    """Route a plain launch to a resident instance; return True if it took it.

    Any flag means the caller wants something specific (a config file, a
    server, a quit), so those always take the slow path.
    """
    if any(argument.startswith("-") for argument in argv[1:]):
        return False
    return send("toggle")


def announce(message: str) -> None:
    """Small helper so both entry points word warnings the same way."""
    print(f"suprman: {message}", file=sys.stderr)
