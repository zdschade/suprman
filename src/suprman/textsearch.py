"""Pure helpers for searching inside a rendered man page or tldr page.

Kept free of GTK so the interesting logic can be tested head-lessly: the
reader widget only turns these spans into text buffer tags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: roff writes bold as ``N\x08N`` and underline as ``_\x08N`` (a literal
#: backspace, not a regex word boundary -- an easy and very destructive mixup).
_BOLD = re.compile(r"(.)\x08\1")
_UNDERLINE = re.compile(r"_\x08(.)")

#: ANSI colour/CSI sequences, removed when a pager leaves them in.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

#: A man page section heading: all caps, no leading space, e.g. ``SYNOPSIS``.
_HEADER = re.compile(r"^[A-Z][A-Z0-9 &'/-]{2,}$")

MAX_MATCHES = 4000


@dataclass(frozen=True)
class Span:
    """Half-open character range."""

    start: int
    end: int


def strip_overstrike(text: str) -> str:
    """Turn a formatted man page into plain text.

    ``man`` emits terminal formatting even when piped to ``cat``: bold is
    ``X\\bX`` and underline is ``_\\bX``.  Collapsing those keeps the output
    readable without depending on ``col(1)``.
    """
    plain = _ANSI.sub("", text)
    plain = _BOLD.sub(lambda match: match.group(1), plain)
    plain = _UNDERLINE.sub(lambda match: match.group(1), plain)
    plain = plain.replace("\x08", "")
    return plain.replace("\r\n", "\n").replace("\x1b", "")


def is_header(line: str) -> bool:
    """True for lines that look like a man page section heading."""
    stripped = line.strip()
    return bool(_HEADER.match(stripped)) and len(stripped.split()) <= 6


def find_all(
    text: str,
    query: str,
    case_sensitive: bool = False,
    limit: int = MAX_MATCHES,
) -> list[Span]:
    """Every non-overlapping occurrence of ``query`` in ``text``."""
    needle = query.strip()
    if not needle:
        return []
    haystack = text if case_sensitive else text.lower()
    needle = needle if case_sensitive else needle.lower()

    spans: list[Span] = []
    index = haystack.find(needle)
    while index >= 0 and len(spans) < limit:
        spans.append(Span(index, index + len(needle)))
        index = haystack.find(needle, index + len(needle))
    return spans


def line_bounds(text: str, offset: int) -> tuple[int, int]:
    """Start/end offsets of the line containing ``offset``."""
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end < 0:
        end = len(text)
    return start, end
