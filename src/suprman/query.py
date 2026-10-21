"""Parsing of the search bar into a mode plus a search term.

The whole UI is driven by one text field, so the prefix rules live here as a
pure function that is easy to test:

===============  ==========================================================
input            behaviour
===============  ==========================================================
``""``           browse vim keybinds
``delete line``  filter vim keybinds
``man``          list every installed man page
``man grep``     filter man pages, opening ``grep`` when it matches exactly
``tldr grep``    open grep in the reader with the tldr page first
===============  ==========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

PREFIXES = ("man", "tldr")


class Mode(str, Enum):
    """What the main list is currently showing."""

    VIM = "vim"
    MAN = "man"
    TLDR = "tldr"


@dataclass(frozen=True)
class Query:
    """A parsed search bar value."""

    mode: Mode = Mode.VIM
    term: str = ""

    @property
    def bare_prefix(self) -> bool:
        """True when the user typed only ``man`` / ``tldr``."""
        return not self.term.strip()

    @property
    def wants_tldr(self) -> bool:
        """Open the reader with tldr content when available."""
        return self.mode is Mode.TLDR


def parse(text: str) -> Query:
    """Split the raw search bar text into a :class:`Query`."""
    cleaned = text.strip()
    if cleaned.startswith(":"):
        cleaned = cleaned[1:].strip()
    if not cleaned:
        return Query()

    head, _, rest = cleaned.partition(" ")
    head = head.lower()

    if head == "man":
        return Query(Mode.MAN, rest.strip())
    if head == "tldr":
        # "tldr" on its own has nothing to look up yet, so show the page list.
        return Query(Mode.TLDR, rest.strip()) if rest.strip() else Query(Mode.MAN, "")
    return Query(Mode.VIM, cleaned)
