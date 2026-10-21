"""Man page text cleanup and in-page search."""

from __future__ import annotations

import unittest

from suprman import textsearch

RAW_MAN = (
    "N\bNA\bAM\bME\b\n"
    "       ls - list directory contents\n"
    "\n"
    "S\bSY\bYN\bNO\bOP\bPS\bSI\bIS\bS\n"
    "       _\bl_\bs [\b[O\bOP\bPT\bTI\bIO\bON\bN]...\n"
)


class StripOverstrikeTest(unittest.TestCase):
    def test_bold_and_underline_are_collapsed(self) -> None:
        self.assertEqual(textsearch.strip_overstrike("N\bNA\bAM\bME\b"), "NAME")
        self.assertEqual(textsearch.strip_overstrike("_\bl_\bs"), "ls")

    def test_real_man_output_is_readable(self) -> None:
        text = textsearch.strip_overstrike(RAW_MAN)
        self.assertIn("NAME", text)
        self.assertIn("ls - list directory contents", text)
        self.assertIn("ls [OPTION]...", text)
        self.assertNotIn("\b", text)

    def test_ansi_escapes_are_removed(self) -> None:
        self.assertEqual(textsearch.strip_overstrike("\x1b[1mNAME\x1b[0m"), "NAME")


class HeaderTest(unittest.TestCase):
    def test_section_headings(self) -> None:
        for line in ("NAME", "SYNOPSIS", "EXIT STATUS", "SEE ALSO"):
            self.assertTrue(textsearch.is_header(line), line)

    def test_indented_headings_are_still_headings(self) -> None:
        self.assertTrue(textsearch.is_header("   OPTIONS"))

    def test_prose_is_not_a_heading(self) -> None:
        self.assertFalse(textsearch.is_header("ls - list directory contents"))
        self.assertFalse(textsearch.is_header("Name"))
        self.assertFalse(textsearch.is_header(""))

    def test_long_caps_line_is_not_a_heading(self) -> None:
        self.assertFalse(textsearch.is_header("THIS IS A VERY LONG LINE OF CAPS TEXT"))


class FindAllTest(unittest.TestCase):
    TEXT = "delete the line\nthen delete it again\n"

    def test_all_occurrences(self) -> None:
        spans = textsearch.find_all(self.TEXT, "delete")
        self.assertEqual([(span.start, span.end) for span in spans], [(0, 6), (21, 27)])

    def test_empty_query_finds_nothing(self) -> None:
        self.assertEqual(textsearch.find_all(self.TEXT, "  "), [])

    def test_case_insensitive_by_default(self) -> None:
        self.assertEqual(len(textsearch.find_all(self.TEXT, "DELETE")), 2)
        self.assertEqual(textsearch.find_all(self.TEXT, "DELETE", case_sensitive=True), [])

    def test_matches_are_non_overlapping(self) -> None:
        spans = textsearch.find_all("aaaa", "aa")
        self.assertEqual(len(spans), 2)

    def test_limit_is_respected(self) -> None:
        self.assertEqual(len(textsearch.find_all("a a a a a", "a", limit=3)), 3)

    def test_offsets_index_the_original_text(self) -> None:
        spans = textsearch.find_all(self.TEXT, "again")
        span = spans[0]
        self.assertEqual(self.TEXT[span.start : span.end], "again")


class LineBoundsTest(unittest.TestCase):
    def test_middle_of_line(self) -> None:
        text = "first\nsecond\nthird"
        self.assertEqual(textsearch.line_bounds(text, 8), (6, 12))

    def test_last_line_without_newline(self) -> None:
        text = "first\nsecond\nthird"
        self.assertEqual(textsearch.line_bounds(text, 16), (13, 18))


if __name__ == "__main__":
    unittest.main()
