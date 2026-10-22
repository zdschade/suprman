"""The page reader: a man/tldr view with a search field that stays put.

Unlike ``man``, the page keeps its search box.  The field filters the rendered
document in place, every hit is highlighted, and Enter/Ctrl-n walks through
them -- which is the whole point of reading a man page inside suprman.

Content is assembled from named *parts* (``tldr``, ``man``, ``status``) that can
arrive in any order, because the tldr page is fetched over the network while the
man page is rendered locally.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

from . import textsearch, vimnav
from .config import Config, hex_to_rgba
from .textsearch import Span

Block = tuple[str, str]

PART_STATUS = "status"
PART_TLDR = "tldr"
PART_MAN = "man"


@dataclass
class Part:
    """One labelled chunk of the document."""

    label: str
    order: int
    blocks: list[Block] = field(default_factory=list)


class ReaderView(Gtk.Box):
    """Scrollable document with an always-visible search field."""

    def __init__(self, config: Config) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.config = config
        self._parts: dict[str, Part] = {}
        self._spans: list[Span] = []
        self._current = -1

        self._build()
        self._create_tags()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self._title = Gtk.Label(xalign=0.0)
        self._title.add_css_class("reader-title")
        self._subtitle = Gtk.Label(xalign=0.0)
        self._subtitle.add_css_class("reader-subtitle")
        header.append(self._title)
        header.append(self._subtitle)
        self.append(header)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("search in page")
        self.search.add_css_class("reader-search")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._on_search_changed)
        self.append(self.search)

        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._buffer = Gtk.TextBuffer()
        self._view = Gtk.TextView(buffer=self._buffer)
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)
        self._view.set_monospace(True)
        # man pages are rendered at `man_width` columns; wrapping them again at
        # a different width is what turns them into ragged, hard-to-read lines.
        # Long lines scroll sideways instead.
        self._view.set_wrap_mode(Gtk.WrapMode.NONE)
        self._view.set_left_margin(2)
        self._view.set_right_margin(6)
        self._view.set_top_margin(2)
        self._view.set_bottom_margin(6)
        self._view.add_css_class("reader-body")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self._view)
        self._scroller = scroller
        self.append(scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._count = Gtk.Label(xalign=0.0)
        self._count.add_css_class("match-count")
        self._count.set_hexpand(True)
        hint_text = "Enter next  ·  Shift+Enter previous  ·  ⌫ back  ·  Esc quit"
        if self.config.behavior.vim_mode:
            hint_text = "j/k scroll  ·  h/l sideways  ·  / search  ·  ⌫ back  ·  Esc quit"
        hint = Gtk.Label(label=hint_text, xalign=1.0)
        hint.add_css_class("footer")
        footer.append(self._count)
        footer.append(hint)
        self.append(footer)

    def _create_tags(self) -> None:
        appearance = self.config.appearance
        accent = appearance.accent
        muted = appearance.muted
        buffer = self._buffer

        buffer.create_tag("h1", weight=Pango.Weight.BOLD, size=13 * Pango.SCALE, foreground=accent)
        # Part labels ("TLDR · tldr.sh", "MAN PAGE") act as dividers.
        buffer.create_tag(
            "section",
            weight=Pango.Weight.BOLD,
            size=9 * Pango.SCALE,
            foreground=muted,
            pixels_above_lines=14,
            pixels_below_lines=4,
        )
        buffer.create_tag("desc", style=Pango.Style.ITALIC, foreground=muted)
        buffer.create_tag(
            "bullet", weight=Pango.Weight.BOLD, foreground=accent, pixels_above_lines=4
        )
        buffer.create_tag("code", family="Monospace", foreground=appearance.foreground)
        # Man page section headings: accent, bold, with air above them.
        buffer.create_tag(
            "header",
            weight=Pango.Weight.BOLD,
            foreground=accent,
            pixels_above_lines=12,
            pixels_below_lines=3,
        )
        buffer.create_tag("body", foreground=appearance.foreground)

        # Search hits: a soft tag for every match, a solid one for the active hit.
        self._tag_match = buffer.create_tag(
            "match", background=hex_to_rgba(accent, 0.3), foreground=appearance.foreground
        )
        self._tag_current = buffer.create_tag(
            "current", background=accent, foreground=appearance.background
        )

    # -- public API --------------------------------------------------------

    def begin(self, title: str, subtitle: str) -> None:
        """Reset the view for a new document."""
        self._parts = {}
        self._spans = []
        self._current = -1
        self._title.set_text(title)
        self._subtitle.set_text(subtitle)
        self.search.set_text("")
        self._count.set_text("")
        self._buffer.set_text("")

    def set_part(self, name: str, label: str, blocks: Sequence[Block], order: int = 0) -> None:
        """Insert or replace one named section and re-render."""
        self._parts[name] = Part(label=label, order=order, blocks=list(blocks))
        self._render()

    def remove_part(self, name: str) -> None:
        if self._parts.pop(name, None) is not None:
            self._render()

    def add_man_text(self, text: str) -> None:
        """Append a rendered man page, tagging section headings."""
        blocks: list[Block] = []
        for line in text.splitlines():
            stripped = line.rstrip()
            if textsearch.is_header(stripped):
                blocks.append(("header", stripped))
            else:
                blocks.append(("body", stripped) if stripped else ("", ""))
        self.set_part(PART_MAN, "MAN PAGE", blocks, order=20)

    def finish(self, message: str = "") -> None:
        """Called once loading is done.

        In vim mode the keyboard is already where it belongs (body on open,
        filter if the user hit "/" while the page loaded), so a late render
        must not yank it back.
        """
        if message:
            self._count.set_text(message)
        if not self.config.behavior.vim_mode:
            self.focus_search()

    def focus_search(self) -> None:
        """Insert mode: the in-page filter takes the keyboard."""
        self.search.grab_focus()

    def focus_body(self) -> None:
        """Normal mode: the document takes the keyboard, so j/k can scroll it."""
        self._view.grab_focus()

    def focus_default(self) -> None:
        """Focus whatever this configuration expects after a page opens."""
        if self.config.behavior.vim_mode:
            self.focus_body()
        else:
            self.focus_search()

    # -- vim scrolling -----------------------------------------------------

    def scroll_lines(self, lines: float) -> None:
        """Scroll the body by ``lines`` text lines (negative scrolls up)."""
        self._scroll(self._scroller.get_vadjustment(), lines * self._line_height())

    def scroll_columns(self, columns: float) -> None:
        """Scroll sideways by ``columns`` characters (negative scrolls left)."""
        self._scroll(self._scroller.get_hadjustment(), columns * self._column_width())

    def scroll_pages(self, pages: float) -> None:
        """Scroll by whole (or fractional, e.g. 0.5 for Ctrl-d) viewports."""
        adjustment = self._scroller.get_vadjustment()
        self._scroll(adjustment, adjustment.get_page_size() * pages)

    def scroll_home(self) -> None:
        adjustment = self._scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_lower())

    def scroll_end(self) -> None:
        adjustment = self._scroller.get_vadjustment()
        adjustment.set_value(
            vimnav.clamp_scroll(
                adjustment.get_upper(),
                adjustment.get_lower(),
                adjustment.get_upper(),
                adjustment.get_page_size(),
            )
        )

    @staticmethod
    def _scroll(adjustment: Gtk.Adjustment, delta: float) -> None:
        adjustment.set_value(
            vimnav.clamp_scroll(
                adjustment.get_value() + delta,
                adjustment.get_lower(),
                adjustment.get_upper(),
                adjustment.get_page_size(),
            )
        )

    def _line_height(self) -> float:
        """Height of one body line in pixels, so j moves by exactly one line."""
        try:
            _y, height = self._view.get_line_yrange(self._buffer.get_start_iter())
        except (TypeError, ValueError):  # pragma: no cover - binding differences
            return 18.0
        return float(height) if height and height > 0 else 18.0

    def _column_width(self) -> float:
        """Width of one monospace character, for h/l horizontal steps."""
        try:
            width, _height = self._view.create_pango_layout("M").get_pixel_size()
        except (AttributeError, TypeError):  # pragma: no cover - binding differences
            return 8.0
        return float(width) if width > 0 else 8.0

    def clear(self) -> None:
        self.begin("", "")

    @property
    def text(self) -> str:
        start, end = self._buffer.get_bounds()
        return self._buffer.get_text(start, end, False)

    # -- rendering ---------------------------------------------------------

    def _ordered_parts(self) -> list[Part]:
        ordered = sorted(self._parts.items(), key=lambda item: (item[1].order, item[0]))
        return [part for _, part in ordered]

    def _render(self) -> None:
        chunks: list[str] = []
        spans: list[tuple[int, int, str]] = []
        offset = 0

        for index, part in enumerate(self._ordered_parts()):
            if index:
                chunks.append("\n")
                offset += 1
            if part.label:
                spans.append((offset, offset + len(part.label), "section"))
                chunks.append(f"{part.label}\n")
                offset += len(part.label) + 1
            for style, text in part.blocks:
                line = f"{text}\n"
                if style:
                    spans.append((offset, offset + len(text), style))
                chunks.append(line)
                offset += len(line)

        self._buffer.set_text("".join(chunks))
        for start, end, style in spans:
            self._buffer.apply_tag_by_name(
                style,
                self._buffer.get_iter_at_offset(start),
                self._buffer.get_iter_at_offset(end),
            )

        if self.search.get_text().strip():
            self._search()
        else:
            self._spans = []
            self._current = -1
            self._update_count()

    # -- searching ---------------------------------------------------------

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._search()

    def _search(self) -> None:
        self._spans = textsearch.find_all(
            self.text, self.search.get_text(), case_sensitive=self.config.behavior.case_sensitive
        )
        self._current = 0 if self._spans else -1
        if self._spans:
            self._scroll_to_current()
        self._apply_highlights()
        self._update_count()

    def _apply_highlights(self) -> None:
        start, end = self._buffer.get_bounds()
        for tag in (self._tag_match, self._tag_current):
            if tag is not None:
                self._buffer.remove_tag(tag, start, end)

        for index, span in enumerate(self._spans):
            tag = self._tag_current if index == self._current else self._tag_match
            self._buffer.apply_tag(
                tag,
                self._buffer.get_iter_at_offset(span.start),
                self._buffer.get_iter_at_offset(span.end),
            )

    def _update_count(self) -> None:
        if not self.search.get_text().strip():
            lines = self.text.count("\n")
            self._count.set_text(f"{lines} lines")
        elif not self._spans:
            self._count.set_text("no matches")
        else:
            self._count.set_text(f"{self._current + 1} / {len(self._spans)} matches")

    def next_match(self, backwards: bool = False) -> None:
        if not self._spans:
            return
        step = -1 if backwards else 1
        self._current = (self._current + step) % len(self._spans)
        self._apply_highlights()
        self._update_count()
        self._scroll_to_current()

    def _scroll_to_current(self) -> None:
        if not 0 <= self._current < len(self._spans):
            return
        span = self._spans[self._current]
        start_iter = self._buffer.get_iter_at_offset(span.start)
        mark = self._buffer.create_mark(None, start_iter, True)
        self._view.scroll_to_mark(mark, 0.15, False, 0.0, 0.0)
        GLib.idle_add(self._buffer.delete_mark, mark)
