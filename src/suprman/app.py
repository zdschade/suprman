"""Application entry point: argument handling, stylesheet, GTK application."""

from __future__ import annotations

import argparse
import os
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from . import __version__
from . import config as config_module
from .config import Config
from .timing import mark
from .window import SuprmanWindow

APP_ID = "io.github.suprman.Suprman"

mark("imports done")


def _warn(message: str) -> None:
    print(f"suprman: {message}", file=sys.stderr)


class SuprmanApp(Gtk.Application):
    """A single-window application; launching again opens a new popup."""

    def __init__(self, cfg: Config, debug: bool = False) -> None:
        # GTK4 moved the application flags to Gio; Gtk.ApplicationFlags only
        # exists in GTK3.
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = cfg
        self.debug = debug
        self._css_loaded = False

    def do_activate(self) -> None:
        self._load_css()
        window = self.props.active_window
        if window is None:
            window = SuprmanWindow(self, self.config)
            mark("window created")
        window.present()
        GLib.idle_add(self._mark_presented)

    @staticmethod
    def _mark_presented() -> bool:
        """First idle callback after present(): the window is on screen."""
        mark("presented")
        return False

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
        "--example-config",
        action="store_true",
        help="write an annotated config file (if missing) and exit",
    )
    parser.add_argument("--debug", action="store_true", help="log configuration problems")
    parser.add_argument("--version", action="version", version=f"suprman {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        _warn("no WAYLAND_DISPLAY or DISPLAY set; nothing to draw on")

    app = SuprmanApp(cfg, debug=args.debug)

    def quit_on_signal() -> bool:
        app.quit()
        return False

    if hasattr(GLib, "unix_signal_add"):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, quit_on_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, quit_on_signal)

    return app.run([sys.argv[0]])
