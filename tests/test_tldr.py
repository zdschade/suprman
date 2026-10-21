"""tldr markdown rendering and cache behaviour (no network access)."""

from __future__ import annotations

import os
import shutil
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from suprman import tldr

MARKDOWN = """\
# ls

> List directory contents.
> More information: <https://manned.org/ls>.

- List files one per line:

`ls -1`

- List all files, including hidden files:

`ls -a {{path}}`

<!-- this comment is dropped -->
"""


class FakeResponse:
    """Minimal stand-in for urlopen()'s context manager."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lines = tldr.render(MARKDOWN)

    def test_title(self) -> None:
        self.assertEqual(self.lines[0], (tldr.STYLE_TITLE, "ls"))

    def test_description_lines(self) -> None:
        descriptions = [text for style, text in self.lines if style == tldr.STYLE_DESC]
        self.assertEqual(descriptions[0], "List directory contents.")
        self.assertIn("More information:", descriptions[1])

    def test_bullets_and_code(self) -> None:
        bullets = [text for style, text in self.lines if style == tldr.STYLE_BULLET]
        codes = [text for style, text in self.lines if style == tldr.STYLE_CODE]
        self.assertEqual(bullets[0], "List files one per line:")
        self.assertEqual(codes[0], "ls -1")

    def test_placeholders_are_expanded(self) -> None:
        codes = [text for style, text in self.lines if style == tldr.STYLE_CODE]
        self.assertIn("ls -a <path>", codes)

    def test_comments_are_dropped(self) -> None:
        self.assertFalse(any("this comment" in text for _, text in self.lines))

    def test_trailing_blank_lines_are_trimmed(self) -> None:
        self.assertNotEqual(self.lines[-1][1], "")

    def test_extensive_markdown_is_not_markdown(self) -> None:
        self.assertTrue(tldr.is_markdown(MARKDOWN))
        self.assertFalse(tldr.is_markdown("ls\n  List directory contents\n"))


class PlatformTest(unittest.TestCase):
    def test_auto_includes_common(self) -> None:
        platforms = tldr.candidate_platforms("auto")
        self.assertEqual(platforms[-1], "common")
        self.assertIn(platforms[0], {"linux", "osx", "windows", "freebsd", "openbsd", "netbsd"})

    def test_explicit_platform(self) -> None:
        self.assertEqual(tldr.candidate_platforms("osx"), ("osx", "common"))


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name) / "tldr"
        self.client = tldr.Tldr(self.cache_dir, ttl_days=7)

    def _write(self, name: str, text: str = "# ls\n\n> List files.\n") -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_fresh_cache_is_used_without_network(self) -> None:
        self._write("ls")
        with mock.patch.object(tldr.Tldr, "_download", side_effect=AssertionError("network")):
            self.assertIn("List files", self.client.fetch("ls") or "")

    def test_stale_cache_triggers_a_refetch(self) -> None:
        path = self._write("ls")
        old = time.time() - 30 * 24 * 60 * 60
        os.utime(path, (old, old))
        with (
            mock.patch.object(tldr.Tldr, "_from_client", return_value=None),
            mock.patch.object(tldr.Tldr, "_download", return_value=None),
        ):
            self.assertIsNone(self.client.fetch("ls"))

    def test_download_result_is_cached(self) -> None:
        body = b"# grep\n\n> Search for a pattern.\n"
        with (
            mock.patch.object(tldr.Tldr, "_from_client", return_value=None),
            mock.patch.object(
                tldr.urllib.request, "urlopen", return_value=FakeResponse(body)
            ) as urlopen,
        ):
            markdown = self.client.fetch("grep")

        self.assertIn("Search for a pattern", markdown or "")
        cached = self.cache_dir / "grep.md"
        self.assertTrue(cached.is_file())
        self.assertIn("Search for a pattern", cached.read_text(encoding="utf-8"))
        self.assertIn("/pages/", urlopen.call_args[0][0].full_url)

    def test_download_falls_back_across_platforms(self) -> None:
        body = b"# grep\n"

        def fake_urlopen(request, timeout=0):
            if "/common/" in request.full_url:
                return FakeResponse(body)
            raise tldr.urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

        with (
            mock.patch.object(tldr.Tldr, "_from_client", return_value=None),
            mock.patch.object(tldr.urllib.request, "urlopen", side_effect=fake_urlopen) as urlopen,
        ):
            markdown = self.client.fetch("grep")

        self.assertIn("grep", markdown or "")
        urls = [call[0][0].full_url for call in urlopen.call_args_list]
        self.assertIn("/common/", urls[-1])
        self.assertEqual(urlopen.call_count, len(self.client.platforms))

    def test_memory_cache_avoids_repeat_lookups(self) -> None:
        with mock.patch.object(tldr.Tldr, "_download", return_value="# ls\n") as download:
            self.client.fetch("ls")
            self.client.fetch("ls")
        self.assertEqual(download.call_count, 1)

    def test_invalid_names_are_rejected(self) -> None:
        for name in ("", "../../etc/passwd", "ls --help", "a/b"):
            self.assertIsNone(self.client.fetch(name), name)

    def test_disabled_client_returns_none(self) -> None:
        client = tldr.Tldr(self.cache_dir, enabled=False)
        self.assertIsNone(client.fetch("ls"))

    def test_client_output_is_accepted(self) -> None:
        if not shutil.which("tldr"):
            self.skipTest("no tldr client installed")
        self.assertIsNotNone(self.client.fetch("ls"))


class PlaceholderTest(unittest.TestCase):
    def test_expand(self) -> None:
        self.assertEqual(tldr.expand_placeholders("cp {{file}} {{dest}}"), "cp <file> <dest>")
        self.assertEqual(tldr.expand_placeholders("no placeholders"), "no placeholders")


if __name__ == "__main__":
    unittest.main()
