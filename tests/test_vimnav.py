"""Vim navigation: key intents, the ':' prompt and scroll clamping."""

from __future__ import annotations

import unittest

from suprman import vimnav


class IntentTest(unittest.TestCase):
    def test_movement_letters(self) -> None:
        self.assertEqual(vimnav.intent("j"), "down")
        self.assertEqual(vimnav.intent("k"), "up")
        self.assertEqual(vimnav.intent("h"), "left")
        self.assertEqual(vimnav.intent("l"), "right")

    def test_named_keys(self) -> None:
        self.assertEqual(vimnav.intent("slash"), "search")
        self.assertEqual(vimnav.intent("colon"), "command")
        self.assertEqual(vimnav.intent("G"), "bottom")

    def test_ctrl_keys(self) -> None:
        self.assertEqual(vimnav.intent("d", ctrl=True), "half_down")
        self.assertEqual(vimnav.intent("u", ctrl=True), "half_up")
        self.assertEqual(vimnav.intent("f", ctrl=True), "page_down")
        self.assertEqual(vimnav.intent("b", ctrl=True), "page_up")
        # Ctrl-D with the shifted spelling still counts.
        self.assertEqual(vimnav.intent("D", ctrl=True), "half_down")

    def test_plain_letters_are_not_half_pages(self) -> None:
        # Ctrl is the modifier for d/u; plain d must stay free (and typing in
        # insert mode is decided by the window, not here).
        self.assertEqual(vimnav.intent("d"), "")
        self.assertEqual(vimnav.intent("u"), "")

    def test_unrelated_keys_fall_through(self) -> None:
        for name in ("z", "space", "Return", "a", "q"):
            self.assertEqual(vimnav.intent(name), "", name)
        self.assertEqual(vimnav.intent("j", ctrl=True), "")


class ClampScrollTest(unittest.TestCase):
    def test_stays_inside_the_range(self) -> None:
        self.assertEqual(vimnav.clamp_scroll(0, 0, 1000, 100), 0)
        self.assertEqual(vimnav.clamp_scroll(50, 0, 1000, 100), 50)
        self.assertEqual(vimnav.clamp_scroll(99999, 0, 1000, 100), 900)
        self.assertEqual(vimnav.clamp_scroll(-40, 0, 1000, 100), 0)

    def test_short_document_collapses_to_lower(self) -> None:
        # A viewport taller than the content has no scrollable range; the old
        # `upper - page_size` arithmetic would go negative here.
        self.assertEqual(vimnav.clamp_scroll(30, 0, 40, 200), 0)

    def test_offset_range_is_respected(self) -> None:
        self.assertEqual(vimnav.clamp_scroll(-10, 5, 100, 20), 5)


class CommandLineTest(unittest.TestCase):
    def test_starts_empty_and_closed(self) -> None:
        command = vimnav.CommandLine()
        self.assertFalse(command.active)
        self.assertEqual(command.text, "")

    def test_typing_and_executing_quit(self) -> None:
        command = vimnav.CommandLine()
        command.start()
        for char in "q":
            command.feed(char)
        self.assertEqual(command.prompt, ":q")
        self.assertEqual(command.execute(), "quit")

    def test_quit_spellings(self) -> None:
        for typed in ("q", "q!", "quit", "Q", "qa", "qa!"):
            command = vimnav.CommandLine()
            command.start()
            for char in typed:
                command.feed(char)
            self.assertEqual(command.execute(), "quit", typed)

    def test_leading_colon_and_spaces_are_ignored(self) -> None:
        command = vimnav.CommandLine()
        command.start()
        for char in " :q ":
            command.feed(char)
        self.assertEqual(command.execute(), "quit")

    def test_unknown_command_does_nothing(self) -> None:
        command = vimnav.CommandLine()
        command.start()
        for char in "wq":
            command.feed(char)
        self.assertEqual(command.execute(), "")

    def test_backspace_reports_emptiness(self) -> None:
        command = vimnav.CommandLine()
        command.start()
        command.feed("q")
        command.feed("w")
        self.assertTrue(command.backspace())
        self.assertEqual(command.text, "q")
        # Deleting the last character leaves an empty line: the window closes
        # the prompt, which is what False signals.
        self.assertFalse(command.backspace())
        self.assertEqual(command.text, "")

    def test_cancel_clears(self) -> None:
        command = vimnav.CommandLine()
        command.start()
        command.feed("q")
        command.cancel()
        self.assertFalse(command.active)
        self.assertEqual(command.text, "")

    def test_input_ignored_while_closed(self) -> None:
        command = vimnav.CommandLine()
        command.feed("q")
        self.assertEqual(command.text, "")

    def test_length_is_capped(self) -> None:
        command = vimnav.CommandLine()
        command.start()
        for _ in range(vimnav.MAX_COMMAND_LENGTH + 10):
            command.feed("x")
        self.assertEqual(len(command.text), vimnav.MAX_COMMAND_LENGTH)

    def test_non_printable_input_is_ignored(self) -> None:
        command = vimnav.CommandLine()
        command.start()
        command.feed("\n")
        command.feed("\t")
        self.assertEqual(command.text, "")


if __name__ == "__main__":
    unittest.main()
