"""Import the GTK layer and pin the API points that differ from GTK3.

Skipped where GTK4 is not installed, so the rest of the suite stays runnable on
a headless box. On a machine with GTK4 this doubles as an import smoke test: it
catches wrong module paths, missing names and GTK3 leftovers before a window is
ever opened.
"""

from __future__ import annotations

import os
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


def load_gtk():
    """Return ``(Gdk, Gio, Gtk)``, or ``None`` when GTK4 is unavailable."""
    try:  # pragma: no cover - environment dependent
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib, Gtk
    except (ImportError, ValueError):  # pragma: no cover - environment dependent
        return None
    return Gdk, Gio, GLib, Gtk


GTK = load_gtk()


def has_display() -> bool:
    """True when a GDK backend actually produced a display.

    Constructing a widget without one does not raise: it segfaults deep inside
    GDK, so every widget-building test must be gated on this, not on the
    typelib merely being importable.
    """
    if GTK is None:
        return False
    Gdk, _Gio, _GLib, Gtk = GTK
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
        self.Gdk, self.Gio, self.GLib, self.Gtk = GTK

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


class _Focusable:
    """Stand-in for a widget, recording focus and focusability."""

    def __init__(self) -> None:
        self.focused = 0
        self.can_focus = True

    def grab_focus(self) -> None:
        self.focused += 1

    def set_can_focus(self, can_focus: bool) -> None:
        self.can_focus = can_focus


class _Label:
    """Stand-in for Gtk.Label."""

    def __init__(self, visible: bool = False) -> None:
        self.text = ""
        self.visible = visible

    def set_text(self, text: str) -> None:
        self.text = text

    def set_visible(self, visible: bool) -> None:
        self.visible = visible

    def get_visible(self) -> bool:
        return self.visible


if GTK is not None:  # pragma: no branch - GTK-less runs skip the tests below
    from suprman.window import SuprmanWindow

    class _FakeWindow:
        """A window-shaped object carrying the real key handling.

        Constructing widgets without a display segfaults, so only the methods
        under test are taken from :class:`SuprmanWindow`; everything they touch
        (labels, focus targets, the selected row) is a stand-in.
        """

        _on_key_pressed = SuprmanWindow._on_key_pressed
        _vim_normal_key = SuprmanWindow._vim_normal_key
        _command_key = SuprmanWindow._command_key
        _start_command = SuprmanWindow._start_command
        _render_command = SuprmanWindow._render_command
        _end_command = SuprmanWindow._end_command
        _on_map = SuprmanWindow._on_map
        _focus_after_map = SuprmanWindow._focus_after_map
        _typing = SuprmanWindow._typing
        _on_list_clicked = SuprmanWindow._on_list_clicked
        _is_text_key = staticmethod(SuprmanWindow._is_text_key)

        def __init__(self, vim_mode: bool = True) -> None:
            from suprman import vimnav

            self.config = types.SimpleNamespace(behavior=types.SimpleNamespace(vim_mode=vim_mode))
            self._command = vimnav.CommandLine()
            self._pending_g = False
            self._hint = _Label(visible=True)
            self._command_label = _Label()
            self.search = _Focusable()
            self.listbox = _Focusable()
            self._rows_data: list[object] = [object() for _ in range(30)]
            self.reader = None
            self._insert_mode = False
            # Stand-in for "the keyboard is in the entry".
            self.insert = False
            self._selected = 0
            self.moves: list[int] = []
            self.closed = 0
            self.activated = 0

        # -- collaborators the handler calls ------------------------------

        def _text_entry_focused(self) -> bool:
            # A field only counts if it is focused *and* focusable, matching
            # the real implementation.
            return self.insert and self.search.can_focus

        def focus_default(self) -> None:
            SuprmanWindow.focus_default(self)
            self.insert = not self.config.behavior.vim_mode

        def _enter_insert_mode(self) -> None:
            SuprmanWindow._enter_insert_mode(self)
            self.insert = True

        def _reader_visible(self) -> bool:
            return False

        def _select(self, index: int) -> None:
            # Same clamp as the real list, without the widget plumbing.
            index = max(0, min(len(self._rows_data) - 1, index))
            self.moves.append(index)
            self._selected = index

        def _activate(self) -> None:
            self.activated += 1

        def close(self) -> None:
            self.closed += 1

        # -- test helper ---------------------------------------------------

        def press(self, name: str, ctrl: bool = False) -> bool:
            """Send a key by GDK name (``j``, ``slash``, ``Return``, ...)."""
            Gdk = GTK[0]  # type: ignore[index]
            keyval = Gdk.keyval_from_name(name)
            state = Gdk.ModifierType.CONTROL_MASK if ctrl else Gdk.ModifierType(0)
            return SuprmanWindow._on_key_pressed(self, None, keyval, 0, state)

        def type_text(self, text: str) -> None:
            for char in text:
                self.press(char)


@unittest.skipUnless(GTK is not None, "GTK4/PyGObject is not installed")
class KeyHandlingTest(unittest.TestCase):
    """Normal mode, insert mode and the ':' prompt, driven key by key."""

    def setUp(self) -> None:
        self.fake = _FakeWindow()

    # -- focus policy ------------------------------------------------------

    def test_normal_mode_focuses_the_list_not_the_entry(self) -> None:
        self.fake.focus_default()
        self.assertEqual(self.fake.listbox.focused, 1)
        self.assertEqual(self.fake.search.focused, 0)
        # Unfocusable is the part that makes it stick: GTK cannot hand the
        # keyboard to the field at map time or on a stray click.
        self.assertFalse(self.fake.search.can_focus)

    def test_slash_makes_the_field_focusable_again(self) -> None:
        self.fake.focus_default()
        self.assertTrue(self.fake.press("slash"))
        self.assertTrue(self.fake.search.can_focus)
        self.assertEqual(self.fake.search.focused, 1)

    def test_escape_from_insert_mode_makes_the_field_unfocusable(self) -> None:
        self.fake.press("slash")
        self.assertTrue(self.fake._insert_mode)
        self.fake.press("Escape")
        self.assertFalse(self.fake._insert_mode)
        self.assertFalse(self.fake.search.can_focus)
        self.assertEqual(self.fake.listbox.focused, 1)
        self.assertEqual(self.fake.closed, 0)

    def test_vim_mode_off_keeps_focus_in_the_search_field(self) -> None:
        fake = _FakeWindow(vim_mode=False)
        fake.focus_default()
        self.assertEqual(fake.search.focused, 1)
        self.assertEqual(fake.listbox.focused, 0)
        self.assertTrue(fake.search.can_focus)

    def test_mapping_the_window_reclaims_normal_mode(self) -> None:
        # GTK assigns initial focus at map time, so the popup must re-assert
        # normal mode afterwards or it opens with the caret in the entry.
        self.fake._on_map(None)
        self.assertEqual(self.fake.listbox.focused, 1)
        self.assertEqual(self.fake.search.focused, 0)
        self.fake._focus_after_map()
        self.assertEqual(self.fake.listbox.focused, 2)

    def test_mapping_leaves_the_entry_alone_when_vim_mode_is_off(self) -> None:
        fake = _FakeWindow(vim_mode=False)
        fake._on_map(None)
        fake._focus_after_map()
        self.assertEqual(fake.search.focused, 0)
        self.assertEqual(fake.listbox.focused, 0)

    # -- normal mode -------------------------------------------------------

    def test_j_and_k_move_one_row(self) -> None:
        self.assertTrue(self.fake.press("j"))
        self.assertTrue(self.fake.press("k"))
        self.assertEqual(self.fake.moves, [1, 0])

    def test_ctrl_d_and_ctrl_u_move_a_half_page(self) -> None:
        self.assertTrue(self.fake.press("d", ctrl=True))
        self.assertTrue(self.fake.press("u", ctrl=True))
        self.assertEqual(self.fake.moves, [10, 0])

    def test_shift_g_jumps_to_the_last_row(self) -> None:
        self.assertTrue(self.fake.press("G"))
        self.assertEqual(self.fake.moves, [len(self.fake._rows_data) - 1])

    def test_gg_jumps_to_the_top(self) -> None:
        self.assertTrue(self.fake.press("g"))
        self.assertTrue(self.fake._pending_g, "first g must arm the sequence")
        self.assertTrue(self.fake.press("g"))
        self.assertEqual(self.fake.moves, [0])
        self.assertFalse(self.fake._pending_g)

    def test_a_pending_g_does_not_swallow_the_next_key(self) -> None:
        self.fake.press("g")
        self.assertTrue(self.fake.press("j"))
        self.assertEqual(self.fake.moves, [1])

    def test_slash_focuses_the_search_field(self) -> None:
        self.assertTrue(self.fake.press("slash"))
        self.assertEqual(self.fake.search.focused, 1)
        self.assertEqual(self.fake.moves, [])

    def test_l_opens_the_selected_row(self) -> None:
        self.assertTrue(self.fake.press("l"))
        self.assertEqual(self.fake.activated, 1)

    def test_h_does_nothing_in_the_list(self) -> None:
        self.assertTrue(self.fake.press("h"))
        self.assertEqual((self.fake.moves, self.fake.activated), ([], 0))

    def test_normal_mode_swallows_stray_printable_keys(self) -> None:
        # Nothing should reach a widget as text in normal mode.
        self.assertTrue(self.fake.press("z"))
        self.assertEqual(self.fake.moves, [])
        self.assertTrue(self.fake.press("q"))

    def test_non_text_keys_still_reach_gtk(self) -> None:
        self.assertFalse(self.fake.press("F5"))

    def test_normal_mode_ignores_a_focused_but_unfocusable_entry(self) -> None:
        # Exactly the reported bug: GTK hands the field focus at map time, yet
        # the field is unfocusable in normal mode, so j must still navigate.
        self.fake.search.can_focus = False
        self.fake.insert = True
        self.assertTrue(self.fake.press("j"))
        self.assertEqual(self.fake.moves, [1])

    def test_insert_mode_lets_letters_type(self) -> None:
        self.fake.insert = True
        self.assertFalse(self.fake.press("j"))
        self.assertFalse(self.fake.press("k"))
        self.assertEqual(self.fake.moves, [])

    def test_insert_mode_slash_types_a_slash(self) -> None:
        self.fake.insert = True
        self.assertFalse(self.fake.press("slash"))
        self.assertEqual(self.fake.search.focused, 0)

    # -- escape ------------------------------------------------------------

    def test_escape_closes_in_normal_mode(self) -> None:
        self.fake.press("Escape")
        self.assertEqual(self.fake.closed, 1)

    def test_escape_leaves_insert_mode_instead_of_quitting(self) -> None:
        self.fake.insert = True
        self.fake.press("Escape")
        self.assertEqual(self.fake.closed, 0)
        self.assertEqual(self.fake.listbox.focused, 1)

    def test_escape_closes_when_vim_mode_is_off(self) -> None:
        self.fake.config.behavior.vim_mode = False
        self.fake.press("Escape")
        self.assertEqual(self.fake.closed, 1)

    # -- ':' prompt --------------------------------------------------------

    def test_colon_opens_the_prompt(self) -> None:
        self.assertTrue(self.fake.press("colon"))
        self.assertTrue(self.fake._command.active)
        self.assertTrue(self.fake._command_label.get_visible())
        self.assertFalse(self.fake._hint.get_visible())
        self.assertEqual(self.fake._command_label.text, ":▏")

    def test_colon_q_enter_quits(self) -> None:
        self.fake.press("colon")
        self.fake.type_text("q")
        self.assertEqual(self.fake._command_label.text, ":q▏")
        self.fake.press("Return")
        self.assertEqual(self.fake.closed, 1)
        self.assertFalse(self.fake._command.active)
        self.assertTrue(self.fake._hint.get_visible())

    def test_escape_cancels_the_prompt_without_quitting(self) -> None:
        self.fake.press("colon")
        self.fake.type_text("q")
        self.fake.press("Escape")
        self.assertEqual(self.fake.closed, 0)
        self.assertFalse(self.fake._command.active)
        self.assertTrue(self.fake._hint.get_visible())

    def test_an_unknown_command_does_not_quit(self) -> None:
        self.fake.press("colon")
        self.fake.type_text("wq")
        self.fake.press("Return")
        self.assertEqual(self.fake.closed, 0)

    def test_backspace_on_an_empty_prompt_closes_it(self) -> None:
        self.fake.press("colon")
        self.fake.press("BackSpace")
        self.assertFalse(self.fake._command.active)
        self.assertTrue(self.fake._hint.get_visible())

    def test_keys_do_not_navigate_while_the_prompt_is_open(self) -> None:
        self.fake.press("colon")
        self.fake.press("j")
        self.assertEqual(self.fake.moves, [])
        self.assertEqual(self.fake._command.text, "j")


@unittest.skipUnless(GTK is not None, "GTK4/PyGObject is not installed")
class ControlServerTest(unittest.TestCase):
    """The resident-instance socket runs on Gio only, so no display is needed."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_commands_reach_the_callback(self) -> None:
        from suprman import ipc
        from suprman.server import ControlServer

        received: list[str] = []
        server = ControlServer(received.append)
        self.addCleanup(server.stop)
        self.assertTrue(server.start(), "server did not start")
        self.assertTrue(server.running)

        self.assertTrue(ipc.send("toggle"))
        self.assertTrue(self._pump_until(lambda: received), "no command arrived")
        self.assertEqual(received, ["toggle"])

    def test_ping_is_not_forwarded(self) -> None:
        from suprman import ipc
        from suprman.server import ControlServer

        received: list[str] = []
        server = ControlServer(received.append)
        self.addCleanup(server.stop)
        self.assertTrue(server.start())

        self.assertTrue(ipc.send("ping"))
        self._pump_until(lambda: False, timeout=0.3)
        self.assertEqual(received, [])

    def test_second_server_refuses_and_leaves_the_first_running(self) -> None:
        from suprman.server import ControlServer

        first = ControlServer(lambda command: None)
        self.addCleanup(first.stop)
        self.assertTrue(first.start())
        second = ControlServer(lambda command: None)
        self.assertFalse(second.start())
        self.assertTrue(first.running)

    def test_stop_removes_the_socket(self) -> None:
        from suprman import ipc
        from suprman.server import ControlServer

        server = ControlServer(lambda command: None)
        self.assertTrue(server.start())
        self.assertTrue(ipc.socket_path().exists())
        server.stop()
        self.assertFalse(ipc.socket_path().exists())

    @staticmethod
    def _pump_until(predicate, timeout: float = 2.0) -> bool:
        """Run the default main context until ``predicate`` is true."""
        context = GTK[2].MainContext.default()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if predicate():
                return True
            time.sleep(0.005)
        return bool(predicate())


if __name__ == "__main__":
    unittest.main()
