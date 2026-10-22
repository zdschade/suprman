"""The popup window itself.

One text field drives everything: it filters Vim keybinds by default, switches
to the man page list on a ``man`` prefix, and stays usable inside the reader.
Much of the code here is bookkeeping to keep the UI responsive while pages are
being rendered or fetched on worker threads.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from . import fuzzy, layershell, manpages, query, tldr, vimkeys, vimnav
from .config import Config
from .reader import PART_MAN, PART_STATUS, PART_TLDR, Block, ReaderView
from .timing import mark

PAGE_LIST = "list"
PAGE_READER = "reader"

#: Each row is a ListBoxRow plus three labels, and GTK shapes the text of every
#: one at first paint, so building all `max_results` rows up front is the single
#: biggest chunk of launch work. Only a screenful is built; the rest arrives as
#: the list is scrolled.
ROWS_PER_SCROLL = 20

#: Roughly the vertical space taken by the search field, separator, footer and
#: padding, used to guess how many rows fill the configured window.
CHROME_HEIGHT = 130

PLACEHOLDER = "search vim keybinds — try “delete line”, or type “man ls”"
CHIP_VIM = "VIM KEYS"
CHIP_MAN = "MAN PAGES"
CHIP_TLDR = "TLDR"
CHIP_READER = "READER"

HINT = "↑↓ move · Enter open · Esc quit · Tab next · “man ls” opens a page"
HINT_VIM = "j/k move · Enter open · / search · :q quit"

#: Rows moved by a half-page key (Ctrl-d/Ctrl-u) and by PgUp/PgDn.
HALF_PAGE_ROWS = 10


@dataclass
class RowData:
    """One result row, rendered by :class:`ResultRow`."""

    keys: str
    description: str
    meta: str = ""
    activate: Callable[[], None] | None = None


class ResultRow(Gtk.ListBoxRow):
    """A reusable row: key sequence on the left, description, section on the right."""

    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("suprman-row")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.keys_label = Gtk.Label(xalign=0.0)
        self.keys_label.add_css_class("row-keys")
        self.keys_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.keys_label.set_width_chars(12)

        self.description_label = Gtk.Label(xalign=0.0, hexpand=True)
        self.description_label.add_css_class("row-desc")
        self.description_label.set_ellipsize(Pango.EllipsizeMode.END)

        self.meta_label = Gtk.Label(xalign=1.0)
        self.meta_label.add_css_class("row-meta")
        self.meta_label.set_ellipsize(Pango.EllipsizeMode.END)

        box.append(self.keys_label)
        box.append(self.description_label)
        box.append(self.meta_label)
        self.set_child(box)

    def bind(self, data: RowData) -> None:
        self.keys_label.set_text(data.keys)
        self.description_label.set_text(data.description)
        self.meta_label.set_text(data.meta)
        self.keys_label.set_visible(bool(data.keys))
        self.meta_label.set_visible(bool(data.meta))


class SuprmanWindow(Gtk.ApplicationWindow):
    """Frameless, centred popup with a search field and a result list."""

    def __init__(self, application: Gtk.Application, config: Config) -> None:
        super().__init__(application=application)
        self.config = config

        self._binds = vimkeys.load(config.behavior.vim_keys_file or None)
        mark(f"keybinds: {len(self._binds)}")
        self._pages: list[manpages.ManPage] | None = None
        self._pages_loading = False
        self._pages_error = ""
        self._tldr = tldr.Tldr(
            cache_dir=config.tldr_cache_dir,
            ttl_days=config.behavior.tldr_ttl_days,
            platform=config.behavior.tldr_platform,
            enabled=config.behavior.tldr,
        )

        self._rows_data: list[RowData] = []
        self._row_widgets: list[ResultRow] = []
        self._shown = 0  # how many of _rows_data have live widgets
        self._repopulating = False
        self._selected = -1
        self._generation = 0
        self._auto_opened_for = ""
        self._closing = False
        # Vim normal mode: the ":" prompt and the "gg" prefix are the only
        # state the key handler carries between presses.
        self._command = vimnav.CommandLine()
        self._pending_g = False

        self.set_title("suprman")
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("suprman")
        self.set_default_size(config.appearance.width, config.appearance.height)

        self.keep_alive = False  # set by the app in --serve mode
        self.connect("close-request", self._on_close_request)
        self._build()
        self._install_controllers()
        # Normal mode: the field must not be able to take focus at all. Doing
        # this up front (not only on map) means GTK's initial-focus pass has
        # nothing to hand the keyboard to but the list.
        if config.behavior.vim_mode:
            self.search.set_can_focus(False)
        layershell.attach(self, namespace="suprman", mode=config.behavior.layer_shell)

        self.show_vim("")
        mark("window built")

    def _on_close_request(self, window: Gtk.Window) -> bool:
        """Hide in resident mode; destroy the window on a one-shot launch."""
        self._generation += 1  # drop any in-flight page load either way
        if self.keep_alive:
            self.set_visible(False)
            return True  # stop the default handler so the instance stays alive
        self._closing = True
        return False

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        surface.add_css_class("suprman-surface")
        self.set_child(surface)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(PLACEHOLDER)
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._on_search_changed)
        self.chip = Gtk.Label(label=CHIP_VIM)
        self.chip.add_css_class("chip")
        self.chip.set_valign(Gtk.Align.CENTER)
        header.append(self.search)
        header.append(self.chip)
        surface.append(header)

        surface.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True)
        surface.append(self.stack)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("suprman-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.connect("row-activated", self._on_row_activated)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(self.listbox)
        self.scroller.get_vadjustment().connect("value-changed", self._on_scroll)
        self.stack.add_named(self.scroller, PAGE_LIST)

        # The reader is built on the first page view: a TextView, a TextBuffer,
        # eight tags and a search entry that most launches never show.
        self.reader: ReaderView | None = None

        self._empty_label = Gtk.Label(xalign=0.0)
        self._empty_label.add_css_class("row-empty")
        self._empty_label.set_wrap(True)
        self._empty_row = Gtk.ListBoxRow()
        self._empty_row.set_selectable(False)
        self._empty_row.set_activatable(False)
        self._empty_row.set_child(self._empty_label)
        self._empty_row.set_visible(False)
        self.listbox.append(self._empty_row)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._count = Gtk.Label(xalign=0.0)
        self._count.add_css_class("footer")
        self._count.set_hexpand(True)
        # The ":" prompt replaces the hint label while it is open, so the
        # footer never changes height on entry.
        self._command_label = Gtk.Label(xalign=1.0)
        self._command_label.add_css_class("cmdline")
        self._command_label.set_visible(False)
        hint = HINT_VIM if self.config.behavior.vim_mode else HINT
        self._hint = Gtk.Label(label=hint, xalign=1.0)
        self._hint.add_css_class("footer")
        footer.append(self._count)
        footer.append(self._command_label)
        footer.append(self._hint)
        surface.append(footer)

    def _install_controllers(self) -> None:
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)
        # GTK assigns the initial focus while mapping the window (to the first
        # focusable widget: the search entry), which happens after present()
        # and therefore after app.do_activate asked for normal mode. Without
        # this hook a fresh popup comes up in insert mode.
        self.connect("map", self._on_map)
        # The entry is unfocusable in normal mode, so a mouse click has to opt
        # into insert mode explicitly.
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_search_clicked)
        self.search.add_controller(click)

    # -- search ------------------------------------------------------------

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._auto_opened_for = ""
        if self.stack.get_visible_child_name() != PAGE_LIST:
            self.stack.set_visible_child_name(PAGE_LIST)
        self._refresh(entry.get_text())

    def _refresh(self, text: str, allow_auto: bool = True) -> None:
        parsed = query.parse(text)
        if parsed.mode is query.Mode.VIM:
            self.show_vim(parsed.term)
        else:
            self.show_man(parsed.term, text, allow_auto=allow_auto)

    def show_vim(self, term: str) -> None:
        """Render the (filtered) keybind list."""
        self._set_chip(CHIP_VIM)
        limit = self.config.behavior.max_results
        binds = fuzzy.rank(
            term,
            self._binds,
            lambda bind: bind.haystack,
            limit=limit,
            case_sensitive=self.config.behavior.case_sensitive,
        )
        rows = [
            RowData(keys=bind.keys, description=bind.description, meta=bind.section_title)
            for bind in binds
        ]
        self._set_rows(rows, "no keybind matches — try “delete line”, or “man grep”")

    def show_man(self, term: str, raw_text: str = "", allow_auto: bool = True) -> None:
        """Render the man page list, loading the index on first use."""
        self._set_chip(CHIP_MAN if not query.parse(raw_text).wants_tldr else CHIP_TLDR)

        if self._pages is None:
            self._start_index_load()
            message = "indexing installed man pages…"
            if self._pages_error:
                message = f"could not index man pages: {self._pages_error}"
            self._set_rows([], message)
            return

        limit = self.config.behavior.max_results
        pages = fuzzy.rank(
            term,
            self._pages,
            lambda page: page.haystack,
            limit=limit,
            case_sensitive=self.config.behavior.case_sensitive,
        )
        rows = [
            RowData(
                keys=page.title,
                description=page.description,
                meta="",
                activate=lambda page=page: self.open_page(page),
            )
            for page in pages
        ]
        self._set_rows(rows, "no man page matches — try “man grep”", total=len(self._pages))

        if (
            allow_auto
            and term
            and self.config.behavior.open_exact
            and raw_text != self._auto_opened_for
        ):
            exact = next(
                (page for page in self._pages if page.name == term or page.title == term), None
            )
            if exact is not None:
                self._auto_opened_for = raw_text
                self.open_page(exact)

    # -- man index ---------------------------------------------------------

    def _start_index_load(self) -> None:
        if self._pages_loading or self._pages is not None:
            return
        self._pages_loading = True
        threading.Thread(target=self._worker_index, daemon=True).start()

    def _worker_index(self) -> None:
        pages: list[manpages.ManPage] = []
        error = ""
        if not manpages.available():
            error = "the 'man' command is not installed"
        else:
            try:
                pages = manpages.load_index(
                    self.config.behavior.section_list(), self.config.index_cache_dir
                )
            except Exception as exc:  # pragma: no cover - defensive, worker thread
                error = str(exc)
        GLib.idle_add(self._finish_index, pages, error)

    def _finish_index(self, pages: list[manpages.ManPage], error: str) -> bool:
        if self._closing:
            return False
        self._pages_loading = False
        self._pages = pages
        self._pages_error = error
        self._refresh(self.search.get_text(), allow_auto=True)
        return False

    # -- reader ------------------------------------------------------------

    def _ensure_reader(self) -> ReaderView:
        """Build the reader the first time a page is opened."""
        if self.reader is None:
            self.reader = ReaderView(self.config)
            self.stack.add_named(self.reader, PAGE_READER)
            mark("reader created")
        return self.reader

    def open_page(self, page: manpages.ManPage) -> None:
        """Show a man page (with its tldr page above it) in the reader."""
        self._generation += 1
        generation = self._generation
        subtitle = f"man {page.section}"
        if page.description:
            subtitle = f"{subtitle} · {page.description}"
        reader = self._ensure_reader()
        reader.begin(page.title, subtitle)
        reader.set_part(PART_STATUS, "", [("desc", "loading…")], order=0)
        self._set_chip(CHIP_READER)
        self.stack.set_visible_child_name(PAGE_READER)
        reader.focus_default()

        if self.config.behavior.tldr:
            threading.Thread(target=self._worker_tldr, args=(generation, page), daemon=True).start()
        threading.Thread(target=self._worker_man, args=(generation, page), daemon=True).start()

    def _worker_tldr(self, generation: int, page: manpages.ManPage) -> None:
        markdown = self._tldr.fetch(page.name)
        blocks: list[Block] = tldr.present(markdown) if markdown else []
        GLib.idle_add(self._show_tldr, generation, blocks)

    def _show_tldr(self, generation: int, blocks: list[Block]) -> bool:
        reader = self.reader
        if reader is None or self._closing or generation != self._generation:
            return False
        if blocks:
            reader.set_part(PART_TLDR, "TLDR · tldr.sh", blocks, order=10)
        else:
            reader.set_part(
                PART_TLDR,
                "TLDR · tldr.sh",
                [("desc", "no tldr page for this command (yet)")],
                order=10,
            )
        reader.remove_part(PART_STATUS)
        return False

    def _worker_man(self, generation: int, page: manpages.ManPage) -> None:
        text = ""
        error = ""
        try:
            text = manpages.render_page(page.name, page.section, self.config.behavior.man_width)
        except manpages.ManError as exc:
            error = str(exc)
        GLib.idle_add(self._show_man_page, generation, text, error)

    def _show_man_page(self, generation: int, text: str, error: str) -> bool:
        reader = self.reader
        if reader is None or self._closing or generation != self._generation:
            return False
        if error:
            reader.set_part(PART_MAN, "MAN PAGE", [("desc", error)], order=20)
        else:
            reader.add_man_text(text)
        reader.remove_part(PART_STATUS)
        reader.finish()
        return False

    def reset(self) -> None:
        """Return to a fresh popup: no query, no open page."""
        self._end_command()
        if self._reader_visible():
            self._close_reader()
        elif self.search.get_text():
            self.search.set_text("")
        else:
            self._refresh("")
        self.focus_default()

    def _close_reader(self) -> None:
        self._generation += 1  # invalidate in-flight loads
        self.stack.set_visible_child_name(PAGE_LIST)
        self.focus_default()
        self._refresh(self.search.get_text(), allow_auto=False)

    def _reader_visible(self) -> bool:
        return self.stack.get_visible_child_name() == PAGE_READER

    # -- rows --------------------------------------------------------------

    def _initial_rows(self) -> int:
        """A screenful of rows plus a little overscan, clamped to 12..40."""
        appearance = self.config.appearance
        visible = (appearance.height - CHROME_HEIGHT) // max(1, appearance.row_height)
        return min(max(int(visible) + 4, 12), 40)

    def _set_rows(self, rows: list[RowData], empty_message: str, total: int | None = None) -> None:
        self._repopulating = True
        try:
            self._rows_data = rows
            self._shown = 0
            for widget in self._row_widgets:
                widget.set_visible(False)
            # New results start at the top; this also keeps the lazy-growth
            # check from firing while the list is half rebuilt.
            self.scroller.get_vadjustment().set_value(0)

            has_rows = bool(rows)
            self._empty_label.set_text(empty_message)
            self._empty_row.set_visible(not has_rows)

            if has_rows:
                self._materialize(self._initial_rows())
                self._select(0)
            else:
                self._selected = -1
                self.listbox.select_row(None)
            self._update_count(len(rows), total)
        finally:
            self._repopulating = False

    def _materialize(self, extra: int) -> None:
        """Create, bind and show up to ``extra`` more rows."""
        target = min(len(self._rows_data), self._shown + max(0, extra))
        while len(self._row_widgets) < target:
            widget = ResultRow()
            self.listbox.insert(widget, len(self._row_widgets))
            self._row_widgets.append(widget)
        for index in range(self._shown, target):
            widget = self._row_widgets[index]
            widget.bind(self._rows_data[index])
            widget.set_visible(True)
        self._shown = target

    def _on_scroll(self, adjustment: Gtk.Adjustment) -> None:
        """Build the next batch as the bottom of the list comes into view."""
        if self._repopulating or self._shown >= len(self._rows_data):
            return
        remaining = adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value()
        if remaining < 3 * self.config.appearance.row_height:
            self._materialize(ROWS_PER_SCROLL)

    def _select(self, index: int) -> None:
        if not self._rows_data:
            return
        index = max(0, min(len(self._rows_data) - 1, index))
        was_shown = self._shown
        if index >= was_shown:
            self._materialize(index - was_shown + 1)

        self._selected = index
        widget = self._row_widgets[index]
        self.listbox.select_row(widget)
        self._ensure_visible(widget)
        if index >= was_shown:
            # A freshly built row has no allocation yet, so scroll again once
            # the layout pass has run.
            GLib.idle_add(self._ensure_visible, widget)
        # In vim normal mode the keyboard stays out of the search field; only
        # the insert mode ("/") puts it there.
        if not self.config.behavior.vim_mode:
            self.search.grab_focus()

    def _ensure_visible(self, widget: Gtk.Widget) -> None:
        """Scroll the selected row into view without stealing focus."""
        try:
            ok, rect = widget.compute_bounds(self.listbox)
        except (TypeError, AttributeError, ValueError):
            return
        if not ok:
            return
        adjustment = self.scroller.get_vadjustment()
        top = rect.get_y()
        bottom = top + rect.get_height()
        view_top = adjustment.get_value()
        view_bottom = view_top + adjustment.get_page_size()
        if top < view_top:
            adjustment.set_value(top)
        elif bottom > view_bottom:
            adjustment.set_value(bottom - adjustment.get_page_size())

    def _activate(self) -> None:
        if 0 <= self._selected < len(self._rows_data):
            action = self._rows_data[self._selected].activate
            if action is not None:
                action()
            else:
                self._copy_keys(self._rows_data[self._selected].keys)

    def _on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        for index, widget in enumerate(self._row_widgets):
            if widget is row and index < len(self._rows_data):
                self._selected = index
                self._activate()
                return

    def _copy_keys(self, keys: str) -> None:
        """Enter on a keybind row copies it, which is often what you want next."""
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(keys)
        except (AttributeError, TypeError):
            return
        self._count.set_text(f"copied  {keys}")

    # -- chrome ------------------------------------------------------------

    def _set_chip(self, text: str) -> None:
        self.chip.set_text(text)

    def _update_count(self, count: int, total: int | None = None) -> None:
        if self._reader_visible():
            return
        if total is not None and total != count:
            self._count.set_text(f"{count} of {total} man pages")
        elif count:
            self._count.set_text(f"{count} results")
        else:
            self._count.set_text("")

    # -- keyboard ----------------------------------------------------------

    def focus_default(self) -> None:
        """Send the keyboard where the current mode expects it.

        Vim mode opens in normal mode: the list owns the keyboard, so j/k are
        navigation rather than text.  Insert mode ("/") focuses the search
        field, and the reader focuses its body for the same reason.
        """
        if self._reader_visible() and self.reader is not None:
            self.reader.focus_default()
        elif self.config.behavior.vim_mode:
            self.search.set_can_focus(False)
            self.listbox.grab_focus()
        else:
            self.search.set_can_focus(True)
            self.search.grab_focus()

    def _enter_insert_mode(self) -> None:
        """Make the search field focusable again and put the caret in it."""
        self.search.set_can_focus(True)
        self.search.grab_focus()

    def _on_search_clicked(
        self, gesture: Gtk.GestureClick, presses: int, x: float, y: float
    ) -> None:
        if self.config.behavior.vim_mode:
            self._enter_insert_mode()

    def _text_entry_focused(self) -> bool:
        """True while the user is typing into a search field (insert mode).

        A ``Gtk.SearchEntry`` delegates focus to its inner ``GtkText``, so
        comparing the focus widget to the entry itself is not enough: check
        whether the focus lives inside either search field.
        """
        focus = self.get_focus()
        if focus is None:
            return False
        entries = [self.search]
        if self.reader is not None:
            entries.append(self.reader.search)
        return any(focus is entry or focus.is_ancestor(entry) for entry in entries)

    def _on_map(self, window: Gtk.Window) -> None:
        """Normal mode wins whenever the popup becomes visible."""
        if not self.config.behavior.vim_mode:
            return
        self.focus_default()
        # Belt and braces: run again once GTK's own map-time focus pass is done.
        GLib.idle_add(self._focus_after_map)

    def _focus_after_map(self) -> bool:
        if self.config.behavior.vim_mode:
            self.focus_default()
        return False

    def _on_key_pressed(
        self, controller: Gtk.EventControllerKey, keyval: int, keycode: int, state: Gdk.ModifierType
    ) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        vim = self.config.behavior.vim_mode
        name = Gdk.keyval_name(keyval) or ""

        # Escape closes the popup from anywhere -- except that it also has to
        # leave the ":" prompt and insert mode first.
        if keyval == Gdk.KEY_Escape:
            self._pending_g = False
            if self._command.active:
                self._end_command()
                return True
            if vim and self._text_entry_focused():
                self.focus_default()
                return True
            self.close()
            return True

        if self._command.active:
            return self._command_key(keyval)

        # Any key consumes a pending "g", whether or not it forms "gg".
        pending_g = self._pending_g
        self._pending_g = False

        reader = self.reader
        if reader is not None and self._reader_visible():
            # Backspace is "back", but only once the in-page search is empty;
            # otherwise it has to keep deleting characters.
            if keyval == Gdk.KEY_BackSpace and not reader.search.get_text():
                self._close_reader()
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                reader.next_match(backwards=bool(state & Gdk.ModifierType.SHIFT_MASK))
                return True
            if ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
                reader.next_match()
                return True
            if ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_P):
                reader.next_match(backwards=True)
                return True
            if vim and not self._text_entry_focused():
                return self._vim_normal_key(name, ctrl, pending_g, reader)
            return False

        if vim and not self._text_entry_focused() and self._vim_normal_key(name, ctrl, pending_g):
            return True

        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) or (ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_P)):
            self._select(self._selected - 1)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down) or (ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N)):
            self._select(self._selected + 1)
            return True
        if keyval == Gdk.KEY_Page_Up:
            self._select(self._selected - HALF_PAGE_ROWS)
            return True
        if keyval == Gdk.KEY_Page_Down:
            self._select(self._selected + HALF_PAGE_ROWS)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._activate()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab, Gdk.KEY_ISO_Left_Tab):
            backwards = keyval == Gdk.KEY_ISO_Left_Tab
            self._select(self._selected + (-1 if backwards else 1))
            return True
        return False

    def _vim_normal_key(
        self, name: str, ctrl: bool, pending_g: bool, reader: ReaderView | None = None
    ) -> bool:
        """Handle one normal-mode key; ``False`` means GTK should see it.

        ``reader`` is set only when the reader is the visible page, so j/k
        scroll the document instead of moving the hidden list selection.
        """
        if name == "g" and not ctrl:
            if pending_g:
                if reader is not None:
                    reader.scroll_home()
                else:
                    self._select(0)
            else:
                self._pending_g = True
            return True

        action = vimnav.intent(name, ctrl=ctrl)
        if not action:
            return False
        if action == "down":
            if reader is not None:
                reader.scroll_lines(1)
            else:
                self._select(self._selected + 1)
        elif action == "up":
            if reader is not None:
                reader.scroll_lines(-1)
            else:
                self._select(self._selected - 1)
        elif action == "left":
            if reader is not None:
                reader.scroll_columns(-4)
        elif action == "right":
            if reader is not None:
                reader.scroll_columns(4)
            else:
                self._activate()
        elif action in ("half_down", "half_up"):
            step = 1 if action == "half_down" else -1
            if reader is not None:
                reader.scroll_pages(0.5 * step)
            else:
                self._select(self._selected + step * HALF_PAGE_ROWS)
        elif action in ("page_down", "page_up"):
            step = 1 if action == "page_down" else -1
            if reader is not None:
                reader.scroll_pages(step)
            else:
                self._select(self._selected + step * HALF_PAGE_ROWS)
        elif action == "bottom":
            if reader is not None:
                reader.scroll_end()
            else:
                self._select(len(self._rows_data) - 1)
        elif action == "search":
            if reader is not None:
                reader.focus_search()
            else:
                self._enter_insert_mode()
        elif action == "command":
            self._start_command()
        else:  # pragma: no cover - every intent above is handled
            return False
        return True

    # -- ":" prompt --------------------------------------------------------

    def _start_command(self) -> None:
        self._command.start()
        self._render_command()
        self._command_label.set_visible(True)
        self._hint.set_visible(False)

    def _render_command(self) -> None:
        self._command_label.set_text(f"{self._command.prompt}▏")

    def _end_command(self) -> None:
        self._command.cancel()
        self._command_label.set_visible(False)
        self._hint.set_visible(True)

    def _command_key(self, keyval: int) -> bool:
        """Feed one key to the ":" prompt; every key belongs to it."""
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            action = self._command.execute()
            self._end_command()
            if action == "quit":
                self.close()
        elif keyval == Gdk.KEY_BackSpace:
            if self._command.backspace():
                self._render_command()
            else:
                self._end_command()
        else:
            char = Gdk.keyval_to_unicode(keyval)
            if char:
                self._command.feed(chr(char))
                self._render_command()
        return True
