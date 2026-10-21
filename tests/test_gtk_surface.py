"""Import the GTK layer and pin the API points that differ from GTK3.

Skipped where GTK4 is not installed, so the rest of the suite stays runnable on
a headless box. On a machine with GTK4 this doubles as an import smoke test: it
catches wrong module paths, missing names and GTK3 leftovers before a window is
ever opened.
"""

from __future__ import annotations

import unittest


def load_gtk():
    """Return ``(Gdk, Gio, Gtk)``, or ``None`` when GTK4 is unavailable."""
    try:  # pragma: no cover - environment dependent
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, Gtk
    except (ImportError, ValueError):  # pragma: no cover - environment dependent
        return None
    return Gdk, Gio, Gtk


GTK = load_gtk()


def has_display() -> bool:
    """True when a GDK backend actually produced a display.

    Constructing a widget without one does not raise: it segfaults deep inside
    GDK, so every widget-building test must be gated on this, not on the
    typelib merely being importable.
    """
    if GTK is None:
        return False
    Gdk, _Gio, Gtk = GTK
    try:
        Gtk.init()
    except Exception:  # pragma: no cover - environment dependent
        return False
    return Gdk.Display.get_default() is not None


DISPLAY_AVAILABLE = has_display()


@unittest.skipUnless(GTK is not None, "GTK4 is not installed")
class GtkSurfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        assert GTK is not None  # narrowed for readers and type checkers
        self.Gdk, self.Gio, self.Gtk = GTK

    def test_application_flags_live_in_gio(self) -> None:
        # Gtk.ApplicationFlags was a GTK3 thing; using it raises AttributeError
        # as soon as the application object is built on GTK4.
        self.assertTrue(hasattr(self.Gio.ApplicationFlags, "NON_UNIQUE"))
        self.assertFalse(hasattr(self.Gtk, "ApplicationFlags"))

    def test_expected_keyvals_and_modifiers_exist(self) -> None:
        for name in (
            "KEY_Escape",
            "KEY_Return",
            "KEY_KP_Enter",
            "KEY_Page_Up",
            "KEY_Page_Down",
            "KEY_Tab",
            "KEY_KP_Tab",
            "KEY_ISO_Left_Tab",
        ):
            self.assertTrue(hasattr(self.Gdk, name), name)
        self.assertTrue(hasattr(self.Gdk.ModifierType, "CONTROL_MASK"))
        self.assertTrue(hasattr(self.Gdk.ModifierType, "SHIFT_MASK"))

    def test_constants_used_by_the_window_exist(self) -> None:
        self.assertIsNotNone(self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.assertIsNotNone(self.Gtk.PropagationPhase.CAPTURE)
        self.assertIsNotNone(self.Gtk.SelectionMode.SINGLE)
        self.assertIsNotNone(self.Gtk.StackTransitionType.NONE)

    def test_gtk_modules_import(self) -> None:
        from suprman import app, reader, window

        self.assertEqual(app.APP_ID, "io.github.suprman.Suprman")
        self.assertTrue(callable(app.main))
        self.assertTrue(hasattr(window, "SuprmanWindow"))
        self.assertTrue(hasattr(reader, "ReaderView"))

    @unittest.skipUnless(DISPLAY_AVAILABLE, "no GDK display, widgets would crash")
    def test_reader_builds(self) -> None:
        from suprman.config import Config
        from suprman.reader import ReaderView

        reader = ReaderView(Config())
        reader.begin("ls(1)", "man 1")
        reader.add_man_text("NAME\n       ls - list directory contents\n")
        reader.search.set_text("list")
        self.assertIn("directory", reader.text)
        self.assertIsInstance(reader, self.Gtk.Box)


if __name__ == "__main__":
    unittest.main()
