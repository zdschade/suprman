"""apropos parsing, manpath fallback, rendering and the index cache."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suprman import manpages

APROPOS_OUTPUT = """\
ls (1)               - list directory contents
git-add (1)          - Add file contents to the index
bash, rbash (1)      - GNU Bourne-Again SHell
ldap.conf (5)        - LDAP configuration file/environment variables
not a page line at all
  continued line that should be ignored
perror (3)           - system error messages
"""


class ParseAproposTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pages = manpages.parse_apropos(APROPOS_OUTPUT)
        self.by_title = {page.title: page for page in self.pages}

    def test_parses_every_page(self) -> None:
        self.assertEqual(len(self.pages), 6)

    def test_names_sections_and_descriptions(self) -> None:
        page = self.by_title["git-add(1)"]
        self.assertEqual(page.name, "git-add")
        self.assertEqual(page.section, "1")
        self.assertEqual(page.description, "Add file contents to the index")

    def test_comma_separated_names_become_separate_pages(self) -> None:
        self.assertIn("bash(1)", self.by_title)
        self.assertIn("rbash(1)", self.by_title)

    def test_ignores_noise(self) -> None:
        titles = set(self.by_title)
        self.assertFalse(any("not a page" in title for title in titles))
        self.assertFalse(any("continued" in title for title in titles))

    def test_results_are_sorted(self) -> None:
        names = [page.name for page in self.pages]
        self.assertEqual(names, sorted(names, key=str.lower))

    def test_empty_output(self) -> None:
        self.assertEqual(manpages.parse_apropos(""), [])

    def test_haystack_includes_title_and_description(self) -> None:
        page = self.by_title["ls(1)"]
        self.assertIn("ls(1)", page.haystack)
        self.assertIn("directory contents", page.haystack)


class ManpathFallbackTest(unittest.TestCase):
    def test_files_are_discovered(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "man1").mkdir()
            (root / "man5").mkdir()
            (root / "man1" / "foo.1.gz").write_bytes(b"")
            (root / "man5" / "bar.conf.5.gz").write_bytes(b"")
            pages = manpages._parse_manpath_output(str(root))
        titles = {page.title for page in pages}
        self.assertEqual(titles, {"foo(1)", "bar.conf(5)"})

    def test_missing_directories_are_skipped(self) -> None:
        self.assertEqual(manpages._parse_manpath_output("/nonexistent/man"), [])


class CacheTest(unittest.TestCase):
    PAGES = [
        manpages.ManPage("ls", "1", "list directory contents"),
        manpages.ManPage("grep", "1", "print lines matching a pattern"),
    ]

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)

    def test_round_trip(self) -> None:
        manpages._write_cache(self.cache_dir / manpages.CACHE_FILENAME, self.PAGES)
        cached = manpages._read_cache(self.cache_dir / manpages.CACHE_FILENAME)
        self.assertEqual(cached, self.PAGES)

    def test_stale_cache_is_ignored(self) -> None:
        cache_file = self.cache_dir / manpages.CACHE_FILENAME
        manpages._write_cache(cache_file, self.PAGES)
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        payload["built"] = time.time() - 2 * manpages.CACHE_TTL_SECONDS
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(manpages._read_cache(cache_file))

    def test_fresh_cache_is_used(self) -> None:
        cache_file = self.cache_dir / manpages.CACHE_FILENAME
        manpages._write_cache(cache_file, self.PAGES)
        self.assertEqual(manpages._read_cache(cache_file), self.PAGES)

    def test_corrupt_cache_is_ignored(self) -> None:
        cache_file = self.cache_dir / manpages.CACHE_FILENAME
        cache_file.write_text("{not json", encoding="utf-8")
        self.assertIsNone(manpages._read_cache(cache_file))

    def test_missing_cache_is_ignored(self) -> None:
        self.assertIsNone(manpages._read_cache(self.cache_dir / "absent.json"))

    def test_write_cache_creates_the_file(self) -> None:
        target = self.cache_dir / "readonly.json"
        target.parent.mkdir(exist_ok=True)
        manpages._write_cache(target, self.PAGES)  # must not raise
        self.assertTrue(target.exists())


class RenderTest(unittest.TestCase):
    def test_invalid_names_are_rejected(self) -> None:
        with self.assertRaises(manpages.ManError):
            manpages.render_page("ls; rm -rf /")
        with self.assertRaises(manpages.ManError):
            manpages.render_page("-la")

    @unittest.skipUnless(manpages.available(), "man is not installed")
    def test_renders_a_real_page(self) -> None:
        text = manpages.render_page("ls", "1", width=90)
        self.assertIn("NAME", text)
        self.assertIn("ls", text)
        self.assertNotIn("\b", text)
        self.assertGreater(len(text), 500)

    @unittest.skipUnless(manpages.available(), "man is not installed")
    def test_unknown_page_raises(self) -> None:
        with self.assertRaises(manpages.ManError):
            manpages.render_page("definitely-not-a-real-command-xyz")


if __name__ == "__main__":
    unittest.main()
