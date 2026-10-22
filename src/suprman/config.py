"""Configuration handling for suprman.

The configuration lives in a small INI file -- by default
``$XDG_CONFIG_HOME/suprman/suprman.conf`` -- and is read with the standard
library :mod:`configparser`.  Every key is optional: anything missing, unknown
or malformed falls back to the defaults declared on the dataclasses below, so a
truncated or hand-edited file can never prevent the popup from opening.

The same values drive the GTK stylesheet (see :func:`build_css`), which keeps
"what the config says" and "what the window looks like" in one place.
"""

from __future__ import annotations

import configparser
import dataclasses
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import TypeVar, cast

APP_NAME = "suprman"
CONFIG_FILENAME = "suprman.conf"

DATA_DIR = Path(__file__).resolve().parent / "data"

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

_SECTION_APPEARANCE = "appearance"
_SECTION_BEHAVIOR = "behavior"


def default_config_dir() -> Path:
    """Return ``$XDG_CONFIG_HOME/suprman`` with a sensible fallback."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / APP_NAME


def default_config_path() -> Path:
    """Return the path suprman reads its configuration from by default."""
    override = os.environ.get("SUPRMAN_CONFIG")
    return Path(override).expanduser() if override else default_config_dir() / CONFIG_FILENAME


@dataclass(frozen=True)
class Appearance:
    """Everything that ends up in the generated stylesheet."""

    background: str = "#0b0b10"
    foreground: str = "#e7e7ea"
    accent: str = "#7aa2f7"
    muted: str = "#82869b"
    border: str = "#ffffff1f"
    opacity: float = 0.88
    radius: int = 14
    width: int = 780
    height: int = 520
    padding: int = 14
    row_height: int = 32
    font: str = "Sans 11"
    key_font: str = "Monospace 10"
    title_font: str = "Sans Bold 12"


@dataclass(frozen=True)
class Behavior:
    """Non-visual knobs."""

    max_results: int = 200
    man_sections: str = "1,2,3,4,5,6,7,8"
    man_width: int = 100
    tldr: bool = True
    tldr_ttl_days: int = 7
    tldr_platform: str = "auto"
    vim_keys_file: str = ""
    layer_shell: str = "auto"  # auto | always | never
    case_sensitive: bool = False
    open_exact: bool = True
    # Vim keys for navigation: j/k move or scroll, h/l sideways, / search,
    # :q quit.  false keeps the keyboard in the search field at all times.
    vim_mode: bool = True
    # auto: leave a resident instance behind after the first cold start, so the
    # next hotkey press is a socket write. never: always start from scratch.
    resident: str = "auto"
    # Minutes a resident instance may sit idle before it exits (0 = never).
    resident_idle_minutes: int = 30

    def section_list(self) -> tuple[str, ...]:
        """Parse ``man_sections`` into a tuple of man section names."""
        return tuple(part.strip() for part in self.man_sections.split(",") if part.strip())


@dataclass(frozen=True)
class Config:
    appearance: Appearance = Appearance()
    behavior: Behavior = Behavior()
    path: Path | None = None

    @property
    def tldr_cache_dir(self) -> Path:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
        return Path(base) / APP_NAME / "tldr"

    @property
    def index_cache_dir(self) -> Path:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
        return Path(base) / APP_NAME


AppT = TypeVar("AppT", Appearance, Behavior)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _warn(debug: bool, message: str) -> None:
    if debug:
        print(f"suprman: config: {message}", file=sys.stderr)


def _coerce(raw: str, default: object, debug: bool, label: str) -> object:
    """Convert ``raw`` to the type of ``default``, falling back when invalid."""
    if isinstance(default, bool):
        value = raw.strip().lower()
        if value in ("1", "yes", "true", "on"):
            return True
        if value in ("0", "no", "false", "off"):
            return False
        _warn(debug, f"{label}: expected a boolean, got {raw!r}")
        return default
    if isinstance(default, int):
        try:
            return int(raw.strip())
        except ValueError:
            _warn(debug, f"{label}: expected an integer, got {raw!r}")
            return default
    if isinstance(default, float):
        try:
            return float(raw.strip())
        except ValueError:
            _warn(debug, f"{label}: expected a number, got {raw!r}")
            return default
    return raw.strip()


def _read_section(
    parser: configparser.ConfigParser,
    section: str,
    template: AppT,
    debug: bool,
) -> AppT:
    """Build a dataclass instance from one INI section."""
    values: dict[str, object] = {}
    for field in dataclasses.fields(template):
        if parser.has_option(section, field.name):
            raw = parser.get(section, field.name, fallback="")
            values[field.name] = _coerce(raw, field.default, debug, f"[{section}] {field.name}")

    instance = dataclasses.replace(template, **values)
    if isinstance(instance, Appearance):
        return cast(AppT, _validate_appearance(instance, debug))
    if isinstance(instance, Behavior):
        return cast(AppT, _validate_behavior(instance, debug))
    return instance


def _validate_appearance(appearance: Appearance, debug: bool) -> Appearance:
    changes: dict[str, object] = {}
    for name in ("background", "foreground", "accent", "muted", "border"):
        value = getattr(appearance, name)
        if not _HEX_RE.match(value):
            _warn(debug, f"[appearance] {name}: invalid colour {value!r}, using default")
            changes[name] = getattr(Appearance(), name)
    if not 0.0 <= appearance.opacity <= 1.0:
        _warn(debug, f"[appearance] opacity: {appearance.opacity} outside 0..1, clamping")
        changes["opacity"] = min(1.0, max(0.0, appearance.opacity))
    for name in ("radius", "width", "height", "padding", "row_height"):
        if getattr(appearance, name) <= 0:
            _warn(debug, f"[appearance] {name}: must be positive, using default")
            changes[name] = getattr(Appearance(), name)
    return dataclasses.replace(appearance, **changes)


def _validate_behavior(behavior: Behavior, debug: bool) -> Behavior:
    changes: dict[str, object] = {}
    if behavior.max_results < 1:
        _warn(debug, "[behavior] max_results: must be at least 1")
        changes["max_results"] = Behavior().max_results
    if behavior.man_width < 40:
        _warn(debug, "[behavior] man_width: too narrow, using 80")
        changes["man_width"] = 80
    if behavior.layer_shell not in ("auto", "always", "never"):
        _warn(debug, f"[behavior] layer_shell: unknown value {behavior.layer_shell!r}")
        changes["layer_shell"] = "auto"
    if not behavior.section_list():
        _warn(debug, "[behavior] man_sections: empty, using all sections")
        changes["man_sections"] = Behavior().man_sections
    if behavior.tldr_platform == "":
        changes["tldr_platform"] = "auto"
    if behavior.resident not in ("auto", "never"):
        _warn(debug, f"[behavior] resident: unknown value {behavior.resident!r}")
        changes["resident"] = "auto"
    if behavior.resident_idle_minutes < 0:
        changes["resident_idle_minutes"] = 0
    return dataclasses.replace(behavior, **changes)


def load(path: Path | str | None = None, debug: bool = False) -> Config:
    """Read the configuration file, returning defaults when it is absent."""
    config_path = Path(path).expanduser() if path else default_config_path()
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    found = config_path.exists()
    try:
        if found:
            parser.read(config_path, encoding="utf-8")
    except (configparser.Error, OSError) as error:
        _warn(debug, f"could not read {config_path}: {error}")
        found = False

    appearance = _read_section(parser, _SECTION_APPEARANCE, Appearance(), debug)
    behavior = _read_section(parser, _SECTION_BEHAVIOR, Behavior(), debug)
    return Config(
        appearance=appearance,
        behavior=behavior,
        path=config_path if found else None,
    )


# ---------------------------------------------------------------------------
# appearance helpers
# ---------------------------------------------------------------------------


def hex_to_rgba(value: str, alpha: float) -> str:
    """Turn ``#rgb``/``#rrggbb``/``#rrggbbaa`` into a CSS ``rgba()`` string."""
    digits = value.lstrip("#")
    if len(digits) in (3, 4):
        digits = "".join(char * 2 for char in digits)
    red, green, blue = (int(digits[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha:.3f})"


_WEIGHTS = {
    "thin": 100,
    "extralight": 200,
    "light": 300,
    "regular": 400,
    "normal": 400,
    "medium": 500,
    "semibold": 600,
    "demibold": 600,
    "bold": 700,
    "extrabold": 800,
    "black": 900,
}


@dataclass(frozen=True)
class FontSpec:
    """A Pango font description split into CSS-friendly parts."""

    family: str
    size: float
    weight: int = 400
    style: str = "normal"

    def declare(self, prefix: str) -> dict[str, object]:
        return {
            f"{prefix}_family": _quote_family(self.family),
            f"{prefix}_size": f"{self.size:g}",
            f"{prefix}_weight": str(self.weight),
            f"{prefix}_style": self.style,
        }


def _quote_family(family: str) -> str:
    families = [part.strip() for part in family.split(",") if part.strip()]
    quoted = [f'"{part}"' if " " in part else part for part in families]
    return ", ".join(quoted) or "Sans"


def parse_font(description: str, fallback: FontSpec) -> FontSpec:
    """Split ``Sans Bold 12`` into family, weight and point size.

    GTK's CSS parser wants ``font-family`` / ``font-size`` / ``font-weight``
    declarations, while users reasonably write Pango descriptions in the config
    file.  This bridges the two, falling back to the whole default on anything
    unparseable.
    """
    text = description.strip()
    if not text:
        return fallback

    size = fallback.size
    match = re.search(r"(\d+(?:\.\d+)?)\s*$", text)
    if match:
        size = float(match.group(1))
        text = text[: match.start()].strip()

    style = "normal"
    words = text.split()
    weight = 400
    families: list[str] = []
    for word in words:
        lowered = word.lower()
        if lowered in _WEIGHTS:
            weight = _WEIGHTS[lowered]
        elif lowered in ("italic", "oblique"):
            style = lowered
        else:
            families.append(word)

    family = " ".join(families).strip()
    if not family:
        family = fallback.family
    if not 4 <= size <= 96:
        size = fallback.size
    return FontSpec(family=family, size=size, weight=weight, style=style)


def build_css(config: Config) -> str:
    """Render ``data/style.css`` with the values from ``config``."""
    appearance = config.appearance
    template = Template((DATA_DIR / "style.css").read_text(encoding="utf-8"))

    body = parse_font(appearance.font, FontSpec("Sans", 11))
    key = parse_font(appearance.key_font, FontSpec("Monospace", 10))
    title = parse_font(appearance.title_font, FontSpec("Sans", 12, 700))

    values: dict[str, object] = {
        "background": appearance.background,
        "background_rgba": hex_to_rgba(appearance.background, appearance.opacity),
        "foreground": appearance.foreground,
        "accent": appearance.accent,
        "accent_soft": hex_to_rgba(appearance.accent, 0.22),
        "muted": appearance.muted,
        "border": appearance.border,
        "radius": appearance.radius,
        "padding": appearance.padding,
        "row_height": appearance.row_height,
    }
    values.update(body.declare("font"))
    values.update(key.declare("key"))
    values.update(title.declare("title"))
    return template.safe_substitute(values)


EXAMPLE_CONFIG = """\
# suprman -- configuration
#
# Location: $XDG_CONFIG_HOME/suprman/suprman.conf
#           (usually ~/.config/suprman/suprman.conf)
#
# Every value is optional; delete a line to fall back to the built-in default.
# Colours accept #rgb, #rrggbb and #rrggbbaa.

[appearance]
# Window surface colour and how opaque it is (0.0 - 1.0).
# Lower opacity = glassier; pair with a compositor blur rule (see README).
background = #0b0b10
opacity = 0.88
foreground = #e7e7ea
accent = #7aa2f7
muted = #82869b
border = #ffffff1f

# Geometry, in logical pixels.
width = 780
height = 520
radius = 14
padding = 14
row_height = 32

# Pango font descriptions.
font = Sans 11
key_font = Monospace 10
title_font = Sans Bold 12

[behavior]
# Maximum number of rows rendered at once.
max_results = 200
# Man sections to index (comma separated). Use 1,8 to keep it snappy.
man_sections = 1,2,3,4,5,6,7,8
# Width, in columns, used to re-render man pages.
man_width = 100
# Show tldr pages above man pages.
tldr = true
# Re-fetch tldr pages older than this many days.
tldr_ttl_days = 7
# auto | linux | osx | windows | common
tldr_platform = auto
# Point at your own keybind file (same JSON schema as data/vim_keys.json).
vim_keys_file =
# auto: leave a resident instance running so later launches are instant.
# never: always start cold.
resident = auto
# Minutes before an idle resident instance exits (0 = never).
resident_idle_minutes = 30
# auto: use wlr-layer-shell when the compositor supports it.
# always: fail loudly if unavailable.  never: plain toplevel window.
layer_shell = auto
case_sensitive = false
# Open a man page straight away when the query matches a page name exactly.
open_exact = true
# Vim keys for navigation: j/k move or scroll, h/l sideways, / focuses the
# search field, :q quits.  The search field is focused only in insert mode.
# false restores the old behaviour: the keyboard always types into the field.
vim_mode = true
"""


def write_example_config(path: Path | str | None = None) -> Path:
    """Write the annotated example configuration, without clobbering."""
    target = Path(path).expanduser() if path else default_config_dir() / CONFIG_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    target.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    return target
