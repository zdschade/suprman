"""Everything that talks to ``man``/``apropos``.

Three jobs live here:

* :func:`load_index` -- enumerate the installed man pages (through ``apropos``,
  with a ``manpath`` fallback) and cache the result on disk.
* :func:`render_page` -- re-render one page to plain text at a known width.
* :func:`parse_apropos` / :func:`strip_overstrike` -- the pure bits, so the
  format handling can be unit tested without a man database.

Nothing here imports GTK; all calls are blocking and run on worker threads from
the UI layer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .textsearch import strip_overstrike

CACHE_FILENAME = "man_index.json"
CACHE_TTL_SECONDS = 24 * 60 * 60

#: ``ls (1)          - list directory contents``
_APROPOS_LINE = re.compile(
    r"^(?P<name>[^()]+?)\s*\((?P<section>[^()\s]+)\)\s*(?:-\s*(?P<description>.*))?$"
)

#: Fallback scan of the manpath: ``/usr/share/man/man1/ls.1.gz``
_MAN_FILE = re.compile(r"^(?P<name>.+?)\.(?P<section>[^./]+?)(?:\.gz)?$")


class ManError(RuntimeError):
    """Raised when a page cannot be rendered."""


@dataclass(frozen=True)
class ManPage:
    """One installed man page."""

    name: str
    section: str
    description: str = ""

    @property
    def title(self) -> str:
        return f"{self.name}({self.section})"

    @property
    def haystack(self) -> str:
        return f"{self.name}  {self.title}  {self.description}"

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.name.lower(), self.section)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def parse_apropos(text: str) -> list[ManPage]:
    """Parse ``apropos`` output into pages, newest format tolerant.

    Handles comma separated name lists (``foo, bar (1) - ...``) by emitting one
    entry per name, and silently drops lines that are not page references.
    """
    pages: dict[tuple[str, str], ManPage] = {}
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith(" "):
            continue
        match = _APROPOS_LINE.match(line)
        if not match:
            continue
        section = match.group("section").strip()
        description = (match.group("description") or "").strip()
        for name in match.group("name").split(","):
            name = name.strip()
            if not name:
                continue
            key = (name, section)
            if key not in pages:
                pages[key] = ManPage(name=name, section=section, description=description)
    return sorted(pages.values(), key=lambda page: page.sort_key)


def _parse_manpath_output(text: str) -> list[ManPage]:
    """Fallback: walk ``manpath`` output for page files."""
    pages: dict[tuple[str, str], ManPage] = {}
    for directory in text.strip().split(os.pathsep):
        if not directory or not Path(directory).is_dir():
            continue
        for path in Path(directory).glob("man*/*"):
            match = _MAN_FILE.match(path.name)
            if not match:
                continue
            section = match.group("section")
            name = match.group("name")
            if name.endswith(".3pm"):  # perl pages keep their suffix in the name
                name = name[: -len(".3pm")]
            pages.setdefault((name, section), ManPage(name=name, section=section))
    return sorted(pages.values(), key=lambda page: page.sort_key)


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


def _sections_argument(sections: tuple[str, ...]) -> list[str]:
    if not sections:
        return []
    return ["-s", ",".join(sections)]


def _run(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("MANWIDTH", "100")
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def build_index(sections: tuple[str, ...] = ()) -> list[ManPage]:
    """Enumerate installed pages. Blocking; call from a worker thread."""
    if shutil.which("apropos"):
        result = _run(["apropos", *_sections_argument(sections), ""])
        pages = parse_apropos(result.stdout)
        if pages:
            return pages

    if shutil.which("manpath"):
        result = _run(["manpath"])
        pages = _parse_manpath_output(result.stdout)
        if pages:
            return pages

    # Last resort: ask man for each section it knows about.
    if shutil.which("man") and sections:
        collected: list[ManPage] = []
        for section in sections:
            result = _run(["man", "-s", section, "-k", "."])
            collected.extend(parse_apropos(result.stdout))
        if collected:
            return sorted(collected, key=lambda page: page.sort_key)
    return []


def _read_cache(cache_file: Path, max_age: int = CACHE_TTL_SECONDS) -> list[ManPage] | None:
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if time.time() - float(payload["built"]) > max_age:
            return None
        pages = [
            ManPage(name=name, section=section, description=description)
            for name, section, description in payload["pages"]
        ]
        return pages or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(cache_file: Path, pages: list[ManPage]) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "built": time.time(),
                    "pages": [[page.name, page.section, page.description] for page in pages],
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(cache_file)
    except OSError:
        pass  # a read-only cache directory is not fatal


def load_index(
    sections: tuple[str, ...] = (),
    cache_dir: Path | None = None,
    max_age: int = CACHE_TTL_SECONDS,
) -> list[ManPage]:
    """Return the man page index, using an on-disk cache when it is fresh."""
    cache_file = (cache_dir or Path.home() / ".cache" / "suprman") / CACHE_FILENAME
    if max_age > 0:
        cached = _read_cache(cache_file, max_age)
        if cached:
            return cached
    pages = build_index(sections)
    if pages:
        _write_cache(cache_file, pages)
    return pages


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"^[\w.+:@-]+$")


def render_page(name: str, section: str = "", width: int = 100, timeout: float = 15.0) -> str:
    """Render a man page to plain text.

    ``man`` is asked for a fixed width and no hyphenation/justification so the
    text reflows predictably inside the popup.
    """
    if not _SAFE_NAME.match(name):
        raise ManError(f"not a valid page name: {name!r}")
    if not shutil.which("man"):
        raise ManError("the 'man' command is not installed")

    env = dict(os.environ)
    env.update({"MANWIDTH": str(width), "MANPAGER": "cat", "PAGER": "cat", "GROFF_NO_SGR": "1"})
    env.setdefault("LC_ALL", "C.UTF-8")

    argv = ["man"]
    if section:
        argv += ["-s", section]
    argv += ["--nh", "--nj", "-P", "cat", "--", name]

    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=env, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise ManError(f"timed out rendering {name}") from error

    output = result.stdout
    if result.returncode != 0 or not output.strip():
        # Busybox and BSD man do not understand man-db's flags; retry plainly.
        try:
            result = subprocess.run(
                ["man", name], capture_output=True, text=True, timeout=timeout, env=env, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise ManError(f"timed out rendering {name}") from error
        output = result.stdout
        if not output.strip():
            detail = (result.stderr or "").strip().splitlines()
            raise ManError(detail[0] if detail else f"no man page for {name}")
    return strip_overstrike(output)


def available() -> bool:
    """True when a usable ``man`` command exists."""
    return shutil.which("man") is not None
