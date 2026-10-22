"""The Unix socket listener behind ``suprman --serve``.

A resident instance keeps GTK, the typelibs and the theme loaded, so the hotkey
path costs one socket write (``ipc.send``) instead of a cold GTK start. The
listener is a :class:`Gio.SocketService`, which sits in the same main loop as
the window -- no threads, no polling.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib

from . import ipc


class ControlServer:
    """Accepts one-word commands on a Unix socket and forwards them."""

    def __init__(self, on_command: Callable[[str], None], path: Path | None = None) -> None:
        self._on_command = on_command
        self.path = Path(path) if path else ipc.socket_path()
        self._service: Gio.SocketService | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        """Start listening. False if another instance owns the socket."""
        if ipc.send("ping"):
            return False

        if self.path.exists():
            # Stale socket left behind by a crash; nothing is listening or
            # start() would already have returned False.
            with contextlib.suppress(OSError):
                self.path.unlink()

        service = Gio.SocketService()
        address = Gio.UnixSocketAddress.new(str(self.path))
        try:
            added = service.add_address(
                address, Gio.SocketType.STREAM, Gio.SocketProtocol.DEFAULT, None
            )
        except GLib.Error:
            return False
        if not added:
            return False

        service.connect("incoming", self._on_incoming)
        service.start()
        self._service = service
        return True

    def stop(self) -> None:
        if self._service is not None:
            self._service.stop()
            self._service = None
        with contextlib.suppress(OSError):
            self.path.unlink()

    @property
    def running(self) -> bool:
        return self._service is not None and self._service.is_active()

    # -- protocol ----------------------------------------------------------

    def _on_incoming(
        self,
        service: Gio.SocketService,
        connection: Gio.SocketConnection,
        source_object: object,
    ) -> bool:
        stream = Gio.DataInputStream.new(connection.get_input_stream())
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, connection)
        return True

    def _on_line(
        self, stream: Gio.DataInputStream, result: Gio.AsyncResult, connection: Gio.SocketConnection
    ) -> None:
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None

        with contextlib.suppress(GLib.Error):
            connection.close(None)

        command = ipc.parse_command(line or "")
        if command in (None, "ping"):
            return
        self._on_command(command)
