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

from . import textsearch
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
        hint = Gtk.Label(
            label="Enter next  ·  Shift+Enter previous  ·  ⌫ back  ·  Esc quit", xalign=1.0
        )
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
        """Called once loading is done; moves focus to the search field."""
        if message:
            self._count.set_text(message)
        self.focus_search()

    def focus_search(self) -> None:
        self.search.grab_focus()

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
