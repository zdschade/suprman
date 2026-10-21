"""suprman -- a hotkey popup for Vim keybinds and man pages.

Modules are split by responsibility:

``app``        GTK application, CSS loading, CLI
``window``     the popup: search field, result list, mode switching
``reader``     the man/tldr view with its own in-page search
``config``     INI parsing, validation, stylesheet rendering
``query``      search bar -> mode + term
``fuzzy``      token based fuzzy ranking
``vimkeys``    the bundled keybind dataset
``manpages``   apropos indexing and man rendering
``tldr``       tldr pages via client, cache or network
``textsearch`` plain-text search helpers shared with the reader
``layershell`` optional wlr-layer-shell integration
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
