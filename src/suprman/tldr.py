"""tldr page lookup with an offline-friendly cache.

Resolution order, cheapest first:

1. an in-process memory cache
2. a fresh file in ``$XDG_CACHE_HOME/suprman/tldr``
3. a local ``tldr`` client, if one is installed (preferring ``--raw`` so we get
   markdown back)
4. one HTTP request per candidate platform against the tldr-pages repository

Downloads are cached as markdown, which also means a machine that goes offline
keeps working with whatever it has already seen.  All methods block; the UI
calls them from a worker thread.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAW_URL = "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages/{platform}/{name}.md"
USER_AGENT = "suprman/0.1 (+https://github.com/tldr-pages/tldr)"

_SAFE_NAME = re.compile(r"^[\w.+:@-]+$")
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")

PLATFORM_BY_SYSTEM = {
    "linux": "linux",
    "darwin": "osx",
    "win32": "windows",
    "freebsd": "freebsd",
    "openbsd": "openbsd",
    "netbsd": "netbsd",
}

STYLE_TITLE = "title"
STYLE_DESC = "desc"
STYLE_BULLET = "bullet"
STYLE_CODE = "code"
STYLE_TEXT = "text"


def candidate_platforms(setting: str = "auto") -> tuple[str, ...]:
    """Platform directory names to try, in order, for this machine."""
    if setting and setting != "auto":
        return (setting, "common")
    native = PLATFORM_BY_SYSTEM.get(sys.platform, "linux")
    return (native, "common")


def expand_placeholders(text: str) -> str:
    """Turn ``{{path}}`` into ``<path>`` for display."""
    return _PLACEHOLDER.sub(lambda match: f"<{match.group(1)}>", text)


def render(markdown: str) -> list[tuple[str, str]]:
    """Convert tldr markdown into ``(style, text)`` display lines.

    The styles are the tag names the reader widget knows about, which keeps the
    parsing testable without a GTK display.
    """
    lines: list[tuple[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append((STYLE_TEXT, ""))
        elif stripped.startswith("#"):
            lines.append((STYLE_TITLE, stripped.lstrip("#").strip()))
        elif stripped.startswith(">"):
            lines.append((STYLE_DESC, expand_placeholders(stripped[1:].strip())))
        elif stripped.startswith("- "):
            lines.append((STYLE_BULLET, expand_placeholders(stripped[2:].strip())))
        elif stripped.startswith("`") and stripped.endswith("`"):
            lines.append((STYLE_CODE, expand_placeholders(stripped.strip("`").strip())))
        elif stripped.startswith("<!--"):
            continue
        else:
            lines.append((STYLE_TEXT, expand_placeholders(stripped)))
    while lines and lines[-1][1] == "":
        lines.pop()
    return lines


class Tldr:
    """Fetches tldr pages, from disk, client or network."""

    def __init__(
        self,
        cache_dir: Path,
        ttl_days: int = 7,
        platform: str = "auto",
        enabled: bool = True,
        timeout: float = 6.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = max(0, ttl_days) * 24 * 60 * 60
        self.platforms = candidate_platforms(platform)
        self.enabled = enabled
        self.timeout = timeout
        self._memory: dict[str, str] = {}

    # -- public ------------------------------------------------------------

    def fetch(self, name: str) -> str | None:
        """Return tldr markdown for ``name``, or ``None`` when unavailable."""
        if not self.enabled:
            return None
        name = name.strip()
        if not name or not _SAFE_NAME.match(name):
            return None
        if name in self._memory:
            return self._memory[name]

        markdown = self._from_cache(name)
        if markdown is None:
            markdown = self._from_client(name)
        if markdown is None:
            markdown = self._download(name)
        if markdown:
            self._memory[name] = markdown
        return markdown

    # -- sources -----------------------------------------------------------

    def _cache_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.md"

    def _from_cache(self, name: str) -> str | None:
        path = self._cache_path(name)
        try:
            stat = path.stat()
            if time.time() - stat.st_mtime > self.ttl_seconds:
                return None
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        return text if text.strip() else None

    def _from_client(self, name: str) -> str | None:
        executable = shutil.which("tldr")
        if not executable:
            return None
        for argv in ([executable, "--raw", name], [executable, "--color", "never", name]):
            try:
                result = subprocess.run(
                    argv, capture_output=True, text=True, timeout=self.timeout * 2, check=False
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0 and result.stdout.strip():
                self._write_cache(name, result.stdout)
                return result.stdout
        return None

    def _download(self, name: str) -> str | None:
        for platform in self.platforms:
            url = RAW_URL.format(platform=platform, name=name)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    markdown = response.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
                continue
            if markdown.strip():
                self._write_cache(name, markdown)
                return markdown
        return None

    def _write_cache(self, name: str, markdown: str) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(name).write_text(markdown, encoding="utf-8")
        except OSError:
            pass  # cache is a convenience, never a requirement


def is_markdown(text: str) -> bool:
    """Heuristic for client output: tldr markdown always has a heading or code."""
    stripped = text.lstrip()
    return stripped.startswith("#") or "`" in stripped
