"""Loading the shipped (or user supplied) Vim keybind dataset.

The dataset is plain JSON so it can be edited without touching Python.  A user
can point ``[behavior] vim_keys_file`` at their own file using the same schema
and the whole popup will follow it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_DATA_FILE = DATA_DIR / "vim_keys.json"

REQUIRED_KEYS = ("keys", "section", "description")


@dataclass(frozen=True)
class KeyBind:
    """One row of the keybind list."""

    keys: str
    description: str
    section: str
    section_title: str
    aliases: tuple[str, ...] = ()

    @property
    def haystack(self) -> str:
        """Text the fuzzy matcher searches.

        The section title is deliberately excluded: it is metadata, and folding
        it in makes every entry in "Search and replace" match the word
        "replace".
        """
        parts = [self.keys, self.description]
        parts.extend(self.aliases)
        return "  ".join(part for part in parts if part)


def _section_titles(document: dict) -> dict[str, str]:
    titles: dict[str, str] = {}
    for section in document.get("sections") or []:
        if isinstance(section, dict) and "id" in section:
            titles[str(section["id"])] = str(section.get("title", section["id"]))
    return titles


def parse(document: dict, source: str = "<data>") -> list[KeyBind]:
    """Turn a parsed JSON document into :class:`KeyBind` objects.

    Malformed entries are skipped rather than failing the whole load: a typo in
    a personal keybind file should not make the popup unusable.
    """
    titles = _section_titles(document)
    binds: list[KeyBind] = []
    for index, entry in enumerate(document.get("keys") or []):
        if not isinstance(entry, dict):
            continue
        missing = [key for key in REQUIRED_KEYS if not str(entry.get(key, "")).strip()]
        if missing:
            print(
                f"suprman: {source}: skipping entry {index}, missing {', '.join(missing)}",
                file=sys.stderr,
            )
            continue
        section = str(entry["section"]).strip()
        aliases = entry.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [str(aliases)]
        binds.append(
            KeyBind(
                keys=str(entry["keys"]).strip(),
                description=str(entry["description"]).strip(),
                section=section,
                section_title=titles.get(section, section.replace("-", " ").title()),
                aliases=tuple(str(alias) for alias in aliases),
            )
        )
    return binds


def load(path: Path | str | None = None) -> list[KeyBind]:
    """Load keybinds from ``path``, falling back to the bundled dataset."""
    source = Path(path).expanduser() if path else DEFAULT_DATA_FILE
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"suprman: cannot read keybinds from {source}: {error}", file=sys.stderr)
        if source == DEFAULT_DATA_FILE:
            return []
        return load(None)
    return parse(document, source=str(source))
