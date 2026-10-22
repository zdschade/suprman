"""Vim-style navigation: key intents, the ``:`` prompt and scroll math.

The window owns the GTK side of key handling; everything that can be decided
without a widget lives here so it stays testable on a box with no display (see
``tests/test_vimnav.py``).  Key *names* are the ones ``Gdk.keyval_name``
returns, which is why the mappings spell ``slash`` and ``colon`` out.
"""

from __future__ import annotations

#: Commands accepted by the ``:`` prompt.  The value is the action the window
#: knows how to perform; unknown commands do nothing.
COMMANDS: dict[str, str] = {
    "q": "quit",
    "q!": "quit",
    "quit": "quit",
    "qa": "quit",
    "qa!": "quit",
}

#: Normal-mode letters.  ``j``/``k`` move (or scroll) a step, ``h``/``l`` go
#: sideways in the reader and ``l`` opens the selected row in the list.
#: Half/full page keys live under Ctrl below.
_KEYS: dict[str, str] = {
    "j": "down",
    "k": "up",
    "h": "left",
    "l": "right",
    "G": "bottom",
    "slash": "search",
    "colon": "command",
}

#: Ctrl-modified keys, looked up with the shifted spelling folded down.
_CTRL_KEYS: dict[str, str] = {
    "d": "half_down",
    "u": "half_up",
    "f": "page_down",
    "b": "page_up",
}

#: Longest command line accepted by the prompt, in characters.
MAX_COMMAND_LENGTH = 40


def intent(name: str, *, ctrl: bool = False) -> str:
    """Translate a GDK key name into a navigation intent.

    Returns ``""`` for keys that are not ours, so the caller can fall through
    to GTK's own handling (arrows, Enter, Tab, a focused text field).
    """
    if ctrl:
        return _CTRL_KEYS.get(name.lower(), "")
    return _KEYS.get(name, "")


def clamp_scroll(value: float, lower: float, upper: float, page_size: float) -> float:
    """Keep a scroller value inside its scrollable range.

    ``upper`` is the total height including the viewport, so the last useful
    value is ``upper - page_size``; short documents (a viewport taller than the
    content) collapse to ``lower`` instead of going negative.
    """
    return max(lower, min(upper - page_size, value))


class CommandLine:
    """The ``:`` prompt: characters in, one action out.

    The window shows ``prompt`` in the footer and feeds every printable key
    here while ``active``.  Execution is deliberately separate from typing so
    the whole thing can be exercised without a window.
    """

    def __init__(self) -> None:
        self.active = False
        self.text = ""

    @property
    def prompt(self) -> str:
        return f":{self.text}"

    def start(self) -> None:
        """Open the prompt on an empty line."""
        self.active = True
        self.text = ""

    def cancel(self) -> None:
        """Close the prompt and forget what was typed."""
        self.active = False
        self.text = ""

    def feed(self, char: str) -> None:
        """Append one printable character (ignored while closed or full)."""
        if not self.active or len(self.text) >= MAX_COMMAND_LENGTH:
            return
        if char.isprintable():
            self.text += char

    def backspace(self) -> bool:
        """Delete one character.

        Returns ``False`` once the line is empty again, which is the window's
        cue to leave the prompt (vim does the same).
        """
        self.text = self.text[:-1]
        return bool(self.text)

    def execute(self) -> str:
        """Return the action for what was typed, or ``""`` when unknown."""
        command = self.text.strip().lstrip(":").lower()
        return COMMANDS.get(command, "")
