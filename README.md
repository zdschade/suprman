# suprman

A small, glassy popup for two things you look up constantly and never remember:
**Vim keybinds** and **man pages**.

Bind it to a key, press it, type. `delete line` finds `dd`. `man ls` opens the
`ls` page with its [tldr](https://tldr.sh/) summary above it — and, unlike
`man`, the search box stays, so you can jump straight to `--color` without
paging through a wall of text.

- Wayland first (GTK4), X11 works too
- No daemon, no server, no config file required
- Python + GTK4 only: no third-party Python packages
- Search that understands words, not just prefixes

```
┌───────────────────────────────────────────────────────────────┐
│  delete line                                      VIM KEYS    │
├───────────────────────────────────────────────────────────────┤
│  dd        delete the current line              Operators     │
│  D         delete from the cursor to the en…    Operators     │
│  x         delete the character under the cu…   Editing       │
│  dG        delete from the cursor to the end…   Operators     │
└───────────────────────────────────────────────────────────────┘
  ↑↓ move · Enter open · Esc clear or quit · Tab next
```

## Install

Dependencies come from your distro, because PyGObject must match the system
GTK:

| Distro        | Command                                                        |
| ------------- | -------------------------------------------------------------- |
| Fedora        | `sudo dnf install python3-gobject gtk4`                        |
| Arch          | `sudo pacman -S python-gobject gtk4`                           |
| Ubuntu/Debian | `sudo apt install python3-gi gir1.2-gtk-4.0`                   |

Optional, for a proper popup (centred, no window decoration in the task
switcher, keyboard grab) on Wayland:

| Distro        | Package                                          |
| ------------- | ------------------------------------------------ |
| Fedora        | `gtk4-layer-shell`                               |
| Arch          | `gtk4-layer-shell`                               |
| Ubuntu/Debian | `libgtk4-layer-shell0` (24.10+; a PPA before)    |

suprman detects layer shell at runtime and falls back to a plain window, so the
optional package is never required.

### From a checkout

```sh
git clone https://github.com/suprman/suprman && cd suprman
./install.sh --user          # ~/.local, no root
# or
sudo ./install.sh            # /usr/local
```

`make install PREFIX=/usr` and `make uninstall` work too, and honour `DESTDIR`
for packagers. `make dist` produces a tarball for a PKGBUILD or RPM spec.

## Bind it to a key

suprman does not install its own hotkey daemon — every desktop already has one,
and fighting it is how you get a second copy of `xbindkeys`.

**Hyprland** (`~/.config/hypr/hyprland.conf`):

```ini
bind = SUPER, slash, exec, suprman
windowrule = float, class:^(suprman)$
windowrule = center, class:^(suprman)$
# optional: real glass, since we ship an alpha background
layerrule = blur, suprman
layerrule = ignorealpha 0.05, suprman
```

**KDE Plasma**: System Settings → Shortcuts → Custom Shortcuts → New → Global
Shortcut → Command/URL, trigger `suprman`. Optionally add a Window Rule for
`suprman` with *Apply initially: Force* → *Active/Inactive opacity* and, on
Plasma 6.2+, a blur rule.

**GNOME**: Settings → Keyboard → Custom Shortcuts → `+`, command `suprman`.
The [Blur My Shell](https://extensions.gnome.org/extension/3193/blur-my-shell/)
extension can blur the window.

**Sway / i3**:

```ini
# sway
bindsym $mod+slash exec suprman
for_window [app_id="suprman"] floating enable, border none, move position center

# i3
bindsym $mod+slash exec --no-startup-id suprman
for_window [class="suprman"] floating enable, border none, move position center
```

**niri** (`~/.config/niri/config.kdl`):

```kdl
binds {
    Mod+Slash { spawn "suprman"; }
}
window-rule {
    match app-id="suprman"
    open-floating true
}
```

## Using it

Everything happens in one field.

| You type        | You get                                                        |
| --------------- | -------------------------------------------------------------- |
| `delete line`   | matching keybinds, best first                                  |
| `:q`            | keybinds for quitting (colons are ignored)                     |
| `man`           | the list of every installed man page                           |
| `man grep`      | filtered pages; `grep` opens immediately on an exact match      |
| `tldr grep`     | same list, jumping straight into the reader with tldr on top   |

Inside a man page the search box stays: typing filters and highlights every
hit, `Enter` / `Ctrl-n` walk forward, `Shift+Enter` / `Ctrl-p` walk back, and
the counter shows `3 / 17 matches`.

| Key                | List                    | Reader                    |
| ------------------ | ----------------------- | ------------------------- |
| `↑` `↓` `Ctrl-p/n` | move                    | previous / next match     |
| `Tab` / `Shift-Tab`| move                    | —                         |
| `PgUp` / `PgDn`    | jump 10 rows            | —                         |
| `Enter`            | open row / copy keybind | next match                |
| `Esc`              | clear query, then quit  | back to the list          |

Enter on a keybind row copies it to the clipboard, which is handy when you are
about to paste it into a config file.

## Configuration

Optional. `suprman --example-config` writes a commented file to
`~/.config/suprman/suprman.conf`; `--config FILE` points somewhere else. Every
key is optional and invalid values fall back to the defaults instead of
refusing to start.

```ini
[appearance]
# Surface colour and opacity (0.0 - 1.0, lower is glassier).
background = #0b0b10
opacity = 0.88
foreground = #e7e7ea
accent = #7aa2f7
muted = #82869b
border = #ffffff1f

width = 780
height = 520
radius = 14
padding = 14
row_height = 32

font = Sans 11
key_font = Monospace 10
title_font = Sans Bold 12

[behavior]
max_results = 200
man_sections = 1,2,3,4,5,6,7,8
man_width = 100
tldr = true
tldr_ttl_days = 7

# auto | linux | osx | windows | common
tldr_platform = auto

# Your own keybind dataset; empty means the bundled one.
vim_keys_file =

# auto | always | never
layer_shell = auto

case_sensitive = false
open_exact = true
```

Comments must live on their own line, since `#` also starts colour values.

Fonts are written as Pango descriptions (`Sans Bold 12`) and translated to CSS
`font-family` / `font-size` / `font-weight` on load.

The full annotated file lives at [docs/config.example.conf](docs/config.example.conf).

### Glass and blur

GTK cannot blur what is behind its own window — that is the compositor's job.
suprman gives you a translucent surface (`opacity`) and a layer-shell surface
named `suprman`; add the blur rule for your compositor:

- **Hyprland**: `layerrule = blur, suprman`
- **KDE Plasma 6.2+**: window rule → *Blur* for `suprman`
- **Sway**: `blur` in your `swayfx`/`sway` build with `for_window` rules
- **GNOME**: Blur My Shell → Applications → add `suprman`

Without a blur rule you still get the translucent, rounded, bordered popup — it
just is not frosted.

## Where the data comes from

**Keybinds** ship with suprman in
[`src/suprman/data/vim_keys.json`](src/suprman/data/vim_keys.json): 260 curated
core bindings across motions, operators, text objects, registers, macros,
search/replace, windows and tabs. It is plain JSON so you can extend it without
touching Python:

```json
{
  "sections": [{ "id": "mine", "title": "My mappings" }],
  "keys": [
    { "keys": "<leader>ff", "section": "mine", "description": "find files" }
  ]
}
```

Point `[behavior] vim_keys_file` at your own file to replace the bundled one.
Malformed entries are skipped with a warning instead of breaking the popup.

**Man pages** are indexed with `apropos` (falling back to a `manpath` scan),
cached in `~/.cache/suprman/man_index.json` for a day, and re-rendered per page
at `man_width` columns with hyphenation and justification off, so text reflows
cleanly instead of arriving pre-wrapped for an 80-column terminal. Delete the
cache file after installing packages if you want the list immediately.

**tldr pages** are looked up in this order: memory, then
`~/.cache/suprman/tldr/<cmd>.md`, then a local `tldr` client (preferring
`--raw`), then `raw.githubusercontent.com/tldr-pages/tldr`. Downloads are
cached as markdown for `tldr_ttl_days`, so the first lookup for a command needs
a network connection and every later one does not. `tldr = false` keeps the
reader local-only.

## Wayland and X11

GTK4 picks Wayland when it is there and falls back to X11 on its own. To force
XWayland: `GDK_BACKEND=x11 suprman`. Under X11 there is no layer shell, so
suprman is a normal undecorated window; use your WM's rules to hide the border
and centre it.

## Development

```sh
make run        # run from the checkout (PYTHONPATH=src)
make test       # unit tests, no display needed
make lint       # ruff, if installed
make check      # byte-compile everything
```

Layout:

```
src/suprman/
  app.py          GTK application, CLI, stylesheet loading
  window.py       the popup: search field, result list, mode switching
  reader.py       man/tldr view with in-page search
  config.py       INI parsing, validation, CSS rendering
  query.py        search bar → mode + term
  fuzzy.py        token-based ranking
  vimkeys.py      keybind dataset loader
  manpages.py     apropos index + man rendering
  tldr.py         tldr client / cache / download
  textsearch.py   plain-text search helpers
  layershell.py   optional wlr-layer-shell
  data/           style.css, vim_keys.json
tests/            unittest suite, headless
```

The pure modules (`fuzzy`, `query`, `config`, `textsearch`, `manpages`, `tldr`,
`vimkeys`) have no GTK imports, which is why the tests run anywhere; the GTK
layer is thin and keeps rendering decisions in `style.css`.

## Known limits

- No global hotkey registration, by design — the compositor owns that.
- No window shadow: it would be clipped by the window bounds. Blur and shadows
  belong to the compositor.
- Layer shell on KDE and GNOME depends on the compositor version; when it is
  unavailable suprman silently uses a normal window.
- tldr needs one network round trip per command the first time, unless a `tldr`
  client is installed.

## Licence

MIT. See [LICENSE](LICENSE).
