"""The bundled keybind dataset and its loader."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suprman import fuzzy, vimkeys


class DatasetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.binds = vimkeys.load()
        self.document = json.loads(vimkeys.DEFAULT_DATA_FILE.read_text(encoding="utf-8"))

    def test_dataset_is_installed_and_big_enough(self) -> None:
        self.assertGreaterEqual(len(self.binds), 150)

    def test_every_entry_is_complete(self) -> None:
        for bind in self.binds:
            self.assertTrue(bind.keys, bind)
            self.assertTrue(bind.description, bind)
            self.assertTrue(bind.section_title, bind)

    def test_sections_are_declared(self) -> None:
        declared = {section["id"] for section in self.document["sections"]}
        used = {bind.section for bind in self.binds}
        self.assertEqual(used - declared, set())
        self.assertEqual(declared - used, set(), "a declared section has no entries")

    def test_no_duplicate_keys_within_a_section(self) -> None:
        seen = {(bind.keys, bind.section) for bind in self.binds}
        self.assertEqual(len(seen), len(self.binds))

    def test_core_bindings_are_present(self) -> None:
        keys = {bind.keys for bind in self.binds}
        expected = ("dd", "yy", "p", "gg", "G", "d", "c", "y", "iw", "iW", ":wq", "q{reg}", "@@")
        for key in expected:
            self.assertIn(key, keys)

    def test_haystack_keeps_metadata_out(self) -> None:
        bind = next(item for item in self.binds if item.keys == "dd")
        self.assertIn("delete", bind.haystack)
        self.assertIn("delete line", bind.haystack)
        # Section titles are displayed, not searched: otherwise every entry in
        # "Search and replace" would match the word "replace".
        self.assertNotIn("Operators", bind.haystack)

    def test_delete_line_ranks_dd_first(self) -> None:
        ranked = fuzzy.rank("delete line", self.binds, lambda bind: bind.haystack, limit=5)
        self.assertEqual(ranked[0].keys, "dd")

    def test_synonyms_find_the_right_binding(self) -> None:
        ranked = fuzzy.rank("copy line", self.binds, lambda bind: bind.haystack, limit=3)
        self.assertIn(ranked[0].keys, {"yy", "Y"})

        ranked = fuzzy.rank("search and replace", self.binds, lambda bind: bind.haystack, limit=3)
        self.assertTrue(ranked[0].keys.startswith(":%s"), ranked[0].keys)

        ranked = fuzzy.rank("comment out", self.binds, lambda bind: bind.haystack, limit=3)
        self.assertIn(ranked[0].keys, {"gcc", "gc{motion}"})

    def test_text_object_entries_are_searchable(self) -> None:
        ranked = fuzzy.rank("inside quotes", self.binds, lambda bind: bind.haystack, limit=5)
        self.assertIn('i"', [bind.keys for bind in ranked])


class LoaderTest(unittest.TestCase):
    def test_malformed_entries_are_skipped(self) -> None:
        document = {
            "sections": [{"id": "x", "title": "X"}],
            "keys": [
                {"keys": "a", "section": "x", "description": "valid"},
                {"keys": "b", "section": "x"},
                "not a dict",
            ],
        }
        binds = vimkeys.parse(document, source="<test>")
        self.assertEqual([bind.keys for bind in binds], ["a"])
        self.assertEqual(binds[0].section_title, "X")

    def test_missing_file_falls_back_to_bundled_data(self) -> None:
        with TemporaryDirectory() as directory:
            binds = vimkeys.load(Path(directory) / "missing.json")
        self.assertGreaterEqual(len(binds), 150)

    def test_custom_file_is_used(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mine.json"
            path.write_text(
                json.dumps(
                    {
                        "sections": [{"id": "mine", "title": "My maps"}],
                        "keys": [
                            {"keys": "<leader>f", "section": "mine", "description": "find files"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            binds = vimkeys.load(path)
        self.assertEqual(len(binds), 1)
        self.assertEqual(binds[0].section_title, "My maps")


if __name__ == "__main__":
    unittest.main()
