"""Optional ``wlr-layer-shell`` integration.

Layer shell is what makes a hotkey popup behave like a popup instead of a stray
toplevel: the compositor centres it, keeps it out of the window list, and lets
it take the keyboard exclusively.  It is genuinely optional -- GTK4 alone works
on Wayland and X11 -- so this module fails soft.

Importing ``Gtk4LayerShell`` is guarded because the typelib ships in a separate
package (``gtk4-layer-shell`` on Arch, ``gtk4-layer-shell`` on Fedora and
``libgtk4-layer-shell0`` on Debian/Ubuntu) that users may not have installed.
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

DEFAULT_NAMESPACE = "suprman"


def attach(window: Gtk.Window, namespace: str = DEFAULT_NAMESPACE, mode: str = "auto") -> bool:
    """Turn ``window`` into a layer-shell surface. Returns True on success.

    ``mode`` comes straight from ``[behavior] layer_shell``:

    * ``never`` -- do nothing, use a plain toplevel
    * ``auto``  -- use layer shell when the compositor supports it
    * ``always``-- like auto, but complain on stderr when it is unavailable
    """
    if mode == "never":
        return False

    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LayerShell
    except (ValueError, ImportError):
        if mode == "always":
            print(
                "suprman: layer shell requested but Gtk4LayerShell is not installed",
                file=sys.stderr,
            )
        return False

    try:
        if not LayerShell.is_supported():
            if mode == "always":
                print("suprman: compositor does not support wlr-layer-shell", file=sys.stderr)
            return False

        LayerShell.init_for_window(window)
        LayerShell.set_namespace(window, namespace)
        LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.EXCLUSIVE)
        # No anchors: the surface is centred. -1 keeps it above other layers.
        LayerShell.set_exclusive_zone(window, -1)
        return True
    except (TypeError, AttributeError, RuntimeError) as error:
        if mode == "always":
            print(f"suprman: could not enable layer shell: {error}", file=sys.stderr)
        return False
