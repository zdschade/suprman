"""Application entry point: argument handling, stylesheet, GTK application.

Two modes:

* default -- a short-lived process that shows the popup and exits when it
  closes. Roughly a second of GTK startup on a cold machine.
* ``--serve`` -- a resident process with the window hidden. A plain ``suprman``
  invocation then hands it a ``toggle`` over a Unix socket and exits in a few
  tens of milliseconds, without ever importing GTK (see :mod:`suprman.ipc`).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from . import __version__, ipc
from . import config as config_module
from .config import Config
from .timing import mark
from .window import SuprmanWindow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .server import ControlServer

APP_ID = "io.github.suprman.Suprman"

mark("imports done")


def _warn(message: str) -> None:
    ipc.announce(message)


class SuprmanApp(Gtk.Application):
    """Single-window application; ``serve`` keeps it alive between popups."""

    def __init__(
        self,
        cfg: Config,
        debug: bool = False,
        serve: bool = False,
        idle_exit_minutes: int = 0,
    ) -> None:
        # GTK4 moved the application flags to Gio; Gtk.ApplicationFlags only
        # exists in GTK3. NON_UNIQUE keeps a second launch from waiting on a
        # remote instance: handoff goes through our own socket instead.
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = cfg
        self.debug = debug
        self.serve = serve
        self.idle_exit_minutes = idle_exit_minutes
        self._css_loaded = False
        self._server: ControlServer | None = None
        self._idle_source = 0

    def do_activate(self) -> None:
        self._load_css()
        window = self.props.active_window
        if window is None:
            window = SuprmanWindow(self, self.config)
            window.keep_alive = self.serve
            mark("window created")

        if self.serve:
            self._start_server(window)
            self._arm_idle_timer()
            mark("serving")
            return

        window.present()
        window.focus_default()
        GLib.idle_add(self._mark_presented)
        # Warm up on the way out, never while this window is still showing: a
        # resident instance would otherwise answer the next press with a second
        # window on top of this one.
        window.connect("close-request", self._on_cold_window_closing)

    def _mark_presented(self) -> bool:
        """First idle callback after present(): the window is on screen."""
        mark("presented")
        # `SUPRMAN_TIMING=1` also reports where the keyboard went; "the cursor
        # is in the wrong place" is otherwise impossible to debug remotely.
        window = self.props.active_window
        if window is not None:
            focus = window.get_focus()
            name = type(focus).__name__ if focus is not None else "none"
            mark(f"focus: {name}")
        return False

    def _on_cold_window_closing(self, window: Gtk.Window) -> bool:
        """Leave a warm instance behind so the next press costs ~30 ms."""
        if (
            self.config.behavior.resident == "auto"
            and not self.serve
            and ipc.spawn_server(self.config.behavior.resident_idle_minutes)
        ):
            mark("warm instance spawned")
        return False

    # -- resident lifetime -------------------------------------------------

    def _arm_idle_timer(self) -> None:
        """Restart the "nobody used me for a while" countdown."""
        if self.idle_exit_minutes <= 0:
            return
        if self._idle_source:
            GLib.source_remove(self._idle_source)
        self._idle_source = GLib.timeout_add_seconds(
            self.idle_exit_minutes * 60, self._on_idle_timeout
        )

    def _on_idle_timeout(self) -> bool:
        window = self.props.active_window
        if window is not None and window.get_visible():
            return True  # in use; check again after another interval
        self._idle_source = 0
        self.quit()
        return False

    # -- resident mode -----------------------------------------------------

    def _start_server(self, window: SuprmanWindow) -> None:
        from .server import ControlServer

        server = ControlServer(self._on_command)
        if not server.start():
            ipc.clear_spawn_marker()
            _warn("another suprman instance is already serving; exiting")
            self.quit()
            return
        ipc.clear_spawn_marker()
        self._server = server
        self.connect("shutdown", self._on_shutdown)

    def _on_shutdown(self, application: Gtk.Application) -> None:
        server = self._server
        if server is not None:
            server.stop()

    def _on_command(self, command: str) -> None:
        window = self.props.active_window
        if window is None:
            return
        mark(f"command: {command}")
        self._arm_idle_timer()

        if command == "quit":
            self.quit()
        elif command == "hide":
            window.set_visible(False)
        elif command == "show":
            self._show(window)
        elif command == "toggle":
            if window.get_visible():
                window.set_visible(False)
            else:
                self._show(window)

    def _show(self, window: SuprmanWindow) -> None:
        window.reset()
        window.present()
        window.focus_default()

    # -- stylesheet --------------------------------------------------------

    def _load_css(self) -> None:
        if self._css_loaded:
            return
        display = Gdk.Display.get_default()
        if display is None:  # pragma: no cover - only without a display
            _warn("no display available")
            return
        try:
            css = config_module.build_css(self.config)
        except OSError as error:
            _warn(f"cannot read the stylesheet: {error}")
            return

        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(css.encode("utf-8"))
        except GLib.Error as error:
            _warn(f"stylesheet rejected by GTK: {error.message}")
            return

        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        mark("stylesheet applied")
        self._css_loaded = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="suprman",
        description="A hotkey popup for Vim keybinds and man pages.",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="FILE",
        help="read settings from FILE instead of ~/.config/suprman/suprman.conf",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="stay resident with the window hidden; later launches toggle it instantly",
    )
    parser.add_argument(
        "--quit",
        action="store_true",
        help="stop a resident instance and exit",
    )
    parser.add_argument(
        "--idle-exit",
        type=int,
        default=0,
        metavar="MINUTES",
        help="with --serve: exit after MINUTES idle (0 = stay until told to quit)",
    )
    parser.add_argument(
        "--example-config",
        action="store_true",
        help="write an annotated config file (if missing) and exit",
    )
    parser.add_argument("--debug", action="store_true", help="log configuration problems")
    parser.add_argument("--version", action="version", version=f"suprman {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.quit:
        if ipc.send("quit"):
            return 0
        _warn("no resident instance to quit")
        return 1

    if args.example_config:
        path = config_module.write_example_config(args.config)
        print(path)
        return 0

    cfg = config_module.load(args.config, debug=args.debug)
    mark("config loaded")
    if args.debug:
        _warn(f"config: {cfg.path or 'built-in defaults'}")
        _warn(f"man sections: {', '.join(cfg.behavior.section_list())}")
        _warn(f"tldr: {'enabled' if cfg.behavior.tldr else 'disabled'}")
        _warn(f"vim keys: {'on (normal mode)' if cfg.behavior.vim_mode else 'off'}")

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        _warn("no WAYLAND_DISPLAY or DISPLAY set; nothing to draw on")

    app = SuprmanApp(cfg, debug=args.debug, serve=args.serve, idle_exit_minutes=args.idle_exit)

    def quit_on_signal() -> bool:
        app.quit()
        return False

    if hasattr(GLib, "unix_signal_add"):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, quit_on_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, quit_on_signal)

    return app.run([sys.argv[0]])
