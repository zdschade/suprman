#!/bin/sh
# Install suprman.
#
#   ./install.sh            # install into /usr/local (may need sudo)
#   ./install.sh --user     # install into ~/.local, no root required
#   ./install.sh --prefix=/opt/suprman
#
# Uninstall with: make uninstall PREFIX=<same prefix>

set -eu

PREFIX=/usr/local
USER_INSTALL=0

usage() {
    cat <<'EOF'
usage: ./install.sh [--user] [--prefix=DIR] [--uninstall] [--dry-run]

  --user            install under ~/.local instead of /usr/local
  --prefix=DIR      install under DIR
  --uninstall       remove a previous install
  --dry-run         print the commands instead of running them
EOF
}

UNINSTALL=0
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --user) USER_INSTALL=1 ;;
        --prefix=*) PREFIX="${arg#*=}" ;;
        --uninstall) UNINSTALL=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "install.sh: unknown option $arg" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$USER_INSTALL" -eq 1 ]; then
    PREFIX="$HOME/.local"
fi

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "+ $*"
    else
        "$@"
    fi
}

if [ "$UNINSTALL" -eq 1 ]; then
    run make uninstall PREFIX="$PREFIX"
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "install.sh: python3 is required" >&2
    exit 1
fi

# A dry run only prints the install commands; skip the dependency probe so the
# plan can be inspected anywhere.
if [ "$DRY_RUN" -eq 1 ]; then
    run make install PREFIX="$PREFIX"
    exit 0
fi

# Runtime dependencies: PyGObject plus the GTK4 typelib. Their package names
# differ per distro, so report the right one instead of a stack trace.
if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0")' >/dev/null 2>&1; then
    cat >&2 <<'EOF'
install.sh: PyGObject with GTK4 is missing.

  Fedora:          sudo dnf install python3-gobject gtk4
  Arch:            sudo pacman -S python-gobject gtk4
  Ubuntu/Debian:   sudo apt install python3-gi gir1.2-gtk-4.0

Optional (popup behaviour on Wayland):
  Fedora:          sudo dnf install gtk4-layer-shell
  Arch:            sudo pacman -S gtk4-layer-shell
  Ubuntu/Debian:   sudo apt install libgtk4-layer-shell0   (24.10+, PPA before)
EOF
    exit 1
fi

if [ "$USER_INSTALL" -eq 1 ] && ! command -v make >/dev/null 2>&1; then
    echo "install.sh: make is required" >&2
    exit 1
fi

run make install PREFIX="$PREFIX"

echo
echo "suprman installed. Try: ${PREFIX}/bin/suprman"
echo "Config file: ${XDG_CONFIG_HOME:-$HOME/.config}/suprman/suprman.conf"
echo "Generate a commented one with: ${PREFIX}/bin/suprman --example-config"
