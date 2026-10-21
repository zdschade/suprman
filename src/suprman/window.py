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
from gi.repository import Gdk, GLib, Gtk, Pango

from . import fuzzy, layershell, manpages, query, tldr, vimkeys
from .config import Config
from .reader import PART_MAN, PART_STATUS, PART_TLDR, Block, ReaderView

PAGE_LIST = "list"
PAGE_READER = "reader"

PLACEHOLDER = "search vim keybinds — try “delete line”, or type “man ls”"
CHIP_VIM = "VIM KEYS"
CHIP_MAN = "MAN PAGES"
CHIP_TLDR = "TLDR"
CHIP_READER = "READER"

HINT = "↑↓ move · Enter open · Esc clear or quit · Tab next · “man ls” opens a page"


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
        self._selected = -1
        self._generation = 0
        self._auto_opened_for = ""

        self.set_title("suprman")
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("suprman")
        self.set_default_size(config.appearance.width, config.appearance.height)

        self._build()
        self._install_controllers()
        layershell.attach(self, namespace="suprman", mode=config.behavior.layer_shell)

        self.show_vim("")

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
        self.stack.add_named(self.scroller, PAGE_LIST)

        self.reader = ReaderView(self.config)
        self.stack.add_named(self.reader, PAGE_READER)

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
        self._hint = Gtk.Label(label=HINT, xalign=1.0)
        self._hint.add_css_class("footer")
        footer.append(self._count)
        footer.append(self._hint)
        surface.append(footer)

    def _install_controllers(self) -> None:
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

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
        self._pages_loading = False
        self._pages = pages
        self._pages_error = error
        self._refresh(self.search.get_text(), allow_auto=True)
        return False

    # -- reader ------------------------------------------------------------

    def open_page(self, page: manpages.ManPage) -> None:
        """Show a man page (with its tldr page above it) in the reader."""
        self._generation += 1
        generation = self._generation
        subtitle = f"man {page.section}"
        if page.description:
            subtitle = f"{subtitle} · {page.description}"
        self.reader.begin(page.title, subtitle)
        self.reader.set_part(PART_STATUS, "", [("desc", "loading…")], order=0)
        self._set_chip(CHIP_READER)
        self.stack.set_visible_child_name(PAGE_READER)
        self.reader.focus_search()

        if self.config.behavior.tldr:
            threading.Thread(target=self._worker_tldr, args=(generation, page), daemon=True).start()
        threading.Thread(target=self._worker_man, args=(generation, page), daemon=True).start()

    def _worker_tldr(self, generation: int, page: manpages.ManPage) -> None:
        markdown = self._tldr.fetch(page.name)
        blocks: list[Block] = tldr.render(markdown) if markdown else []
        GLib.idle_add(self._show_tldr, generation, blocks)

    def _show_tldr(self, generation: int, blocks: list[Block]) -> bool:
        if generation != self._generation:
            return False
        if blocks:
            self.reader.set_part(PART_TLDR, "TLDR · tldr.sh", blocks, order=10)
        else:
            self.reader.set_part(
                PART_TLDR,
                "TLDR · tldr.sh",
                [("desc", "no tldr page for this command (yet)")],
                order=10,
            )
        self.reader.remove_part(PART_STATUS)
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
        if generation != self._generation:
            return False
        if error:
            self.reader.set_part(PART_MAN, "MAN PAGE", [("desc", error)], order=20)
        else:
            self.reader.add_man_text(text)
        self.reader.remove_part(PART_STATUS)
        self.reader.finish()
        return False

    def _close_reader(self) -> None:
        self._generation += 1  # invalidate in-flight loads
        self.stack.set_visible_child_name(PAGE_LIST)
        self.search.grab_focus()
        self._refresh(self.search.get_text(), allow_auto=False)

    def _reader_visible(self) -> bool:
        return self.stack.get_visible_child_name() == PAGE_READER

    # -- rows --------------------------------------------------------------

    def _set_rows(self, rows: list[RowData], empty_message: str, total: int | None = None) -> None:
        self._rows_data = rows

        while len(self._row_widgets) < len(rows):
            widget = ResultRow()
            self.listbox.insert(widget, len(self._row_widgets))
            self._row_widgets.append(widget)

        for index, widget in enumerate(self._row_widgets):
            if index < len(rows):
                widget.bind(rows[index])
                widget.set_visible(True)
            else:
                widget.set_visible(False)

        has_rows = bool(rows)
        self._empty_label.set_text(empty_message)
        self._empty_row.set_visible(not has_rows)

        if has_rows:
            self._select(0)
        else:
            self._selected = -1
            self.listbox.select_row(None)
        self._update_count(len(rows), total)

    def _select(self, index: int) -> None:
        if not self._rows_data:
            return
        index = max(0, min(len(self._rows_data) - 1, index))
        self._selected = index
        widget = self._row_widgets[index]
        self.listbox.select_row(widget)
        self._ensure_visible(widget)
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

    def _on_key_pressed(
        self, controller: Gtk.EventControllerKey, keyval: int, keycode: int, state: Gdk.ModifierType
    ) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if keyval == Gdk.KEY_Escape:
            self._go_back()
            return True

        if self._reader_visible():
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self.reader.next_match(backwards=bool(state & Gdk.ModifierType.SHIFT_MASK))
                return True
            if ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self.reader.next_match()
                return True
            if ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_P):
                self.reader.next_match(backwards=True)
                return True
            return False

        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) or (ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_P)):
            self._select(self._selected - 1)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down) or (ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N)):
            self._select(self._selected + 1)
            return True
        if keyval == Gdk.KEY_Page_Up:
            self._select(self._selected - 10)
            return True
        if keyval == Gdk.KEY_Page_Down:
            self._select(self._selected + 10)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._activate()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab, Gdk.KEY_ISO_Left_Tab):
            backwards = keyval == Gdk.KEY_ISO_Left_Tab
            self._select(self._selected + (-1 if backwards else 1))
            return True
        return False

    def _go_back(self) -> None:
        if self._reader_visible():
            self._close_reader()
            return
        if self.search.get_text():
            self.search.set_text("")
            self._auto_opened_for = ""
            self._refresh("")
            return
        self.close()
