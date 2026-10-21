"""A small, dependency-free fuzzy matcher.

The matcher is deliberately token based: a query is split on whitespace and
*every* token has to match somewhere in the haystack.  That is what makes
``delete line`` find ``dd  delete the current line`` even though the words are
not adjacent, while still ranking a literal ``delete line`` first when it
exists.

Scoring rewards, in order of importance:

* literal substrings over scattered subsequences
* matches at the start of a word (``dd`` -> ``delete``)
* consecutive characters
* early matches and short haystacks
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

# Characters that are treated as word separators when looking for boundaries.
SEPARATORS = frozenset(" \t\n/-_.:,;()[]{}<>|\"'`")

_MAX_OCCURRENCES = 64


def _boundary(haystack: str, index: int) -> bool:
    return index == 0 or haystack[index - 1] in SEPARATORS


def _substring_score(haystack: str, token: str, index: int) -> float:
    score = 40.0 + min(len(token), 12) * 2.0
    if _boundary(haystack, index):
        score += 25.0
    score += max(0.0, 20.0 - 0.1 * index)
    return score


def _subsequence_score(haystack: str, token: str) -> float | None:
    """Greedy left-to-right subsequence match, or ``None`` when impossible."""
    total = 0.0
    previous = -2
    cursor = 0
    for char in token:
        index = haystack.find(char, cursor)
        if index < 0:
            return None
        if index == previous + 1:
            total += 14.0
        elif _boundary(haystack, index):
            total += 10.0
        previous = index
        cursor = index + 1
    # Prefer dense matches in short strings.
    return total + 15.0 - 0.05 * (len(haystack) - len(token))


def _token_score(haystack: str, token: str) -> float | None:
    best: float | None = None
    index = haystack.find(token)
    seen = 0
    while index >= 0 and seen < _MAX_OCCURRENCES:
        candidate = _substring_score(haystack, token, index)
        if best is None or candidate > best:
            best = candidate
        index = haystack.find(token, index + 1)
        seen += 1
    if best is not None:
        return best
    # Scattered subsequence matching is only useful for short abbreviations
    # ("dl" -> "delete line"); for real words it produces nonsense matches like
    # "copy" inside "complete ... keyword".
    if len(token) <= 2:
        return _subsequence_score(haystack, token)
    return None


def score(query: str, haystack: str, case_sensitive: bool = False) -> float | None:
    """Score ``haystack`` against ``query``; ``None`` means "no match"."""
    tokens = query.split()
    if not tokens:
        return 0.0
    folded = haystack if case_sensitive else haystack.lower()
    total = 0.0
    for token in tokens:
        token = token if case_sensitive else token.lower()
        token_score = _token_score(folded, token)
        if token_score is None:
            return None
        total += token_score

    # Prefer haystacks the query covers well: "dd -- delete the current line"
    # should beat "S -- delete the whole line and insert" for "delete line",
    # because more of its text is actually the query.
    coverage = sum(len(token) for token in tokens) / max(len(folded), 1)
    total += 30.0 * coverage - 0.02 * len(folded)

    # A literal phrase match beats a pile of separate words.
    phrase = query.strip() if case_sensitive else query.strip().lower()
    index = folded.find(phrase) if len(tokens) > 1 else -1
    if index >= 0:
        total += 60.0 + max(0.0, 20.0 - 0.2 * index)
    return total


def rank(
    query: str,
    items: Iterable[T],
    text_of: Callable[[T], str],
    limit: int | None = None,
    case_sensitive: bool = False,
) -> list[T]:
    """Return the items matching ``query``, best first.

    Equally scored items keep their incoming order, so callers stay in control
    of the tie-break (alphabetical for man pages, curated order for keybinds).
    """
    query = query.strip()
    if not query:
        results = list(items)
        return results[:limit] if limit else results

    scored: list[tuple[float, int, T]] = []
    for position, item in enumerate(items):
        value = score(query, text_of(item), case_sensitive)
        if value is not None:
            scored.append((value, position, item))

    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    ranked = [entry[2] for entry in scored]
    return ranked[:limit] if limit else ranked


@dataclass(frozen=True)
class Span:
    """Half-open character range inside a haystack."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


def highlight_spans(query: str, text: str, case_sensitive: bool = False) -> Sequence[Span]:
    """Ranges of ``text`` covered by any query token, for list highlighting."""
    tokens = [token for token in query.split() if token]
    if not tokens:
        return ()
    haystack = text if case_sensitive else text.lower()
    spans: list[Span] = []
    for token in tokens:
        needle = token if case_sensitive else token.lower()
        index = haystack.find(needle)
        while index >= 0:
            spans.append(Span(index, index + len(needle)))
            index = haystack.find(needle, index + len(needle))
    return tuple(sorted(spans, key=lambda span: span.start))
