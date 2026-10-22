# AGENTS.md — working on suprman

A GTK4/PyGObject popup (Fedora/Arch/Ubuntu) that searches Vim keybinds, man pages and
[tldr](https://tldr.sh/) pages from one text field. User-facing documentation is
[README.md](README.md); this file is the map for changing the code without rediscovering it.

Read this first, then `src/suprman/window.py` (the flow) and `src/suprman/config.py` (the knobs).
If the `gtk4-python-apps` skill is available in your environment, it contains the generic GTK4
lessons this project was built on.

## Commands

| Command | What it does |
| --- | --- |
| `make run` | Run from the checkout (`PYTHONPATH=src python3 -m suprman`); needs a display |
| `make test` | `unittest` suite, 138 tests; ~10 skip without GTK or a display |
| `make lint` | `ruff check` + formatting (installed? `python3 -m pip install ruff`) |
| `make check` | Byte-compile everything (syntax only) |
| `make example-config` | Write the annotated config to `~/.config/suprman/suprman.conf` |
| `./install.sh --user` | Install into `~/.local` (no root); `sudo ./install.sh` for `/usr/local` |
| `make install PREFIX=/usr DESTDIR=/tmp/pkg` | Staging install for packagers |
| `SUPRMAN_TIMING=1 suprman` | Per-milestone startup timings on stderr |
| `make dist` | Source tarball for a PKGBUILD/spec |

## Layout

```
bin/suprman              launcher: locates the package, then the socket fast path
src/suprman/app.py       CLI, Gtk.Application, stylesheet load, resident (--serve) wiring
src/suprman/window.py    the popup: search field, result list, lazy rows, mode switching
src/suprman/reader.py    man/tldr view with in-page search and match walking
src/suprman/query.py     search bar -> (Mode, term); the "man"/"tldr" prefixes
src/suprman/fuzzy.py     token ranking: substring > subsequence, phrase bonus, coverage
src/suprman/vimkeys.py   dataset loader (KeyBind, haystack composition)
src/suprman/manpages.py  apropos index, manpath fallback, man rendering, disk cache
src/suprman/tldr.py      client / cache / HTTP fallback, markdown -> styled lines
src/suprman/textsearch.py overstrike + ANSI cleanup, in-page match spans
src/suprman/config.py    INI parsing, validation, CSS rendering, EXAMPLE_CONFIG
src/suprman/timing.py    SUPRMAN_TIMING=1 marks (process age from /proc/self/stat)
src/suprman/ipc.py       stdlib-only Unix socket client + spawn; safe before `import gi`
src/suprman/server.py    Gio.SocketService listener for resident mode
src/suprman/layershell.py optional wlr-layer-shell, fails soft
src/suprman/data/        style.css (string.Template), vim_keys.json (one entry per line)
data/                    .desktop entry and systemd user unit
docs/config.example.conf generated from config.EXAMPLE_CONFIG — never hand-edit
tests/                   GTK-free unit tests plus a few Gio/widget ones that skip
```

## Hard rules

1. **Dependencies**: Python standard library + PyGObject only. No PyYAML/requests/click/pytest.
   `json`, `configparser`, `urllib`, `unittest`, `argparse` are the intended tools. Light means
   "nothing to pip install", never "fewer features".
2. **Python >= 3.10**, GTK4 only. `from __future__ import annotations` everywhere.
3. Every module that imports `Gdk` must `gi.require_version("Gdk", "4.0")` too, not just `Gtk`.
   Application flags are `Gio.ApplicationFlags` — `Gtk.ApplicationFlags` is GTK3 and raises at
   construction.
4. **No GTK calls off the main thread.** Workers return through `GLib.idle_add` and compare
   `self._generation`; `_closing` guards window teardown.
5. **Config parity is tested**: `docs/config.example.conf` must equal `config.EXAMPLE_CONFIG`.
   After adding/editing a key run
   `PYTHONPATH=src python3 -c "from suprman import config; open('docs/config.example.conf','w').write(config.EXAMPLE_CONFIG)"`.
6. `src/suprman/data/style.css` is a `string.Template`: `$name` placeholders are filled by
   `config.build_css`. No other `$` may appear. It carries colours/sizes only — GTK *widget*
   properties (`wrap-mode`, `has-tooltip`, …) are not CSS and are silently ignored with a warning.
7. `vim_keys.json` keeps one object per line, `{keys, section, description, aliases?}`. Sections
   must be declared and used; `(keys, section)` must be unique (tests enforce both). After any
   programmatic rewrite, re-parse the file — a dropped brace is silent until load.
8. Never add window shadows or blur: clipped/owned by the compositor. See README "Glass and blur".

## How a search flows

`window._on_search_changed` → `query.parse` → `Mode.VIM` (`show_vim`) or `Mode.MAN/TLDR`
(`show_man`). `fuzzy.rank` scores each item's `haystack`; `window._set_rows` materialises about a
screenful of `ResultRow`s (lazy, grows on scroll). Enter on a man page calls `open_page`, which
starts two worker threads (tldr via HTTP/client, man via `man` subprocess), each rendering into a
named part of `ReaderView`.

`Config` is the single source of truth for look and behaviour; `reader._create_tags` is the one
place colours are set in Python (text-buffer tags), so keep it in sync with `Appearance`.

## Resident instance (why the hotkey path is fast)

Cold start is ~900 ms, of which ~430 ms is Python+PyGObject imports and ~190 ms GTK theme init —
neither is fixable in this app. So:

- `bin/suprman` and `__main__.py` call `ipc.try_handoff()` **before** importing `app`/`gi`. That
  module must stay stdlib-only; importing `gi` there costs the whole win (~32 ms vs ~900 ms).
- A cold window spawns one detached `--serve` instance **when it closes** (never at startup, or a
  second window appears over the visible one). `resident = never` opts out.
- The server hides on `close-request` (returns True), resets on show, and exits after
  `resident_idle_minutes` idle. A spawn marker file stops double-spawns while it binds.
- Cold path must keep working alone: a dead server degrades to slow, not broken.

## Performance rules

Already done, do not regress: lazy rows, lazy `ReaderView`, lazy `urllib`, `compileall` in
`make install`, socket handoff, `mark()` calls at each milestone. If something feels slow, measure
with `SUPRMAN_TIMING=1` before changing code; the first line tells you whether the handoff worked
(`handoff: delivered` vs `handoff: no resident instance`). Do not trade a feature for milliseconds.

## Verifying without a display

GTK-free tests run anywhere (`fuzzy`, `query`, `config`, `textsearch`, `manpages`, `tldr`,
`vimkeys`, `ipc`, `timing`). Keep new logic there — that is why those modules exist.

`tests/test_gtk_surface.py` runs only where PyGObject can load GTK4. To get that on a headless
box without root, extract the typelibs locally:

```sh
mkdir -p /tmp/gtkroot && cd /tmp/gtkroot
apt-get download gir1.2-gtk-4.0 gir1.2-graphene-1.0 gir1.2-freedesktop gir1.2-pango-1.0 \
    gir1.2-atk-1.0 gir1.2-gdkpixbuf-2.0 gir1.2-harfbuzz-0.0 gir1.2-gdesktopenums-3.0 \
    libgtk-4-1 libgtk-4-common libcairo2 libcairo-script-interpreter2
for d in *.deb; do dpkg-deb -x "$d" root; done
glib-compile-schemas root/usr/share/glib-2.0/schemas
triplet=$(find root/usr/lib -maxdepth 1 -type d -name '*-linux-gnu*' -printf '%f\n' | head -1)
export LD_LIBRARY_PATH=$PWD/root/usr/lib/$triplet
export GI_TYPELIB_PATH=$LD_LIBRARY_PATH/girepository-1.0
export GSETTINGS_SCHEMA_DIR=$PWD/root/usr/share/glib-2.0/schemas
export XDG_DATA_DIRS=$PWD/root/usr/share:/usr/share
```

That buys: module imports, `Gtk.CssProvider.load_from_data(build_css(...))` (the only real check
that the stylesheet parses), `Gtk.Application(...)` construction, and the whole `ControlServer`
socket path. It does **not** buy widgets: GTK 4.14 has no headless backend and constructing any
widget with `Gdk.Display.get_default() is None` **segfaults** (exit 139, no summary) instead of
raising. Gate widget tests on a real display, then hand the UI check to the user (`make run`).

## Pitfalls already paid for

- roff overstrike uses a literal backspace (`\x08`), not `\b`; the wrong regex eats characters at
  every word boundary and turns man pages into soup. Assert `"\x08" not in text`.
- Rendering man pages with `wrap-mode` on re-flows text that `man` already laid out; keep
  `WrapMode.NONE` plus a horizontal scroller.
- GTK CSS `scrollbar slider { min-width: … }` without `margin: 0` reports negative sizes.
- One capture-phase `EventControllerKey` on the window handles all keys; per-widget handlers fought
  `GtkSearchEntry`'s own Escape and needed the version-sensitive `get_current_event_state()`.
- `ListBox.select_row()` does not scroll while focus stays in the entry: measure with
  `row.compute_bounds(listbox)` and move the `vadjustment`.
- `section_title` is deliberately excluded from keybind haystacks; including it made every
  "Search and replace" entry match "replace".
- Sparse subsequence matching is limited to tokens of <= 2 characters; without that, "copy" matched
  "complete … keyword".

## Cookbook

- **Add a keybind**: one line in `data/vim_keys.json` with `aliases` for the words users would
  search ("copy", "comment"). Then `make test` (uniqueness + section checks).
- **Add a config option**: field on `Appearance`/`Behavior`, a line in `EXAMPLE_CONFIG` (comments on
  their own line — `#` also starts colours), validation in `_validate_*` if it can be wrong,
  regenerate `docs/config.example.conf`, mention it in README.
- **Add a search prefix**: `query.Mode` + `PREFIXES` + the dispatch in `window._refresh`.
- **Change the look**: `appearance` keys + `style.css` only; text-buffer colours live in
  `reader._create_tags`.
- **Change ranking**: `fuzzy.py`; the acceptance tests are in `tests/test_fuzzy.py` and
  `tests/test_vimkeys.py` (`"delete line"` must rank `dd` first).

## Definition of done

`make lint` clean, `make test` green, config parity preserved, `docs/config.example.conf`
regenerated if config changed, README updated if user-facing behaviour changed, and a note in the
commit message about what you could not verify (usually: widgets, key handling, real compositor).
