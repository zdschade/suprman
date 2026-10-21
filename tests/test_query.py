"""Search bar prefix handling."""

from __future__ import annotations

import unittest

from suprman import query


class ParseTest(unittest.TestCase):
    def test_empty_query_browses_vim_keys(self) -> None:
        self.assertEqual(query.parse(""), query.Query(query.Mode.VIM, ""))
        self.assertEqual(query.parse("   "), query.Query(query.Mode.VIM, ""))

    def test_plain_text_filters_vim_keys(self) -> None:
        parsed = query.parse("delete line")
        self.assertIs(parsed.mode, query.Mode.VIM)
        self.assertEqual(parsed.term, "delete line")

    def test_bare_man_lists_pages(self) -> None:
        parsed = query.parse("man")
        self.assertIs(parsed.mode, query.Mode.MAN)
        self.assertTrue(parsed.bare_prefix)

    def test_man_with_term(self) -> None:
        parsed = query.parse("man grep")
        self.assertIs(parsed.mode, query.Mode.MAN)
        self.assertEqual(parsed.term, "grep")
        self.assertFalse(parsed.bare_prefix)

    def test_tldr_with_term_wants_tldr(self) -> None:
        parsed = query.parse("tldr grep")
        self.assertIs(parsed.mode, query.Mode.TLDR)
        self.assertTrue(parsed.wants_tldr)
        self.assertEqual(parsed.term, "grep")

    def test_bare_tldr_lists_pages(self) -> None:
        parsed = query.parse("tldr")
        self.assertIs(parsed.mode, query.Mode.MAN)
        self.assertFalse(parsed.wants_tldr)

    def test_prefix_is_case_insensitive(self) -> None:
        self.assertIs(query.parse("MAN ls").mode, query.Mode.MAN)
        self.assertIs(query.parse("Tldr ls").mode, query.Mode.TLDR)

    def test_words_starting_with_man_are_not_prefixes(self) -> None:
        parsed = query.parse("manual")
        self.assertIs(parsed.mode, query.Mode.VIM)
        self.assertEqual(parsed.term, "manual")

    def test_extra_whitespace_is_trimmed(self) -> None:
        self.assertEqual(query.parse("  man   ls  ").term, "ls")

    def test_leading_colon_is_ignored(self) -> None:
        self.assertEqual(query.parse(":man ls").term, "ls")


if __name__ == "__main__":
    unittest.main()
