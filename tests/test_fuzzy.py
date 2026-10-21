"""Fuzzy matcher behaviour, including the example from the README."""

from __future__ import annotations

import unittest

from suprman import fuzzy

BINDS = [
    ("dd", "delete the current line"),
    ("D", "delete from the cursor to the end of the line"),
    ("yy", "yank the current line"),
    ("gg", "jump to the first line of the file"),
    ("x", "delete the character under the cursor"),
]


def keys_of(results: list[tuple[str, str]]) -> list[str]:
    return [keys for keys, _ in results]


def score_of(query: str, haystack: str, case_sensitive: bool = False) -> float:
    value = fuzzy.score(query, haystack, case_sensitive)
    assert value is not None, f"{query!r} unexpectedly did not match {haystack!r}"
    return value


class ScoreTest(unittest.TestCase):
    def test_literal_substring_beats_scattered_match(self) -> None:
        literal = score_of("ab", "xx ab")
        scattered = score_of("ab", "a" + "x" * 12 + "b")
        self.assertGreater(literal, scattered)

    def test_word_start_is_rewarded(self) -> None:
        at_word_start = score_of("del", "dd  delete line")
        mid_word = score_of("del", "aa  model line")
        self.assertGreater(at_word_start, mid_word)

    def test_phrase_match_is_rewarded(self) -> None:
        phrase = score_of("delete line", "dd  delete the current line  delete line")
        words = score_of("delete line", "dd  delete the current line")
        self.assertGreater(phrase, words)

    def test_short_tokens_may_match_as_a_subsequence(self) -> None:
        self.assertIsNotNone(fuzzy.score("dl", "dd  delete the current line"))

    def test_long_words_do_not_match_scattered_characters(self) -> None:
        self.assertIsNone(fuzzy.score("copy", "complete the next keyword match"))

    def test_no_match_returns_none(self) -> None:
        self.assertIsNone(fuzzy.score("zebra", "yy  yank the current line"))

    def test_empty_query_matches_everything(self) -> None:
        self.assertEqual(fuzzy.score("", "anything"), 0.0)

    def test_case_sensitivity(self) -> None:
        # Case-insensitive by default, exact when asked.
        self.assertIsNotNone(fuzzy.score("DELETE", "dd  delete line"))
        self.assertIsNone(fuzzy.score("DELETE", "dd  delete line", case_sensitive=True))
        self.assertIsNotNone(fuzzy.score("Delete", "Delete line", case_sensitive=True))


class RankTest(unittest.TestCase):
    def test_delete_line_finds_dd(self) -> None:
        results = fuzzy.rank("delete line", BINDS, lambda bind: f"{bind[0]}  {bind[1]}")
        self.assertTrue(results)
        self.assertEqual(results[0][0], "dd")

    def test_all_tokens_must_match(self) -> None:
        results = fuzzy.rank("delete zebra", BINDS, lambda bind: f"{bind[0]}  {bind[1]}")
        self.assertEqual(results, [])

    def test_empty_query_preserves_order(self) -> None:
        results = fuzzy.rank("  ", BINDS, lambda bind: f"{bind[0]}  {bind[1]}")
        self.assertEqual(keys_of(results), [bind[0] for bind in BINDS])

    def test_limit_is_respected(self) -> None:
        results = fuzzy.rank("line", BINDS, lambda bind: f"{bind[0]}  {bind[1]}", limit=2)
        self.assertEqual(len(results), 2)

    def test_ranking_prefers_shorter_haystacks(self) -> None:
        items = [("long", "line " + "padding " * 10), ("short", "line")]
        results = fuzzy.rank("line", items, lambda item: f"{item[0]} {item[1]}")
        self.assertEqual(results[0][0], "short")


class HighlightTest(unittest.TestCase):
    def test_spans_cover_each_token(self) -> None:
        spans = fuzzy.highlight_spans("delete line", "delete the current line")
        self.assertEqual([(span.start, span.end) for span in spans], [(0, 6), (19, 23)])

    def test_empty_query_has_no_spans(self) -> None:
        self.assertEqual(fuzzy.highlight_spans("", "anything"), ())


if __name__ == "__main__":
    unittest.main()
